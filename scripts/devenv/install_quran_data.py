#!/usr/bin/env python3
"""Install ``talqeen-quran-data`` and fetch the Quran data pack, fonts and TS SDK at the pin.

The Hafs reference data (Digital Khatt v2 words, ayah-end markers, surah metadata) is read
from the Talqeen pack through its SDK. Both come from the private
``QUD-Technologies/talqeen-ai-data`` repository at ``qua_shared/quran_data_pin.py``'s
``TALQEEN_DATA_REF``; neither is committed here. The frontend's Mushaf view needs two more of
that repository's release assets: the TypeScript SDK tarball (``@qud-technologies/quran-data``,
installed by ``inspector/frontend/package.json`` from ``inspector/frontend/.quran-data/``) and
the fonts the pack's ``fonts()`` names (``data/fonts/``, served by ``/api/static/quran-data/``).
Every file is checked against its pinned sha256.

    scripts/devenv/install_quran_data.py                  # all: SDK, pack, fonts, TS SDK
    scripts/devenv/install_quran_data.py --check          # report, change nothing
    scripts/devenv/install_quran_data.py --ts-only        # just the TS SDK (before `npm ci`)
    scripts/devenv/install_quran_data.py --wheel-dir DIR  # build the SDK wheel, do not install
    scripts/devenv/install_quran_data.py --pack-dir DIR   # put the pack (and fonts/) elsewhere
    scripts/devenv/install_quran_data.py --ts-dir DIR     # put the TS SDK tarball elsewhere

Credentials, in order: ``TALQEEN_DATA_READ_TOKEN`` (CI, image build), ``GH_TOKEN``, then the
token of the existing ``gh`` login. The token is passed to git through the environment and
never printed. Unlike the optional ``qua-domain`` edition data this is required: the
Inspector has no Hafs text without it, so a missing credential is an error.

Why a sparse blobless fetch: the data repo also carries timing archives and sources; this
fetches one commit's tree and checks out the SDK directory and the one pack file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qua_shared.quran_data_pin import (  # noqa: E402
    DATA_RELEASE,
    FONT_SHA256,
    FONTS_DIR_NAME,
    PACK_NAME,
    PACK_PATH,
    PACK_SHA256,
    SDK_DIST_NAME,
    SDK_PATH,
    SDK_VERSION,
    TALQEEN_DATA_REF,
    TALQEEN_HTTPS_URL,
    TALQEEN_REPO,
    TS_SDK_FILE,
    TS_SDK_RELEASE,
    TS_SDK_SHA256,
)

TOKEN_ENVS = ("TALQEEN_DATA_READ_TOKEN", "GH_TOKEN")
FORBIDDEN_HINT = (
    f"The token was refused: it needs read access (contents: read) to {TALQEEN_HTTPS_URL}. "
    "In CI and the image build that is the TALQEEN_DATA_READ_TOKEN secret."
)
DEFAULT_PACK_DIR = ROOT / "data"
DEFAULT_TS_DIR = ROOT / "inspector" / "frontend" / ".quran-data"
API = "https://api.github.com"
HTTP_TIMEOUT_S = 120
REDIRECTS = (301, 302, 303, 307, 308)


def _token() -> tuple[str, str]:
    """``(token, where it came from)``; exits when no credential is available."""
    for name in TOKEN_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        out = None
    if out is not None and out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip(), "gh auth token"
    raise SystemExit(
        f"talqeen-quran-data: no credential for {TALQEEN_HTTPS_URL}. "
        f"Set {' or '.join(TOKEN_ENVS)}, or run `gh auth login`."
    )


def _git_env(token: str) -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = f"url.https://x-access-token:{token}@github.com/.insteadOf"
    env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(args: list[str], cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        # git echoes the remote URL on failure; strip the credential before reporting.
        token = env["GIT_CONFIG_KEY_0"].split("x-access-token:", 1)[1].split("@", 1)[0]
        message = (result.stderr or result.stdout).replace(token, "***").strip()
        if "error: 403" in message:
            message += f"\n{FORBIDDEN_HINT}"
        raise SystemExit(f"git {args[0]} failed: {message}")


def fetch_revision(dest: Path, token: str) -> Path:
    """Sparse-checkout the SDK and the pack at ``TALQEEN_DATA_REF`` into ``dest``."""
    env = _git_env(token)
    dest.mkdir(parents=True, exist_ok=True)
    _git(["init", "--quiet"], dest, env)
    _git(["remote", "add", "origin", TALQEEN_HTTPS_URL], dest, env)
    _git(["sparse-checkout", "set", "--no-cone", f"/{SDK_PATH}/", f"/{PACK_PATH}"], dest, env)
    _git(
        ["fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", TALQEEN_DATA_REF],
        dest,
        env,
    )
    _git(["checkout", "--quiet", "FETCH_HEAD"], dest, env)
    if not (dest / SDK_PATH / "pyproject.toml").is_file():
        raise SystemExit(f"{SDK_PATH}/pyproject.toml missing at {TALQEEN_DATA_REF}")
    return dest


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_pack(checkout: Path, pack_dir: Path) -> Path:
    """Verify the pack against the pin and copy it into ``pack_dir``."""
    source = checkout / PACK_PATH
    digest = sha256_file(source)
    if digest != PACK_SHA256:
        raise SystemExit(f"{PACK_PATH} sha256 {digest} != pinned {PACK_SHA256}")
    pack_dir.mkdir(parents=True, exist_ok=True)
    target = pack_dir / PACK_NAME
    shutil.copyfile(source, target)
    return target


# ---------------------------------------------------------------------------
# Release assets (TS SDK tarball, fonts) over the GitHub REST API
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface the redirect: the asset's storage URL must be fetched without our token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, PLR0913
        return None


def _get(url: str, token: str, accept: str) -> bytes:
    headers = {"Accept": accept, "User-Agent": "qua-install-quran-data", "Authorization": f"Bearer {token}"}
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=HTTP_TIMEOUT_S) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location") if exc.code in REDIRECTS else None
        if location:
            with urllib.request.urlopen(location, timeout=HTTP_TIMEOUT_S) as r:  # noqa: S310
                return r.read()
        hint = f"\n{FORBIDDEN_HINT}" if exc.code in (401, 403, 404) else ""
        raise SystemExit(f"GET {url}: HTTP {exc.code}{hint}") from None


def release_asset(tag: str, name: str, token: str) -> bytes:
    """Bytes of release ``tag``'s asset ``name`` in the data repo."""
    meta = json.loads(_get(f"{API}/repos/{TALQEEN_REPO}/releases/tags/{tag}", token, "application/vnd.github+json"))
    for asset in meta.get("assets", []):
        if asset.get("name") == name:
            return _get(asset["url"], token, "application/octet-stream")
    raise SystemExit(f"release {tag} of {TALQEEN_REPO} has no asset {name}")


