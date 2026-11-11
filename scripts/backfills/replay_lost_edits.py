"""Write back segment edits that reached edit history but not detailed.json.

Overlapping saves of one reciter could each write the whole detailed.json from a copy
that lacked the other's changes, so ``edit_history.jsonl`` logged an edit the live
document does not carry. For every live segment whose latest op is not reverted, whose
``segment_uid``, ``time_start``, ``time_end`` and ``matched_ref`` still match that op's
``targets_after`` snapshot, this restores each field the op changed (before → after)
among :data:`RESTORABLE` when the live value differs. History already holds the edits,
so no op is appended.

Dry run by default. ``--execute`` writes; restart the Space afterwards so its
in-memory detailed.json is reloaded (a save from the running Space would otherwise
write its cached copy back over the repair).

Examples::

    python scripts/backfills/replay_lost_edits.py --slug abdulbasit_abdulsamad_mujawwad_tarteel
    python scripts/backfills/replay_lost_edits.py --slug a b --bucket prod --execute
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("replay_lost_edits")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}

#: Segment fields an edit op sets on a segment it keeps (uid, span and ref unchanged).
RESTORABLE = ("ignored_categories", "join_verdicts", "is_wasl", "confidence")
#: Fields where an absent value and an empty list mean the same thing.
_LIST_FIELDS = frozenset({"ignored_categories", "join_verdicts"})
_IDENTITY = ("time_start", "time_end", "matched_ref")


@dataclass(frozen=True)
class Restore:
    uid: str
    chapter: int | None
    matched_ref: str
    batch_id: str
    saved_at_utc: str
    op_type: str
    fields: dict[str, object]
    live: dict[str, object]


def _setup_paths_and_env(bucket: str, execute: bool) -> None:
    """Point the storage backend at ``bucket``, read-only unless ``execute``."""
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]
    os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"
    if not execute:
        os.environ["INSPECTOR_READ_ONLY"] = "1"


def _norm(field: str, value: object) -> object:
    if field in _LIST_FIELDS:
        return value or []
    if field == "is_wasl":
        return bool(value)
    return value


def _snapshots(op: dict, side: str) -> list[dict]:
    snaps = op.get(f"targets_{side}")
    if snaps is None:
        snaps = (op.get("snapshots") or {}).get(side)
    if isinstance(snaps, dict):
        snaps = [snaps]
    return [s for s in snaps or [] if isinstance(s, dict)]


def _reverted(history: list[dict]) -> tuple[set[str], set[str]]:
    ops: set[str] = set()
    batches: set[str] = set()
    for rec in history:
        if rec.get("reverts_op_ids"):
            ops.update(rec["reverts_op_ids"])
        elif rec.get("reverts_batch_id"):
            batches.add(rec["reverts_batch_id"])
    return ops, batches


def _latest_ops(history: list[dict]) -> dict[str, tuple[dict, dict, dict | None]]:
    """``{uid: (batch, op, before_snapshot)}`` for the last op whose after-state
    names the segment, reverted or not."""
    latest: dict[str, tuple[dict, dict, dict | None]] = {}
    for rec in sorted(history, key=lambda r: r.get("saved_at_utc") or ""):
        if rec.get("reverts_batch_id"):
            continue
        for op in rec.get("operations") or []:
            before = {s.get("segment_uid"): s for s in _snapshots(op, "before")}
            for snap in _snapshots(op, "after"):
                uid = snap.get("segment_uid")
                if uid:
                    latest[uid] = (rec, op, before.get(uid))
    return latest


def _changed_fields(before: dict | None, after: dict) -> list[str]:
    """The :data:`RESTORABLE` fields the op set: all present after a segment it
    created, else those whose value differs between its before and after."""
    if before is None:
        return [f for f in RESTORABLE if f in after]
    return [f for f in RESTORABLE if _norm(f, before.get(f)) != _norm(f, after.get(f))]


def lost_edits(history: list[dict], live: dict[str, dict]) -> list[Restore]:
    """Each live segment whose latest unreverted op's after-state it does not carry."""
    reverted_ops, reverted_batches = _reverted(history)
    out: list[Restore] = []
    for uid, (rec, op, before) in _latest_ops(history).items():
        seg = live.get(uid)
        if seg is None or rec.get("batch_id") in reverted_batches:
            continue
        if op.get("op_id") in reverted_ops:
            continue
        after = next(s for s in _snapshots(op, "after") if s.get("segment_uid") == uid)
        if any(seg.get(k) != after.get(k) for k in _IDENTITY):
            continue
        fields = {
            f: after.get(f)
            for f in _changed_fields(before, after)
            if _norm(f, seg.get(f)) != _norm(f, after.get(f))
        }
        if fields:
            out.append(
                Restore(
                    uid=uid,
                    chapter=after.get("chapter"),
                    matched_ref=str(after.get("matched_ref")),
                    batch_id=str(rec.get("batch_id")),
                    saved_at_utc=str(rec.get("saved_at_utc")),
                    op_type=str(op.get("op_type")),
                    fields=fields,
                    live={f: seg.get(f) for f in fields},
                )
            )
    return sorted(out, key=lambda r: (r.saved_at_utc, r.uid))


def apply_restore(seg: dict, fields: dict[str, object]) -> None:
    """Set each restored field on ``seg``; an empty list field is dropped, as a save does."""
    for field, value in fields.items():
        if field in _LIST_FIELDS and not value:
            seg.pop(field, None)
        else:
            seg[field] = value


def _replay(slug: str, execute: bool, deps: dict) -> int:
    entries = deps["load_detailed"](slug)
    if not entries:
        log.error("no detailed entries for slug=%s", slug)
        return 0
    live = {
        s["segment_uid"]: s for e in entries for s in e.get("segments", []) if s.get("segment_uid")
    }
    restores = lost_edits(deps["parse_history"](slug) or [], live)
    for r in restores:
        log.info(
            "%s ch%s %s %s (%s %s %s): %s -> %s",
            slug, r.chapter, r.uid, r.matched_ref, r.op_type, r.saved_at_utc,
            r.batch_id, r.live, r.fields,
        )  # fmt: skip
    chapters = sorted({r.chapter for r in restores if r.chapter is not None})
    log.info("%s: %d segments to restore in chapters %s", slug, len(restores), chapters)
    if not restores or not execute:
        return len(restores)
    with deps["detailed_lock"](slug):
        for r in restores:
            apply_restore(live[r.uid], r.fields)
        deps["persist_detailed"](slug, deps["cache"].get_seg_meta(slug), entries, chapters)
        deps["cache"].pop_seg_caches_affected_by_segment_edit(slug)
    return len(restores)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", nargs="+", required=True)
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument(
        "--execute", action="store_true", help="Write. Without it the script only reports."
    )
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _setup_paths_and_env(args.bucket, args.execute)

    from services.activity.history_query import parse_history_for_reciter
    from services.segments.save import persist_detailed
    from services.storage import cache
    from services.storage.data_loader import detailed_lock, load_detailed

    deps = {
        "load_detailed": load_detailed,
        "parse_history": parse_history_for_reciter,
        "persist_detailed": persist_detailed,
        "detailed_lock": detailed_lock,
        "cache": cache,
    }
    total = sum(_replay(slug, args.execute, deps) for slug in args.slug)
    log.info(
        "%s: %d segments across %d deliveries",
        "EXECUTED" if args.execute else "DRY RUN",
        total,
        len(args.slug),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
