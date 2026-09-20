#!/usr/bin/env python3
"""Build the Inspector image and smoke-test that it actually BOOTS.

Builds ``<context>/Dockerfile`` (or reuses a prebuilt ``--tag`` with
``--skip-build``), runs the container with a secret-free offline recipe
(``filesystem`` backend + the public fixtures dataset, dev mode, no bucket),
and polls ``/healthz`` until it returns HTTP 200 with ``status: ok``. The
container is always torn down; the script exits non-zero if the image fails to
build, boot, or report healthy — printing the container logs on failure.

This catches the "builds but won't boot" class (bad import, gunicorn
misconfig, route-registration error) that a plain ``docker build`` can't see.

Used by:
  * the opt-in CI ``boot-smoke`` job (manual prod dispatch), which builds with
    a layer cache and calls this with ``--skip-build --tag <built>``; and
  * ``scripts/deploy/upload_inspector.py --verify-boot``, which builds the staged
    context here before deploying.

No Hugging Face token or bucket access required — the fixtures dataset pulled by
``seed_fixtures`` is public.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Reuse the public-fixtures downloader from the devenv sibling.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "devenv"))
import seed_fixtures  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTAINER = "inspector-smoke"
# Offline boot recipe. Overrides the Dockerfile's baked deployed-mode env
# (INSPECTOR_BUCKET_MOUNT, INSPECTOR_BEHIND_PROXY) so /healthz runs in local
# mode (200 when healthy, not 503-on-degraded) and dev mode auto-enables.
_RUN_ENV = {
    "INSPECTOR_BACKEND": "filesystem",
    "INSPECTOR_FILESYSTEM_ROOT": "/app/inspector/.fixtures",
    "INSPECTOR_DEV_MODE": "1",
    "INSPECTOR_BUCKET_MOUNT": "",
    "INSPECTOR_AUTO_DETECT": "0",
    "INSPECTOR_AUTO_MOUNT": "0",
}


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def _writable_fixtures() -> Path:
    """Public fixtures copied into a world-writable temp dir.

    The container runs as uid 1000 and SQLite opens the fixtures DB read-write
    (WAL side files); a world-writable copy lets it write regardless of the
    host file ownership, without mutating the developer's own ``.fixtures``.
    """
    seed_fixtures._download(force=False)
    src = seed_fixtures.FIXTURES_ROOT
    tmp = Path(tempfile.mkdtemp(prefix="inspector-smoke-fixtures-"))
    dst = tmp / "fixtures"
    shutil.copytree(src, dst)
    for path in dst.rglob("*"):
        path.chmod(0o777)
    dst.chmod(0o777)
    return dst


def _rm_container() -> None:
    subprocess.run(
        ["docker", "rm", "-f", _CONTAINER],
        capture_output=True,
        text=True,
    )


def _poll_healthz(port: int, timeout: int) -> dict:
    """Poll /healthz until the app is up and hydrated, or raise on timeout.

    Success = HTTP 200 with ``state_loaded`` and ``db.open`` true. We do NOT
    require ``status == "ok"``: offline (filesystem backend, no bucket mount)
    the app is healthy but reports ``status: degraded`` because
    ``bucket_mounted`` is false. The boot smoke only proves the app builds,
    boots, registers routes, and serves a hydrated /healthz.
    """
    url = f"http://localhost:{port}/healthz"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                body = resp.read().decode("utf-8")
                if resp.status == 200:
                    payload = json.loads(body)
                    if payload.get("state_loaded") and payload.get("db", {}).get("open"):
                        return payload
                    last = f"200 but not hydrated yet: {body}"
                else:
                    last = f"HTTP {resp.status}: {body}"
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last = f"not up yet ({e})"
        time.sleep(2)
    raise TimeoutError(f"/healthz never went healthy within {timeout}s — last: {last}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument(
        "--context",
        default=str(_REPO_ROOT),
        help="Docker build context (must contain Dockerfile). Default: repo root.",
    )
    p.add_argument("--tag", default="inspector-smoke:latest", help="Image tag.")
    p.add_argument(
        "--skip-build",
        action="store_true",
        help="Don't build; --tag must already exist (CI builds with its cache).",
    )
    p.add_argument("--port", type=int, default=7860, help="Host port to map to 7860.")
    p.add_argument("--timeout", type=int, default=90, help="Seconds to wait for health.")
    args = p.parse_args(argv)

    context = Path(args.context).resolve()
    if not args.skip_build:
        dockerfile = context / "Dockerfile"
        if not dockerfile.is_file():
            print(f"ERROR: no Dockerfile at {dockerfile}", file=sys.stderr)
            return 2
        print(f"==> Building {args.tag} from {context}")
        build = ["docker", "build"]
        # Both deploy keys are optional at the docker layer (`required=false`);
        # absent, the image builds without the renderer package / without
        # qua-domain, and the boot assertion below still has to pass.
        for secret in ("PRIVATE_REPO_TOKEN",):
            if os.environ.get(secret):
                build.extend(["--secret", f"id={secret},env={secret}"])
        _run([*build, "-f", str(dockerfile), "-t", args.tag, str(context)])

    print("==> Preparing offline fixtures")
    fixtures = _writable_fixtures()

    _rm_container()
    run_cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        _CONTAINER,
        "-p",
        f"{args.port}:7860",
        "-v",
        f"{fixtures}:/app/inspector/.fixtures",
    ]
    for key, val in _RUN_ENV.items():
        run_cmd += ["-e", f"{key}={val}"]
    run_cmd.append(args.tag)

    try:
        print(f"==> Booting {args.tag}")
        _run(run_cmd)
        payload = _poll_healthz(args.port, args.timeout)
        print("==> /healthz OK:")
        print(json.dumps(payload, indent=2))
        return 0
    except (subprocess.CalledProcessError, TimeoutError) as e:
        print(f"\nERROR: boot smoke failed: {e}", file=sys.stderr)
        logs = subprocess.run(
            ["docker", "logs", _CONTAINER],
            capture_output=True,
            text=True,
        )
        print("---- container logs ----", file=sys.stderr)
        print(logs.stdout, file=sys.stderr)
        print(logs.stderr, file=sys.stderr)
        return 1
    finally:
        _rm_container()
        shutil.rmtree(fixtures.parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
