"""HF dataset publish job — launch / monitor for one reciter.

  admin_hf_publish.py launch SLUG [--monitor] [--poll-secs N]
  admin_hf_publish.py monitor JOB_ID [--poll-secs N]

Mirrors ``admin_ts_job.py`` for the ``hf_publish`` kind — the launch path is
``services/admin/jobs/hf_publish.launch`` (single-flight against the slug,
stages job code to the aligner-bucket, runs the prebuilt ts-job image). On
terminal success the prod Space's poll worker calls ``complete()`` within
~120 s; the webhook fires first when ``INSPECTOR_WEBHOOK_SECRET`` is in the
launcher's env.

For settings and the per-track release schema, open
``docs/reference/dataset-and-releases.md``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _bootstrap as bs  # noqa: E402

TERMINAL = {"succeeded", "completed", "failed", "error", "errored",
            "timed-out", "timeout", "stopped", "canceled", "cancelled", "deleted"}


def _print(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _do_launch(a, ctx) -> int:
    from services.admin.jobs import hf_publish  # noqa: E402
    from services.admin.jobs import base as jobs_base  # noqa: E402
    from services.state import state as state_service  # noqa: E402

    if state_service.get_row(a.slug) is None:
        print(f"unknown slug {a.slug}", file=sys.stderr); return 4
    busy = jobs_base.running_job_for(slug=a.slug)
    if busy is not None:
        print(f"a job is already in-flight: {busy}", file=sys.stderr); return 5
    if a.dry_run:
        _print(f"DRY RUN — would launch hf_publish for {a.slug}"); return 0
    result = hf_publish.launch(
        a.slug,
        webhook_base="https://qud-technologies-quranic-universal-audio.hf.space",
    )
    _print(f"launched job_id={result.get('job_id')}")
    _print(f"  URL: {result.get('url')}")
    bs.after_write_banner(a)
    if a.monitor:
        return _do_monitor_loop(result["job_id"], poll_secs=a.poll_secs)
    return 0


def _do_monitor(a, ctx) -> int:
    return _do_monitor_loop(a.job_id, poll_secs=a.poll_secs)


def _do_monitor_loop(job_id: str, *, poll_secs: int = 15) -> int:
    from huggingface_hub import fetch_job_logs, inspect_job  # noqa: E402
    seen = 0
    last_status = None
    while True:
        try:
            info = inspect_job(job_id=job_id)
        except Exception as exc:
            _print(f"inspect_job error: {exc}"); time.sleep(poll_secs); continue
        status = (getattr(getattr(info, "status", None), "stage", "") or "").lower()
        if status != last_status:
            _print(f"status: {status}"); last_status = status
        try:
            lines = list(fetch_job_logs(job_id=job_id))
        except Exception:
            lines = []
        for line in lines[seen:]:
            print(f"   {line}", flush=True)
        seen = len(lines)
        if status in TERMINAL:
            _print(f"terminal: {status}"); return 0 if status in {"succeeded", "completed"} else 1
        time.sleep(poll_secs)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("launch", help="launch an hf_publish job for SLUG")
    pl.add_argument("slug")
    pl.add_argument("--monitor", action="store_true",
                    help="block + stream logs until the job hits a terminal stage")
    pl.add_argument("--poll-secs", type=int, default=15)
    bs.add_common_args(pl, mutating=True)

    pm = sub.add_parser("monitor", help="stream logs for an existing job until terminal")
    pm.add_argument("job_id")
    pm.add_argument("--poll-secs", type=int, default=15)
    bs.add_common_args(pm, mutating=False)

    a = p.parse_args()
    # launch only queues an HF job (no in-process DB write; --monitor needs the
    # Space up) → mutates for the --yes-prod gate, but safe_write=False (no pause).
    mutates = a.cmd == "launch"
    return bs.run(
        a,
        lambda ctx: {"launch": _do_launch, "monitor": _do_monitor}[a.cmd](a, ctx),
        need_actor=False, mutates=mutates, safe_write=False, need_db=False,
    )


if __name__ == "__main__":
    sys.exit(main())
