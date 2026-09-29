"""Store the lab's WASL verse-end verdicts on the segments that kept those joins.

``apply_verse_end_verdicts`` applied a ``verse_ends_applied.json``'s WAQF verdicts
as splits; a WASL verdict needed no split, so nothing recorded it and the
cross-verse card still asks it. This script writes each WASL verdict as a
``join_verdicts`` answer (``at_ms`` = the lab's cursor) on the live segment, the
way a reviewer's all-WASL answer does: when every verse end inside the segment
then has an answer, the segment is settled for ``cross_verse`` (``ignore_issue``,
``fix_kind`` ``auto_fix``); otherwise only the answers are stored and the rest
stay asked. One op per segment, so the History panel shows it and undo reverts it.

A join is skipped when its segment is gone, its word is no longer a verse end
inside the segment, the cursor is outside it, or the answer is already stored.
Each chapter save waits until both files read back. Dry run by default; restart
the Space before and after ``--apply`` so its in-memory detailed.json matches.

    python3 scripts/backfills/store_verse_end_verdicts.py --slug abdulmohsin_al_qasim_qdc \\
        --applied runs/abdulmohsin_al_qasim_qdc/v4/cv/verse_ends_applied.json
    python3 scripts/backfills/store_verse_end_verdicts.py --slug ... --applied ... --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_verse_end_verdicts import (  # noqa: E402
    _BUCKETS,
    _OWNER_ID,
    _OWNER_LOGIN,
    _await_visible,
    _setup_paths_and_env,
    _snapshot,
)

log = logging.getLogger("store_verse_end_verdicts")

CATEGORY = "cross_verse"
FIX_KIND = "auto_fix"
WASL = "wasl"


@dataclass(frozen=True)
class Store:
    chapter: int
    uid: str
    verdicts: tuple[dict, ...]
    settle: bool


def _live(entries: list[dict], chapter_of: Callable[[str], int]) -> dict[str, tuple[dict, int]]:
    return {
        seg["segment_uid"]: (seg, chapter_of(entry["ref"]))
        for entry in entries
        for seg in entry.get("segments", [])
        if seg.get("segment_uid")
    }


def _plan_one(seg: dict, joins: list[dict], verse_ends: list[str], tally: Counter) -> list[dict]:
    stored = {j["after_ref"] for j in seg.get("join_verdicts") or []}
    out: list[dict] = []
    for join in joins:
        ref, at = join.get("after_ref"), join.get("cursor_ms")
        if ref not in verse_ends:
            tally["ref_moved"] += 1
        elif not isinstance(at, int) or not seg["time_start"] < at <= seg["time_end"]:
            tally["cursor_outside"] += 1
        elif ref in stored:
            tally["already_stored"] += 1
        else:
            out.append({"after_ref": ref, "at_ms": at, "verdict": WASL})
    return out


def plan_verdicts(
    entries: list[dict],
    applied: dict[str, dict],
    verse_ends_of: Callable[[str], list[str]],
    chapter_of: Callable[[str], int],
) -> tuple[list[Store], dict[str, int]]:
    """The answers ``applied``'s WASL verdicts add, and why the others were skipped."""
    live = _live(entries, chapter_of)
    tally: Counter[str] = Counter()
    out: list[Store] = []
    for uid, item in applied.items():
        joins = [j for j in item.get("joins") or [] if j.get("verdict") == WASL]
        if not joins:
            continue
        found = live.get(uid)
        if found is None:
            tally["gone"] += len(joins)
            continue
        seg, chapter = found
        ends = verse_ends_of(seg.get("matched_ref") or "")
        verdicts = _plan_one(seg, joins, ends, tally)
        if not verdicts:
            continue
        answered = {j["after_ref"] for j in seg.get("join_verdicts") or []}
        answered |= {v["after_ref"] for v in verdicts}
        settle = CATEGORY not in (seg.get("ignored_categories") or []) and set(ends) <= answered
        tally["stored"] += len(verdicts)
        tally["segments_settled" if settle else "segments_partial"] += 1
        out.append(Store(chapter, uid, tuple(verdicts), settle))
    return out, dict(tally)


def _answered(seg: dict, store: Store) -> dict:
    """``_reduceIgnoreIssue`` with WASL answers: verdicts added, settled when complete."""
    out = copy.deepcopy(seg)
    out.pop("word_timings", None)
    verdicts = [*(out.get("join_verdicts") or []), *copy.deepcopy(store.verdicts)]
    out["join_verdicts"] = sorted(verdicts, key=lambda j: (j["at_ms"], j["after_ref"]))
    if store.settle:
        out["ignored_categories"] = [*(out.get("ignored_categories") or []), CATEGORY]
        out["confidence"] = 1.0
    return out


