#!/usr/bin/env python3
"""Install ``qua-domain`` from the pinned ``QUD-Technologies/qua`` commit.

``qua-domain`` carries the four riwayat's exact scripts, coordinate indexes,
fonts, and the Hafs->target word projection. It is not on PyPI — it is a package
inside a private monorepo — so it is installed from an exact commit recorded in
``qua_shared/qua_domain_pin.py``.

    scripts/devenv/install_qua_domain.py            # install (or reinstall)
    scripts/devenv/install_qua_domain.py --check    # report, install nothing
    scripts/devenv/install_qua_domain.py --wheel-dir DIR   # build only

Credentials, in order:

1. ``PRIVATE_REPO_TOKEN`` — a read-only token for QUD Technologies private
   repositories. CI, the image build, and both Spaces use it.
2. ``QUA_DOMAIN_DEPLOY_KEY`` — the legacy repository-scoped SSH key.
3. Whatever git credential the developer already has for the monorepo (an
   ``ssh-agent`` identity, a ``gh auth`` helper). A contributor who works on the
   sibling repo needs nothing extra.

Without either, this **exits 0 having installed nothing**. The Inspector then
runs Hafs-only: ``services/reference/editions.available()`` is False and every
non-Hafs delivery fails loudly rather than rendering under Hafs coordinates. A
fork must stay buildable, so a missing private dependency is not a build error.

Why a sparse blobless fetch and not ``pip install git+ssh://…#subdirectory=``:
pip clones the WHOLE monorepo (engines, labs, model artefacts) to reach one
package. This fetches the one commit's tree and checks out one directory.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qua_shared.qua_domain_pin import (  # noqa: E402
    QUA_COMMIT,
    QUA_DIST_NAME,
    QUA_PACKAGE_PATH,
    QUA_SSH_URL,
)

DEPLOY_KEY_ENV = "QUA_DOMAIN_DEPLOY_KEY"
TOKEN_ENV = "PRIVATE_REPO_TOKEN"

#: ``StrictHostKeyChecking=no`` is safe here and unavoidable: the image build and
#: the HF Space runner have no known_hosts, the host is pinned to github.com in
#: the URL, and the payload is verified afterwards — the package's own digests
#: (``words_sha256``, font ``sha256``) are asserted at boot.
_SSH_OPTS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes"


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _write_key(text: str, directory: Path) -> Path:
    """Materialise the deploy key at 0600; OpenSSH refuses a loose-mode key."""
    key = directory / "qua_domain_key"
    # Two ways this file gets rejected with a bare "invalid format":
    #  - no trailing newline (a CI secret box strips it);
    #  - CRLF line endings — which is what write_text does on Windows unless
    #    newline="" disables translation. OpenSSH parses neither.
    key.write_text(text.replace("\r\n", "\n").rstrip("\n") + "\n", encoding="utf-8", newline="")
    key.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return key


def _git_env(key_path: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    if key_path is not None:
        env["GIT_SSH_COMMAND"] = f"ssh -i {key_path.as_posix()} {_SSH_OPTS}"
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = f"url.https://x-access-token:{token}@github.com/.insteadOf"
        env["GIT_CONFIG_VALUE_0"] = "git@github.com:"
    env["GIT_TERMINAL_PROMPT"] = "0"  # fail fast instead of blocking on a prompt
    return env


def installed_version() -> str | None:
    """The installed distribution's version, or ``None``."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(QUA_DIST_NAME)
    except PackageNotFoundError:
        return None


def fetch_package(dest: Path, key_path: Path | None) -> Path:
    """Sparse-checkout ``QUA_PACKAGE_PATH`` at ``QUA_COMMIT`` into ``dest``."""
    env = _git_env(key_path)
    _run(["git", "init", "--quiet", str(dest)], env=env)
    _run(["git", "remote", "add", "origin", QUA_SSH_URL], cwd=dest, env=env)
    _run(["git", "sparse-checkout", "set", "--cone", QUA_PACKAGE_PATH], cwd=dest, env=env)
    # --filter=blob:none: fetch the commit's tree, download file contents lazily
    # for the one checked-out directory. --depth 1: no history.
    _run(
        ["git", "fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", QUA_COMMIT],
        cwd=dest,
        env=env,
    )
    _run(["git", "checkout", "--quiet", "FETCH_HEAD"], cwd=dest, env=env)
    package = dest / QUA_PACKAGE_PATH
    if not (package / "pyproject.toml").is_file():
        raise SystemExit(f"{QUA_PACKAGE_PATH}/pyproject.toml missing at {QUA_COMMIT}")
    return package


def _credential_available() -> tuple[str | None, str]:
    """``(deploy key text | None, human reason)`` — the second is always printed."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return None, f"{TOKEN_ENV} is set"
    key = os.environ.get(DEPLOY_KEY_ENV, "").strip()
    if key:
        return key, f"{DEPLOY_KEY_ENV} is set"
    probe = subprocess.run(
        ["git", "ls-remote", "--exit-code", QUA_SSH_URL, "HEAD"],
        capture_output=True,
        env=_git_env(None),
    )
    if probe.returncode == 0:
        return None, "an existing git credential reaches the monorepo"
    return None, f"no {DEPLOY_KEY_ENV} and no git credential for {QUA_SSH_URL}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report state, install nothing")
    parser.add_argument("--wheel-dir", type=Path, help="build a wheel here instead of installing")
    args = parser.parse_args()

    current = installed_version()
    if args.check:
        print(f"pin           {QUA_COMMIT}")
        print(f"installed     {QUA_DIST_NAME} {current or '(absent)'}")
        _, reason = _credential_available()
        print(f"credential    {reason}")
        return 0

    key_text, reason = _credential_available()
    if key_text is None and reason.startswith("no "):
        print(f"qua-domain: skipped — {reason}.")
        print("  The Inspector will run Hafs-only; non-Hafs deliveries fail loudly.")
        return 0
    print(f"qua-domain: installing {QUA_COMMIT[:12]} ({reason})")

    with tempfile.TemporaryDirectory(prefix="qua-domain-") as tmp:
        tmpdir = Path(tmp)
        key_path = _write_key(key_text, tmpdir) if key_text else None
        package = fetch_package(tmpdir / "src", key_path)
        if args.wheel_dir:
            args.wheel_dir.mkdir(parents=True, exist_ok=True)
            _run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--wheel-dir",
                    str(args.wheel_dir),
                    str(package),
                ]
            )
            built = sorted(args.wheel_dir.glob("qua_domain-*.whl"))
            print(f"qua-domain: built {built[-1].name if built else '(nothing?)'}")
            return 0
        _run([sys.executable, "-m", "pip", "install", "--no-deps", str(package)])

    print(f"qua-domain: installed {installed_version()}")
    return 0


if __name__ == "__main__":
    # Windows leaves .git object files read-only; TemporaryDirectory's rmtree
    # then raises PermissionError AFTER a successful install.
    _real_rmtree = shutil.rmtree

    def _force_rmtree(path, ignore_errors=False, **kwargs):  # noqa: ANN001, ANN202
        def _chmod_retry(func, target, _exc):  # noqa: ANN001, ANN202
            Path(target).chmod(stat.S_IWRITE)
            func(target)

        return _real_rmtree(path, ignore_errors=ignore_errors, onerror=_chmod_retry)

    shutil.rmtree = _force_rmtree
    raise SystemExit(main())
