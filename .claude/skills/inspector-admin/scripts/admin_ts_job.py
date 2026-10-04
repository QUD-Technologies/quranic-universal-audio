"""TS-job lifecycle CLI — consolidates the launch + cancel + status + publish
operations into one entrypoint. Replaces inspector/scripts/launch_ts_job.py.

  admin_ts_job.py launch SLUG [--beams 30,2] [--workers N] [--dl-workers N]
                              [--flavor cpu-upgrade] [--timeout 2h] [--monitor]
  admin_ts_job.py cancel SLUG JOB_ID
  admin_ts_job.py status SLUG JOB_ID [--tail N]
  admin_ts_job.py record SLUG JOB_ID       # bucket-persisted record
  admin_ts_job.py history SLUG             # all linked job ids
  admin_ts_job.py publish SLUG --job-id JOB_ID    # alias of admin_publish.py
  admin_ts_job.py monitor SLUG JOB_ID      # poll until terminal, stream new logs

For settings / job format / publish path semantics open
docs/reference/timestamps-job.md.
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


def _beams(raw: str) -> list[int]:
    return [int(t) for t in raw.replace(" ", "").split(",") if t]


def _chapters(raw: str | None) -> list[int] | None:
    """Parse --chapters into surah numbers; None = whole reciter."""
    if not raw:
        return None
    return [int(t) for t in raw.replace(" ", "").split(",") if t]


def _print(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _do_launch(a, ctx) -> int:
    from qua_shared.schemas import TsJobSettings  # noqa: E402
    from services.admin import timestamps_jobs  # noqa: E402
    from services.state import state as state_service  # noqa: E402

    if state_service.get_row(a.slug) is None:
        print(f"unknown slug {a.slug}", file=sys.stderr); return 4
    existing = timestamps_jobs.running_job_for(a.slug)
    if existing:
        print(f"a job is already in-flight: {existing}", file=sys.stderr); return 5
    settings = TsJobSettings(
        beams=_beams(a.beams), workers=a.workers,
        download_workers=a.dl_workers, flavor=a.flavor, timeout=a.timeout,
        aligner_model=getattr(a, "model", None),
        chapters=_chapters(getattr(a, "chapters", None)),
    )
    _print(f"settings: {settings.model_dump(exclude_none=False)}")
    if a.dry_run:
        _print("DRY RUN — not launching"); return 0
    result = timestamps_jobs.launch(
        a.slug, settings=settings,
        webhook_base="https://qud-technologies-quranic-universal-audio.hf.space",
    )
    _print(f"launched job_id={result.get('job_id')}")
    _print(f"  URL: {result.get('url')}")
    bs.after_write_banner(a)
    if a.monitor:
        return _do_monitor_loop(result["job_id"], poll_secs=a.poll_secs)
    return 0


def _do_cancel(a, ctx) -> int:
    from services.admin import timestamps_jobs  # noqa: E402
    if a.dry_run:
        _print(f"DRY RUN — would cancel job {a.job_id}"); return 0
    res = timestamps_jobs.cancel_job(a.slug, a.job_id)
    _print(f"cancel: {res}")
    bs.after_write_banner(a)
    return 0 if res.get("canceled") else 1


def _do_status(a, ctx) -> int:
    from services.admin import timestamps_jobs  # noqa: E402
    info = timestamps_jobs.job_status(a.slug, a.job_id, log_tail=a.tail)
    _print(f"status: {info['status']}  url: {info.get('url')}")
    for line in info.get("logs", [])[-a.tail:]:
        print(f"   {line}")
    return 0


def _do_record(a, ctx) -> int:
    from services.admin import timestamps_jobs  # noqa: E402
    import json as _json
    rec = timestamps_jobs.read_job_record(a.slug, a.job_id)
    print(_json.dumps(rec, indent=2, ensure_ascii=False) if rec else "(no record)")
    return 0


def _do_history(a, ctx) -> int:
    from services.admin import timestamps_jobs  # noqa: E402
    rows = timestamps_jobs.list_job_records(a.slug)
    if not rows:
        print("(no jobs)")
        return 0
    for r in rows:
        print(f"  {r.get('job_id'):42s}  {r.get('status','?'):>10s}  "
              f"started={bs.fmt_iso(r.get('started_at'))}  "
              f"ended={bs.fmt_iso(r.get('ended_at'))}")
    return 0


def _do_publish(a, ctx) -> int:
    from services.admin import timestamps_jobs  # noqa: E402
    if a.dry_run:
        _print(f"DRY RUN — would complete_timestamps_job({a.slug!r}, {a.job_id!r})")
        return 0
    res = timestamps_jobs.complete_timestamps_job(a.slug, a.job_id)
    _print(f"publish: {res}")
    bs.after_write_banner(a)
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
            so = getattr(info, "status", None)
            stage = (getattr(so, "stage", None) or so or "").lower()
        except Exception as exc:
            stage = ""; _print(f"inspect_job err: {exc}")
        if stage and stage != last_status:
            _print(f"status → {stage}"); last_status = stage
        try:
            lines = list(fetch_job_logs(job_id=job_id))
        except Exception:
            lines = []
        for line in lines[seen:]:
            print(f"   | {line}", flush=True)
        seen = len(lines)
        if stage in TERMINAL:
            _print(f"terminal: {stage}")
            return 0 if stage in ("succeeded", "completed") else 1
        time.sleep(poll_secs)


def _common_writer_args(p: argparse.ArgumentParser) -> None:
    bs.add_common_args(p)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("launch", help="launch a TS job for SLUG")
    pl.add_argument("slug"); pl.add_argument("--beams", default="50,2")
    pl.add_argument("--workers", type=int, default=None)
    pl.add_argument("--dl-workers", type=int, default=None)
    pl.add_argument("--flavor", default=None); pl.add_argument("--timeout", default=None)
    pl.add_argument("--model", default=None, help="aligner model catalog id (default: store default)")
    pl.add_argument("--chapters", default=None,
                    help="comma-separated surah numbers to re-align (default: whole reciter); "
                         "untouched chapters keep their existing shards")
    pl.add_argument("--monitor", action="store_true")
    pl.add_argument("--poll-secs", type=int, default=15)
    _common_writer_args(pl)

    pc = sub.add_parser("cancel", help="cancel an in-flight TS job")
    pc.add_argument("slug"); pc.add_argument("job_id"); _common_writer_args(pc)

    ps = sub.add_parser("status", help="live status + log tail")
    ps.add_argument("slug"); ps.add_argument("job_id")
    ps.add_argument("--tail", type=int, default=50)
    bs.add_common_args(ps, mutating=False)

    pr = sub.add_parser("record", help="durable bucket-persisted record")
    pr.add_argument("slug"); pr.add_argument("job_id")
    bs.add_common_args(pr, mutating=False)

    ph = sub.add_parser("history", help="all linked job records for SLUG")
    ph.add_argument("slug"); bs.add_common_args(ph, mutating=False)

    pp = sub.add_parser("publish", help="complete_timestamps_job — release the reciter")
    pp.add_argument("slug"); pp.add_argument("--job-id", required=True)
    _common_writer_args(pp)

    pm = sub.add_parser("monitor", help="poll until terminal, stream new log lines")
    pm.add_argument("slug"); pm.add_argument("job_id")
    pm.add_argument("--poll-secs", type=int, default=15)
    bs.add_common_args(pm, mutating=False)

    a = p.parse_args()
    # Launch links its Space run id into SQLite; status may complete a terminal
    # run and publish/refresh the reciter. Both need the guarded single-writer
    # window in production, just like cancel/publish.
    mutates = a.cmd in ("launch", "cancel", "status", "publish")
    safe_write = mutates
    return bs.run(
        a,
        lambda ctx: {"launch": _do_launch, "cancel": _do_cancel, "status": _do_status,
                     "record": _do_record, "history": _do_history,
                     "publish": _do_publish, "monitor": _do_monitor}[a.cmd](a, ctx),
        need_actor=mutates, mutates=mutates, safe_write=safe_write,
    )


if __name__ == "__main__":
    raise SystemExit(main())