def _checked(name: str, data: bytes, want: str) -> bytes:
    digest = hashlib.sha256(data).hexdigest()
    if digest != want:
        raise SystemExit(f"{name}: sha256 {digest} != pinned {want}")
    return data


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def fetch_ts_sdk(ts_dir: Path, token: str) -> Path:
    """The TypeScript SDK tarball the frontend's package.json installs from ``ts_dir``."""
    data = _checked(TS_SDK_FILE, release_asset(TS_SDK_RELEASE, TS_SDK_FILE, token), TS_SDK_SHA256)
    return _write(ts_dir / TS_SDK_FILE, data)


def _asset_location(url: str) -> tuple[str, str]:
    """``(tag, asset name)`` of a ``.../releases/download/<tag>/<name>`` URL."""
    marker = "/releases/download/"
    if marker not in url:
        raise SystemExit(f"manifest URL is not a release asset: {url}")
    tag, _, name = url.split(marker, 1)[1].partition("/")
    return tag, name


def fetch_fonts(fonts_dir: Path, token: str) -> list[Path]:
    """The pinned fonts, located through ``DATA_RELEASE``'s ``manifest.json``."""
    manifest = json.loads(release_asset(DATA_RELEASE, "manifest.json", token))
    out = []
    for name, sha in sorted(FONT_SHA256.items()):
        entry = manifest.get("files", {}).get(name)
        if not entry:
            raise SystemExit(f"{DATA_RELEASE} manifest.json does not list {name}")
        if entry.get("sha256") != sha:
            raise SystemExit(f"{name}: manifest sha256 {entry.get('sha256')} != pinned {sha}")
        tag, asset = _asset_location(entry["url"])
        out.append(_write(fonts_dir / name, _checked(name, release_asset(tag, asset, token), sha)))
    return out


def ts_state(ts_dir: Path) -> str:
    tgz = ts_dir / TS_SDK_FILE
    if not tgz.is_file():
        return "absent"
    return "ok" if sha256_file(tgz) == TS_SDK_SHA256 else "sha256 mismatch"


