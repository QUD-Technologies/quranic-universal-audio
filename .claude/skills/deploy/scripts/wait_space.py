#!/usr/bin/env python3
"""Poll an Inspector HF Space until it has (re)built and is RUNNING + healthy.

Built to run as a BACKGROUND job after a deploy: it blocks, logging each
runtime-stage change, and exits 0 once the Space reaches RUNNING *and*
``/healthz`` returns 200 — not merely when the upload finished. It exits
non-zero on a terminal failure (BUILD_ERROR / RUNTIME_ERROR / a paused or
stopped Space) or on timeout, so the launching Bash job notifies the agent of
the real outcome.

Space ids mirror scripts/deploy/upload_inspector.py::SPACE_REPOS.
Auth: HF_TOKEN / INSPECTOR_HF_TOKEN from the env, else loaded from the current
checkout's ``.env`` via qua_shared._env.load_repo_env.

    python wait_space.py [dev|prod]   # default: dev
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

SPACE_REPOS = {
    "dev": "QUD-Technologies/quranic-inspector-dev",
    "prod": "QUD-Technologies/quranic-universal-audio",
}
POLL_SECONDS = 10
TIMEOUT_SECONDS = 15 * 60
# A RUNNING+healthy Space is only accepted once we've seen the rebuild start
# (stage left RUNNING) — OR after this grace window, covering a near-instant
# no-op rebuild that's already RUNNING by our first poll.
BUILD_GRACE_SECONDS = 90
TERMINAL_BAD = {"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "PAUSED", "STOPPED", "DELETING"}


def _load_token() -> str | None:
    tok = os.environ.get("INSPECTOR_HF_TOKEN") or os.environ.get("HF_TOKEN")
    if tok:
        return tok
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
        sys.path.insert(0, root)
        from qua_shared._env import load_repo_env

        load_repo_env()
    except Exception:
        pass
    return os.environ.get("INSPECTOR_HF_TOKEN") or os.environ.get("HF_TOKEN")


def _healthz_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    env = (argv[0] if argv else "dev").lower()
    if env not in SPACE_REPOS:
        print(f"usage: wait_space.py [dev|prod] (got '{env}')", file=sys.stderr)
        return 2

    repo_id = SPACE_REPOS[env]
    healthz = f"https://{repo_id.replace('/', '-')}.hf.space/healthz"

    from huggingface_hub import HfApi

    api = HfApi(token=_load_token())

    print(f"==> Waiting for {env} Space {repo_id} to (re)build and run", flush=True)
    print(f"    healthz: {healthz}", flush=True)
    start = time.monotonic()
    last_stage = None
    saw_build = False
    while time.monotonic() - start < TIMEOUT_SECONDS:
        elapsed = int(time.monotonic() - start)
        try:
            stage = str(api.get_space_runtime(repo_id).stage)
        except Exception as e:
            stage = f"UNKNOWN({type(e).__name__})"
        if stage != last_stage:
            print(f"    [{elapsed:4d}s] stage={stage}", flush=True)
            last_stage = stage
        up = stage.upper()
        if up != "RUNNING":
            saw_build = True
        if up in TERMINAL_BAD:
            print(f"!! {repo_id} entered {stage} — deploy failed.", flush=True)
            return 1
        if up == "RUNNING" and (saw_build or elapsed >= BUILD_GRACE_SECONDS) and _healthz_ok(healthz):
            print(f"OK: {repo_id} is RUNNING and /healthz returned 200 ({elapsed}s).", flush=True)
            return 0
        time.sleep(POLL_SECONDS)

    print(f"!! Timed out after {TIMEOUT_SECONDS // 60} min waiting for {repo_id}.", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
