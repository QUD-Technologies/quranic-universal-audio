"""Fill ``catalog/review_signals.json`` — the reciter picker's open review counts.

Offline, from this machine: each delivery's open Low Confidence (under its riwayah
threshold) and Low Confidence v2 counts, computed by
``services.validation.review_signals.compute(offline=True)`` (the same gates the validator
uses), merged into the bucket file. Reads every listed delivery's ``detailed.json``,
sidecar and edit history; writes only that one small catalog file (no DB, no Space pause).
The running Space picks the file up within ``REFRESH_S`` (10 min).

Dry run by default (prints the counts). ``--execute`` writes.

    python scripts/backfills/review_signals.py --bucket prod                 # every delivery
    python scripts/backfills/review_signals.py --bucket prod --slugs a b      # just these
    python scripts/backfills/review_signals.py --bucket prod --execute
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}
#: Reciters computed at once (each is bucket reads + a JSON parse).
THREADS = 8


def _setup(bucket: str) -> None:
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]
    os.environ["INSPECTOR_BACKEND"] = "bucket"
    os.environ["INSPECTOR_AUTO_MOUNT"] = "0"
    if bucket == "prod":
        # The prod-bucket guard protects the SQLite DB sync. This script never opens the DB:
        # it reads reciter files and writes the one catalog file.
        os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"


def _all_slugs() -> list[str]:
    from huggingface_hub import HfFileSystem

    root = f"buckets/{os.environ['INSPECTOR_BUCKET_REPO']}/reciters"
    fs = HfFileSystem()
    return sorted(
        str(p).rsplit("/", 1)[1]
        for p in fs.ls(root, detail=False)
        if fs.exists(f"{p}/detailed.json")
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), required=True)
    ap.add_argument("--slugs", nargs="*", help="deliveries to (re)count (default: every one)")
    ap.add_argument("--execute", action="store_true", help="write the file (default: dry run)")
    args = ap.parse_args()
    _setup(args.bucket)

    from services.validation import review_signals

    slugs = args.slugs or _all_slugs()
    print(f"counting {len(slugs)} deliveries on {args.bucket}", flush=True)

    def one(slug: str) -> tuple[str, dict[str, int] | None, str | None]:
        try:
            return slug, review_signals.compute(slug, offline=True), None
        except Exception as e:  # noqa: BLE001 — report and skip, never write a guess
            return slug, None, f"{type(e).__name__}: {e}"

    updates: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(THREADS) as pool:
        for slug, counts, err in pool.map(one, slugs):
            if err is not None:
                failures.append(f"{slug}: {err}")
                print(f"  FAILED {slug}: {err}", flush=True)
                continue
            if counts is None:
                continue
            updates[slug] = counts
            if any(counts.values()):
                print(f"  {slug}: {counts}", flush=True)
    open_n = sum(1 for c in updates.values() if any(c.values()))
    print(f"{len(updates)} counted, {open_n} with open items, {len(failures)} failed")
    if not args.execute:
        print("dry run: pass --execute to write catalog/review_signals.json")
        return 1 if failures else 0
    merged = review_signals.write_counts(updates)
    print(f"wrote catalog/review_signals.json: {len(merged)} deliveries with open items")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
