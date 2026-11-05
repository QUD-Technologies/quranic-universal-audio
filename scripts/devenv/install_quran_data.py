#!/usr/bin/env python3
"""Install ``talqeen-quran-data`` and fetch the Quran data pack at the pinned revision.

The Hafs reference data (Digital Khatt v2 words, ayah-end markers, surah metadata) is read
from the Talqeen pack through its SDK. Both come from the private
``QUD-Technologies/talqeen-ai-data`` repository at ``qua_shared/quran_data_pin.py``'s
``TALQEEN_DATA_REF``; neither is committed here.

    scripts/devenv/install_quran_data.py                  # install SDK + fetch pack to data/
    scripts/devenv/install_quran_data.py --check          # report, change nothing
    scripts/devenv/install_quran_data.py --wheel-dir DIR  # build the SDK wheel, do not install
    scripts/devenv/install_quran_data.py --pack-dir DIR   # put the pack somewhere else

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
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qua_shared.quran_data_pin import (  # noqa: E402
    PACK_NAME,
    PACK_PATH,
    PACK_SHA256,
    SDK_DIST_NAME,
    SDK_PATH,
    SDK_VERSION,
    TALQEEN_DATA_REF,
    TALQEEN_HTTPS_URL,
)

TOKEN_ENVS = ("TALQEEN_DATA_READ_TOKEN", "GH_TOKEN")
FORBIDDEN_HINT = (
    f"The token was refused: it needs read access (contents: read) to {TALQEEN_HTTPS_URL}. "
    "In CI and the image build that is the TALQEEN_DATA_READ_TOKEN secret."
)
DEFAULT_PACK_DIR = ROOT / "data"


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


def _report(pack_dir: Path) -> None:
    print(f"pin           {TALQEEN_DATA_REF}")
    print(f"sdk           {SDK_DIST_NAME} {installed_version() or '(absent)'} (pin {SDK_VERSION})")
    print(f"pack          {pack_dir / PACK_NAME}: {pack_state(pack_dir)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report state, change nothing")
    parser.add_argument("--wheel-dir", type=Path, help="build the SDK wheel here, do not install")
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK_DIR)
    parser.add_argument("--force", action="store_true", help="reinstall even when up to date")
    args = parser.parse_args()

    if args.check:
        _report(args.pack_dir)
        return 0
    up_to_date = installed_version() == SDK_VERSION and pack_state(args.pack_dir) == "ok"
    if up_to_date and not args.wheel_dir and not args.force:
        print(f"talqeen-quran-data: {SDK_VERSION} and {PACK_NAME} already at the pin")
        return 0

    token, source = _token()
    print(f"talqeen-quran-data: fetching {TALQEEN_DATA_REF[:12]} ({source})")
    with tempfile.TemporaryDirectory(prefix="talqeen-data-") as tmp:
        checkout = fetch_revision(Path(tmp) / "src", token)
        pack = install_pack(checkout, args.pack_dir)
        print(f"talqeen-quran-data: pack -> {pack}")
        sdk = checkout / SDK_PATH
        if args.wheel_dir:
            args.wheel_dir.mkdir(parents=True, exist_ok=True)
            _pip(["wheel", "--no-deps", "--wheel-dir", str(args.wheel_dir), str(sdk)])
            return 0
        _pip(["install", "--quiet", str(sdk)])
    print(f"talqeen-quran-data: installed {installed_version()}")
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
