"""Write back ``is_wasl`` answers that reached edit history but not detailed.json.

Patch saves dropped ``is_wasl`` until the save fix, so a lone WASL / WAQF toggle was
logged as a ``set_is_wasl`` op while the segment kept its old value. For every live
segment whose latest op (reverted ops skipped) is a ``set_is_wasl``, this sets
``is_wasl`` to that op's value when they differ. History already holds the answer, so
no op is appended. Dry run by default; restart the Space after ``--apply`` so its
in-memory detailed.json is reloaded.

Examples::

    python3 scripts/backfills/replay_lost_is_wasl.py --slug islam_sobhi_mp3quran --bucket prod
    python3 scripts/backfills/replay_lost_is_wasl.py --slug a b c --bucket prod --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("replay_lost_is_wasl")

_BUCKETS = {
    "dev": "hetchyy/quranic-inspector-bucket-dev",
    "prod": "hetchyy/quranic-inspector-bucket",
}


def _setup_paths_and_env(bucket: str) -> None:
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]


def _reverted(history: list[dict]) -> tuple[set[str], set[str]]:
    ops: set[str] = set()
    batches: set[str] = set()
    for rec in history:
        if rec.get("reverts_op_ids"):
            ops.update(rec["reverts_op_ids"])
        elif rec.get("reverts_batch_id"):
            batches.add(rec["reverts_batch_id"])
    return ops, batches


def lost_answers(history: list[dict], live: dict[str, dict]) -> dict[str, bool]:
    """``{uid: is_wasl}`` for live segments whose latest op is a ``set_is_wasl``
    the segment does not reflect."""
    reverted_ops, reverted_batches = _reverted(history)
    latest: dict[str, tuple[str, bool]] = {}
    for rec in history:
        if rec.get("batch_id") in reverted_batches:
            continue
        for op in rec.get("operations") or []:
            if op.get("op_id") in reverted_ops:
                continue
            for snap in op.get("targets_after") or []:
                uid = snap.get("segment_uid")
                if uid:
                    latest[uid] = (op.get("op_type", ""), bool(snap.get("is_wasl")))
    return {
        uid: want
        for uid, (op_type, want) in latest.items()
        if op_type == "set_is_wasl" and uid in live and bool(live[uid].get("is_wasl")) != want
    }


def _replay(slug: str, apply: bool, deps: dict) -> int:
    entries = deps["load_detailed"](slug)
    if not entries:
        log.error("no detailed entries for slug=%s", slug)
        return 0
    live = {
        s["segment_uid"]: s for e in entries for s in e.get("segments", []) if s.get("segment_uid")
    }
    lost = lost_answers(deps["parse_history"](slug) or [], live)
    to_wasl = sum(lost.values())
    log.info(
        "%s: %d lost answers (%d wasl, %d waqf)", slug, len(lost), to_wasl, len(lost) - to_wasl
    )
    if not lost or not apply:
        return len(lost)
    for uid, want in lost.items():
        live[uid]["is_wasl"] = want
    deps["persist_detailed"](slug, deps["cache"].get_seg_meta(slug), entries)
    deps["cache"].pop_seg_caches_affected_by_segment_edit(slug)
    return len(lost)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", nargs="+", required=True)
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument(
        "--apply", action="store_true", help="Mutate. Without it the script only reports."
    )
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _setup_paths_and_env(args.bucket)

    from services.activity.history_query import parse_history_for_reciter
    from services.segments.save import persist_detailed
    from services.state import state as state_service
    from services.storage import cache
    from services.storage.data_loader import load_detailed

    state_service.hydrate()
    deps = {
        "load_detailed": load_detailed,
        "parse_history": parse_history_for_reciter,
        "persist_detailed": persist_detailed,
        "cache": cache,
    }
    total = sum(_replay(slug, args.apply, deps) for slug in args.slug)
    log.info(
        "%s: %d answers across %d deliveries",
        "APPLIED" if args.apply else "DRY RUN",
        total,
        len(args.slug),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