def _answer_op(before: dict, after: dict, index: int, chapter: int, entry: dict, uuid) -> dict:
    snap_before = [_snapshot(before, index, chapter, entry)]
    snap_after = [_snapshot(after, index, chapter, entry)]
    return {
        "op_id": uuid(),
        "op_type": "ignore_issue",
        "op_context_category": CATEGORY,
        "fix_kind": FIX_KIND,
        "targets_before": snap_before,
        "targets_after": snap_after,
        "affected_chapters": [chapter],
        "patch": {
            "before": snap_before,
            "after": snap_after,
            "removedIds": [],
            "insertedIds": [],
            "affectedChapterIds": [chapter],
        },
    }


def chapter_save(
    entries: list[dict], chapter: int, stores: list[Store], uuid, chapter_of: Callable[[str], int]
) -> dict:
    """The full-replace save payload for ``chapter`` with ``stores`` answered."""
    by_uid = {s.uid: s for s in stores if s.chapter == chapter}
    segments: list[dict] = []
    operations: list[dict] = []
    index = 0
    for entry in entries:
        if chapter_of(entry["ref"]) != chapter:
            continue
        for seg in entry.get("segments", []):
            store = by_uid.get(seg.get("segment_uid") or "")
            out = seg
            if store:
                out = _answered(seg, store)
                operations.append(_answer_op(seg, out, index, chapter, entry, uuid))
            segments.append({**out, "audio_url": entry.get("audio") or ""})
            index += 1
    return {"full_replace": True, "segments": segments, "operations": operations}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--applied", required=True, help="the verse_ends_applied.json that was applied")
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument(
        "--apply", action="store_true", help="Mutate. Without it the script only reports."
    )
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _setup_paths_and_env(args.bucket)

    from qua_shared.schemas import Actor
    from qua_shared.schemas.config.access import Role
    from qua_shared.riwayat import resolve_sdk_slug
    from services.segments.save import save_seg_data
    from services.state import state as state_service
    from services.storage import cache, data_dir
    from services.storage.data_loader import get_word_counts, load_detailed, seg_meta
    from services.validation.detail import _verse_end_refs
    from utils.references import chapter_from_ref
    from utils.uuid7 import uuid7

    state_service.hydrate()
    doc = json.loads(Path(args.applied).read_text(encoding="utf-8"))
    applied = doc.get("by_uid") or {}
    # The edition detailed.json was aligned in (absent for Hafs); the catalog lives in
    # the Space's DB, which this process does not pull.
    riwayah = (seg_meta(args.slug) or {}).get("riwayah") or "hafs_an_asim"
    word_counts = get_word_counts(resolve_sdk_slug(riwayah))
    entries = load_detailed(args.slug)
    if not entries:
        log.error("no detailed entries for slug=%s", args.slug)
        return 2

    def verse_ends_of(ref: str) -> list[str]:
        return _verse_end_refs(ref, word_counts)

    stores, tally = plan_verdicts(entries, applied, verse_ends_of, chapter_from_ref)
    log.info("%s: %d segments to answer; %s", args.slug, len(stores), tally)
    if not stores or not args.apply:
        if stores:
            log.info("DRY RUN — re-run with --apply to mutate.")
        return 0

    actor = Actor(hf_user_id=_OWNER_ID, login_at_time=_OWNER_LOGIN, role=Role.OWNER)

    def read_history() -> bytes | None:
        try:
            return data_dir.get_backend().read_bytes(data_dir.edit_history_path(args.slug))
        except Exception:  # noqa: BLE001 — absent or mid-write; the poll retries
            return None

    failed = 0
    chapters = sorted({s.chapter for s in stores})
    for chapter in chapters:
        cache.pop_seg_caches_affected_by_segment_edit(args.slug)
        payload = chapter_save(load_detailed(args.slug), chapter, stores, uuid7, chapter_from_ref)
        if not payload["operations"]:
            continue
        result = save_seg_data(args.slug, chapter, payload, actor=actor)
        if isinstance(result, tuple):
            log.error("chapter %d: save failed %s", chapter, result)
            failed += 1
            continue
        ops = payload["operations"]
        _await_visible(read_history, [op["op_id"] for op in ops], f"ch {chapter} history")
        log.info("chapter %d: %d segments answered", chapter, len(ops))
    cache.pop_seg_caches_affected_by_segment_edit(args.slug)
    log.info("DONE: %d chapters, %d failed", len(chapters), failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