def fonts_state(fonts_dir: Path) -> str:
    bad = sorted(n for n, sha in FONT_SHA256.items() if not (fonts_dir / n).is_file() or sha256_file(fonts_dir / n) != sha)
    return "ok" if not bad else f"missing or wrong: {', '.join(bad)}"


def installed_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(SDK_DIST_NAME)
    except PackageNotFoundError:
        return None


def pack_state(pack_dir: Path) -> str:
    pack = pack_dir / PACK_NAME
    if not pack.is_file():
        return "absent"
    return "ok" if sha256_file(pack) == PACK_SHA256 else "sha256 mismatch"


def _pip(args: list[str]) -> None:
    subprocess.run([sys.executable, "-m", "pip", *args], check=True)


def _report(pack_dir: Path, ts_dir: Path) -> None:
    print(f"pin           {TALQEEN_DATA_REF}")
    print(f"sdk           {SDK_DIST_NAME} {installed_version() or '(absent)'} (pin {SDK_VERSION})")
    print(f"pack          {pack_dir / PACK_NAME}: {pack_state(pack_dir)}")
    print(f"fonts         {pack_dir / FONTS_DIR_NAME}: {fonts_state(pack_dir / FONTS_DIR_NAME)}")
    print(f"ts sdk        {ts_dir / TS_SDK_FILE}: {ts_state(ts_dir)}")


def _install_python(args: argparse.Namespace, token: str) -> None:
    """Sparse-fetch the revision: the pack, then the Python SDK (installed or built as a wheel)."""
    with tempfile.TemporaryDirectory(prefix="talqeen-data-") as tmp:
        checkout = fetch_revision(Path(tmp) / "src", token)
        pack = install_pack(checkout, args.pack_dir)
        print(f"talqeen-quran-data: pack -> {pack}")
        sdk = checkout / SDK_PATH
        if args.wheel_dir:
            args.wheel_dir.mkdir(parents=True, exist_ok=True)
            _pip(["wheel", "--no-deps", "--wheel-dir", str(args.wheel_dir), str(sdk)])
            return
        _pip(["install", "--quiet", str(sdk)])
    print(f"talqeen-quran-data: installed {installed_version()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report state, change nothing")
    parser.add_argument("--wheel-dir", type=Path, help="build the SDK wheel here, do not install")
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK_DIR)
    parser.add_argument("--ts-dir", type=Path, default=DEFAULT_TS_DIR)
    parser.add_argument("--ts-only", action="store_true", help="fetch only the TS SDK tarball")
    parser.add_argument("--force", action="store_true", help="reinstall even when up to date")
    args = parser.parse_args()
    fonts_dir = args.pack_dir / FONTS_DIR_NAME

    if args.check:
        _report(args.pack_dir, args.ts_dir)
        return 0
    python_ok = installed_version() == SDK_VERSION and pack_state(args.pack_dir) == "ok"
    need_python = not args.ts_only and (args.force or bool(args.wheel_dir) or not python_ok)
    need_fonts = not args.ts_only and (args.force or fonts_state(fonts_dir) != "ok")
    need_ts = args.force or ts_state(args.ts_dir) != "ok"
    if not (need_python or need_fonts or need_ts):
        print(f"talqeen-quran-data: {SDK_VERSION}, {PACK_NAME}, fonts and {TS_SDK_FILE} already at the pin")
        return 0

    token, source = _token()
    print(f"talqeen-quran-data: fetching {TALQEEN_DATA_REF[:12]} ({source})")
    if need_ts:
        print(f"talqeen-quran-data: ts sdk -> {fetch_ts_sdk(args.ts_dir, token)}")
    if need_fonts:
        for path in fetch_fonts(fonts_dir, token):
            print(f"talqeen-quran-data: font -> {path}")
    if need_python:
        _install_python(args, token)
    return 0


def _force_rmtree(path, ignore_errors=False, **kwargs):  # noqa: ANN001, ANN202
    """Windows leaves .git object files read-only, so the temp dir's rmtree would raise."""

    def _chmod_retry(func, target, _exc):  # noqa: ANN001, ANN202
        Path(target).chmod(stat.S_IWRITE)
        func(target)

    return _REAL_RMTREE(path, ignore_errors=ignore_errors, onerror=_chmod_retry)


_REAL_RMTREE = shutil.rmtree

if __name__ == "__main__":
    shutil.rmtree = _force_rmtree
    raise SystemExit(main())
