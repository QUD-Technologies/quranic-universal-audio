"""Regenerate reciters' timestamps from this machine and report them to the Inspector.

Runs the Inspector's own timestamps run (``services.admin.ts_aligner_runner``) in
this process against the bucket: the same aligner calls, shard writes, validation,
profile and run record as a run launched from the Releases tab. A run that
succeeds is then reported through ``POST /api/admin/internal/ts-refreshed``, the
out-of-band refresh webhook, which advances the reciter's ``ts`` release and
marks its HF + GH releases stale so the next publish ships the new shards. The
state DB is only read (a private copy); every DB write happens in the Space.

Use it when many reciters need a regeneration the Space would run one at a time
(e.g. timing the recorded openers into every shard). Only changed segments are
re-timed unless ``--full``; every chapter in scope gets new shards.

Needs ``HF_TOKEN``, ``INSPECTOR_EXTRACTION_SECRET`` (aligner) and
``INSPECTOR_WEBHOOK_SECRET`` (the webhook) in the environment.

Examples::

    python scripts/backfills/regen_timestamps_offline.py --slug ibrahim_al_akhdar_drive --bucket prod
    python scripts/backfills/regen_timestamps_offline.py --slug-file slugs.txt --bucket prod --execute --workers 3
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("regen_timestamps_offline")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}
_INSPECTORS = {
    "dev": "https://qud-technologies-quranic-inspector-dev.hf.space",
    "prod": "https://qud-technologies-quranic-universal-audio.hf.space",
}
#: The aligner both Inspector Spaces time against (their ``INSPECTOR_ALIGNER_URL``).
ALIGNER_URL = "https://qud-technologies-quranic-universal-aligner-dev.hf.space"


def _setup_paths_and_env(bucket: str) -> None:
    """Import the Inspector against ``bucket`` with a private, never-uploaded copy of its DB."""
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]
    if bucket == "prod":
        os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"
    os.environ.setdefault("INSPECTOR_ALIGNER_URL", ALIGNER_URL)
    os.environ["INSPECTOR_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "inspector.db")

    from services.db import sync

    def refuse(*_args, **_kwargs):
        raise RuntimeError("backfills never upload the state DB")

    sync.upload = refuse
    sync.daily_snapshot = refuse
    sync.pull()


def regen_one(slug: str, chapters: list[int] | None, full: bool, deps: dict) -> str:
    """Run ``slug``'s timestamps and report a success; returns the run's outcome."""
    record = deps["new_record"](slug, chapters)
    deps["run"](record, deps["riwayah"](slug), full)
    if record.status != "succeeded":
        log.error("%s: %s — %s", slug, record.status, record.error)
        return record.status
    if not deps["notify"](slug, chapters):
        log.error("%s: shards written but the ts-refreshed report was not accepted", slug)
        return "unreported"
    return "succeeded"


def _read_slugs(args) -> list[str]:
    slugs = list(args.slug or [])
    if args.slug_file:
        slugs += Path(args.slug_file).read_text("utf-8").split()
    return list(dict.fromkeys(slugs))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", nargs="+")
    ap.add_argument("--slug-file", help="whitespace-separated slugs")
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument("--chapters", help="comma-separated surahs; default every chapter")
    ap.add_argument(
        "--full", action="store_true", help="re-time every segment, not only stale ones"
    )
    ap.add_argument("--workers", type=int, default=1, help="reciters run at once")
    ap.add_argument("--execute", action="store_true", help="Run. Without it the script only lists.")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    slugs = _read_slugs(args)
    if not slugs:
        ap.error("no slugs given")
    chapters = [int(c) for c in args.chapters.split(",")] if args.chapters else None
    if not args.execute:
        log.info("DRY RUN: would regenerate %d reciter(s), chapters=%s", len(slugs), chapters)
        return 0
    _setup_paths_and_env(args.bucket)

    from qua_shared.inspector_notify import notify_ts_refreshed
    from qua_shared.schemas import TsJobSettings
    from qua_shared.schemas.bucket.ts_job_record import TsJobRecord
    from services.admin import ts_aligner_runner
    from services.reference.delivery_edition import sdk_riwayah_for

    def new_record(slug: str, chs: list[int] | None) -> TsJobRecord:
        now = datetime.now(UTC)
        return TsJobRecord(
            job_id=f"ts-offline-{now:%Y%m%dT%H%M%SZ}-{slug[:24]}",
            slug=slug,
            settings=TsJobSettings(chapters=chs),
            chapters_refreshed=chs,
            started_at=now.isoformat().replace("+00:00", "Z"),
        )

    deps = {
        "new_record": new_record,
        "run": ts_aligner_runner._run,
        "riwayah": sdk_riwayah_for,
        "notify": lambda slug, chs: notify_ts_refreshed(
            _INSPECTORS[args.bucket], slug, chapters=chs, reason="offline_regen"
        ),
    }
    with ThreadPoolExecutor(max(1, args.workers)) as pool:
        outcomes = dict(
            zip(
                slugs,
                pool.map(lambda s: regen_one(s, chapters, args.full, deps), slugs),
                strict=True,
            )
        )
    for slug, outcome in outcomes.items():
        log.info("%s: %s", slug, outcome)
    done = sum(o == "succeeded" for o in outcomes.values())
    log.info("EXECUTED: %d/%d reciters regenerated", done, len(outcomes))
    return 0 if done == len(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
