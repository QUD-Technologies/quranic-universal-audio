"""Undo Missed Waqf splits whose only cut was answered WASL.

A Missed Waqf card answered WASL means the reciter did not stop: the segment stays
whole and the item is ignored for ``missed_waqf``. A split launched from that card
whose single cut carries ``is_wasl`` contradicts that, so this script reverses it
through :func:`services.segments.undo.undo_ops` (same audit trail as the undo button)
and then records an ``ignore_issue`` op for ``missed_waqf`` on the restored segment.

Only single-cut splits are touched; a split with more than one cut is reported and
left alone. Dry run by default.

Examples::

    python3 scripts/backfills/missed_waqf_unsplit_wasl.py --slug fatih_seferagic_way2quran --bucket prod
    python3 scripts/backfills/missed_waqf_unsplit_wasl.py --slug fatih_seferagic_way2quran --bucket prod --apply
"""

from __future__ import annotations

import argparse
import copy
import logging
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("missed_waqf_unsplit_wasl")

CATEGORY = "missed_waqf"
_BUCKETS = {
    "dev": "hetchyy/quranic-inspector-bucket-dev",
    "prod": "hetchyy/quranic-inspector-bucket",
}
_OWNER_ID = "684abe5b6327ae8863d106d2"
_OWNER_LOGIN = "hetchyy"


def _setup_paths_and_env(bucket: str) -> None:
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]


def _live_segments(entries: list[dict]) -> dict[str, tuple[dict, str]]:
    return {
        seg["segment_uid"]: (seg, entry.get("ref", ""))
        for entry in entries
        for seg in entry.get("segments", [])
        if seg.get("segment_uid")
    }


def _candidates(
    history: list[dict], live: dict[str, tuple[dict, str]]
) -> tuple[list[tuple[str, str, str]], int]:
    """``[(batch_id, op_id, root_uid)]`` for live single-cut WASL splits, and the
    number of multi-cut WASL splits left alone."""
    reverted_ops: set[str] = set()
    reverted_batches: set[str] = set()
    for rec in history:
        if rec.get("reverts_batch_id"):
            if rec.get("reverts_op_ids"):
                reverted_ops.update(rec["reverts_op_ids"])
            else:
                reverted_batches.add(rec["reverts_batch_id"])
    out: list[tuple[str, str, str]] = []
    multi = 0
    for rec in history:
        batch_id = rec.get("batch_id", "")
        if rec.get("reverts_batch_id") or batch_id in reverted_batches:
            continue
        for op in rec.get("operations") or []:
            if op.get("op_type") != "split_segment" or op.get("op_context_category") != CATEGORY:
                continue
            if op.get("op_id") in reverted_ops:
                continue
            after = op.get("targets_after") or []
            uids = [t.get("segment_uid") for t in after]
            if len(after) < 2 or not all(u in live for u in uids):
                continue
            if not any(live[u][0].get("is_wasl") for u in uids[:-1]):
                continue
            if len(after) > 2:
                multi += 1
                continue
            out.append((batch_id, op["op_id"], uids[0]))
    return out, multi


def _ignore_record(slug: str, uid: str, actor, deps) -> bool:
    """Add ``missed_waqf`` to the restored segment's ignores and log the op."""
    entries = deps["load_detailed"](slug)
    live = _live_segments(entries)
    if uid not in live:
        log.error("ignore: uid=%s not live after undo", uid)
        return False
    seg, entry_ref = live[uid]
    before = copy.deepcopy(seg)
    cats = list(seg.get("ignored_categories") or [])
    if CATEGORY not in cats:
        cats.append(CATEGORY)
    seg["ignored_categories"] = cats
    deps["persist_detailed"](slug, deps["cache"].get_seg_meta(slug), entries)
    chapter = deps["chapter_from_ref"](entry_ref) if entry_ref else None
    fields = ("time_start", "time_end", "matched_ref", "confidence", "ignored_categories")

    def snap(state: dict) -> dict:
        return {"segment_uid": uid, "chapter": chapter, **{k: state.get(k) for k in fields}}

    record = {
        "schema_version": deps["history_schema_version"],
        "batch_id": deps["uuid7"](),
        "saved_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "chapter": chapter,
        "operations": [
            {
                "op_id": deps["uuid7"](),
                "op_type": "ignore_issue",
                "op_context_category": CATEGORY,
                "fix_kind": "ignore",
                "targets_before": [snap(before)],
                "targets_after": [snap(seg)],
            }
        ],
        "actor": actor.model_dump(mode="json"),
    }
    deps["append_edit_history"](slug, record)
    deps["cache"].append_history_batch(slug, record)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument(
        "--apply", action="store_true", help="Mutate. Without it the script only reports."
    )
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _setup_paths_and_env(args.bucket)

    from constants import HISTORY_SCHEMA_VERSION
    from qua_shared.schemas import Actor
    from qua_shared.schemas.config.access import Role
    from services.activity.history_query import parse_history_for_reciter
    from services.segments.save import persist_detailed
    from services.segments.undo import undo_ops
    from services.state import state as state_service
    from services.storage import cache, data_dir
    from services.storage.data_loader import load_detailed
    from utils.references import chapter_from_ref
    from utils.uuid7 import uuid7

    state_service.hydrate()
    # The catalog lives in the Space's database; a local run takes the edition
    # from detailed.json, which sdk_riwayah_for cross-checks against anyway.
    from services.reference import delivery_edition
    from services.storage import data_loader

    delivery_edition.inspector_riwayah_for = lambda slug: (
        (data_loader.seg_meta(slug) or {}).get("riwayah") or delivery_edition.DEFAULT_RIWAYAH
    )
    entries = load_detailed(args.slug)
    if not entries:
        log.error("no detailed entries for slug=%s", args.slug)
        return 2
    history = parse_history_for_reciter(args.slug) or []
    candidates, multi = _candidates(history, _live_segments(entries))
    log.info("single-cut WASL splits: %d; multi-cut left alone: %d", len(candidates), multi)
    for batch_id, op_id, uid in candidates:
        log.info("  batch=%s op=%s root=%s", batch_id, op_id, uid)
    if not candidates or not args.apply:
        if candidates:
            log.info("DRY RUN — re-run with --apply to mutate.")
        return 0

    actor = Actor(hf_user_id=_OWNER_ID, login_at_time=_OWNER_LOGIN, role=Role.OWNER)
    by_batch: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for batch_id, op_id, uid in candidates:
        by_batch[batch_id].append((op_id, uid))
    deps = {
        "load_detailed": load_detailed,
        "persist_detailed": persist_detailed,
        "cache": cache,
        "chapter_from_ref": chapter_from_ref,
        "append_edit_history": data_dir.append_edit_history,
        "uuid7": uuid7,
        "history_schema_version": HISTORY_SCHEMA_VERSION,
    }
    undone = ignored = failed = 0
    for batch_id, pairs in by_batch.items():
        result = undo_ops(args.slug, batch_id, {op_id for op_id, _ in pairs}, actor=actor)
        if isinstance(result, tuple):
            log.error("batch=%s undo failed: %s", batch_id, result)
            failed += len(pairs)
            continue
        undone += result.get("operations_reversed", 0)
        for _op_id, uid in pairs:
            ignored += _ignore_record(args.slug, uid, actor, deps)
    cache.pop_seg_caches_affected_by_segment_edit(args.slug)
    cache.pop_seg_split_group_index(args.slug)
    log.info("DONE: undone=%d ignored=%d failed=%d", undone, ignored, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
