"""Open the measured silence between abutting stop segments.

A split cuts at one cursor, so both pieces meet there and the pause is shared
between them with no gap. The timing engine's cut-timing pass
(``cut_timing_v1.json``, ``joins[]`` of ``kind`` ``stop`` keyed by the LEFT
segment's uid) aligned each such pair with a seeded psil and measured the
silence. This script trims each pair to it: the left segment ends where the
pause starts, the right starts where it ends, and the left's WAQF verdict moves
with its edge. Each pair is two ``trim_segment`` ops (``fix_kind`` ``auto_fix``)
chained through the uids, so the History panel shows one edit per stop.

A pair is skipped when it no longer abuts, its word no longer ends the left
segment, or the silence does not leave both pieces at least ``MIN_PIECE_MS``.
Joins timed ``none`` (no measurable silence) are left alone. Each chapter save
waits until both files read back. Dry run by default; restart the Space before
and after ``--apply`` so its in-memory detailed.json matches the bucket.

    python3 scripts/backfills/open_stop_gaps.py --slug abdulrahman_az_zawawi_way2quran
    python3 scripts/backfills/open_stop_gaps.py --slug ... --timing run/cut_timing_v1.json --apply
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

log = logging.getLogger("open_stop_gaps")

FIX_KIND = "auto_fix"
STOP = "stop"
TIMED_SOURCES = frozenset({"psil", "energy"})
#: Shortest piece a trim may leave (the editor's own ``EDIT_MIN_DURATION_MS``).
MIN_PIECE_MS = 50


@dataclass(frozen=True)
class Gap:
    chapter: int
    left_uid: str
    right_uid: str
    start_ms: int
    end_ms: int


def _end_ref(ref: str) -> str:
    return ref.rpartition("-")[2]


def _rows_by_chapter(
    entries: list[dict], chapter_of: Callable[[str], int]
) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for entry in entries:
        out.setdefault(chapter_of(entry["ref"]), []).extend(entry.get("segments", []))
    return out


def _gap_for(join: dict, left: dict, right: dict) -> tuple[Gap | None, str]:
    a, b = join.get("silence_start_ms"), join.get("silence_end_ms")
    if join.get("source") not in TIMED_SOURCES or not isinstance(a, int) or not isinstance(b, int):
        return None, "untimed"
    if right["time_start"] != left["time_end"]:
        return None, "not_abutting"
    if join.get("after_ref") != _end_ref(left.get("matched_ref") or ""):
        return None, "word_moved"
    if a - left["time_start"] < MIN_PIECE_MS or right["time_end"] - b < MIN_PIECE_MS or a >= b:
        return None, "too_short"
    if not a <= left["time_end"] <= b:
        return None, "silence_off_boundary"
    return Gap(0, left["segment_uid"], right["segment_uid"], a, b), "ok"


def plan_gaps(
    entries: list[dict], timing: dict[str, dict], chapter_of: Callable[[str], int]
) -> tuple[list[Gap], dict[str, int]]:
    """The gaps ``timing``'s stop joins ask for, and why the others were skipped."""
    tally: Counter[str] = Counter()
    out: list[Gap] = []
    for chapter, rows in _rows_by_chapter(entries, chapter_of).items():
        for left, right in zip(rows, rows[1:], strict=False):
            item = timing.get(left.get("segment_uid") or "")
            joins = [j for j in (item or {}).get("joins") or [] if j.get("kind") == STOP]
            if not joins:
                continue
            gap, why = _gap_for(joins[0], left, right)
            tally[why] += 1
            if gap:
                out.append(Gap(chapter, gap.left_uid, gap.right_uid, gap.start_ms, gap.end_ms))
    return out, dict(tally)


def _trimmed(seg: dict, *, start: int | None = None, end: int | None = None) -> dict:
    """``_reduceTrim``: new bounds, confidence 1, the edge WAQF verdict moved with the end."""
    out = copy.deepcopy(seg)
    out.pop("word_timings", None)
    if end is not None:
        edge = _end_ref(seg.get("matched_ref") or "")
        for j in out.get("join_verdicts") or []:
            if j["at_ms"] == seg["time_end"] and j["after_ref"] == edge and j["verdict"] == "waqf":
                j["at_ms"] = end
        out["time_end"] = end
    if start is not None:
        out["time_start"] = start
    out["confidence"] = 1.0
    return out


def _trim_op(before: dict, after: dict, index: int, chapter: int, entry: dict, uuid) -> dict:
    snap_before = [_snapshot(before, index, chapter, entry)]
    snap_after = [_snapshot(after, index, chapter, entry)]
    return {
        "op_id": uuid(),
        "op_type": "trim_segment",
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
    entries: list[dict], chapter: int, gaps: list[Gap], uuid, chapter_of: Callable[[str], int]
) -> dict:
    """The full-replace save payload for ``chapter`` with ``gaps`` opened."""
    ends = {g.left_uid: g.start_ms for g in gaps if g.chapter == chapter}
    starts = {g.right_uid: g.end_ms for g in gaps if g.chapter == chapter}
    segments: list[dict] = []
    operations: list[dict] = []
    index = 0
    for entry in entries:
        if chapter_of(entry["ref"]) != chapter:
            continue
        for seg in entry.get("segments", []):
            uid = seg.get("segment_uid")
            out = seg
            if uid in ends:
                out = _trimmed(out, end=ends[uid])
                operations.append(_trim_op(seg, out, index, chapter, entry, uuid))
            if uid in starts:
                prev = out
                out = _trimmed(out, start=starts[uid])
                operations.append(_trim_op(prev, out, index, chapter, entry, uuid))
            segments.append({**out, "audio_url": entry.get("audio") or ""})
            index += 1
    return {"full_replace": True, "segments": segments, "operations": operations}


def _load_timing(args, read_json) -> dict[str, dict]:
    if args.timing:
        doc = json.loads(Path(args.timing).read_text(encoding="utf-8"))
    else:
        doc = read_json() or {}
    return doc.get("by_uid") or {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--timing", help="a local cut_timing_v1.json (default: the bucket's)")
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
    from services.segments.save import save_seg_data
    from services.state import state as state_service
    from services.storage import cache, data_dir
    from services.storage.data_loader import load_detailed
    from utils.references import chapter_from_ref
    from utils.uuid7 import uuid7

    state_service.hydrate()
    timing = _load_timing(args, lambda: data_dir.read_cut_timing_doc(args.slug))
    entries = load_detailed(args.slug)
    if not entries:
        log.error("no detailed entries for slug=%s", args.slug)
        return 2
    gaps, tally = plan_gaps(entries, timing, chapter_from_ref)
    log.info("%s: %d gaps to open; %s", args.slug, len(gaps), tally)
    if not gaps or not args.apply:
        if gaps:
            log.info("DRY RUN — re-run with --apply to mutate.")
        return 0

    actor = Actor(hf_user_id=_OWNER_ID, login_at_time=_OWNER_LOGIN, role=Role.OWNER)

    def read_history() -> bytes | None:
        try:
            return data_dir.get_backend().read_bytes(data_dir.edit_history_path(args.slug))
        except Exception:  # noqa: BLE001 — absent or mid-write; the poll retries
            return None

    failed = 0
    for chapter in sorted({g.chapter for g in gaps}):
        cache.pop_seg_caches_affected_by_segment_edit(args.slug)
        payload = chapter_save(load_detailed(args.slug), chapter, gaps, uuid7, chapter_from_ref)
        if not payload["operations"]:
            continue
        result = save_seg_data(args.slug, chapter, payload, actor=actor)
        if isinstance(result, tuple):
            log.error("chapter %d: save failed %s", chapter, result)
            failed += 1
            continue
        ops = payload["operations"]
        _await_visible(read_history, [op["op_id"] for op in ops], f"ch {chapter} history")
        log.info("chapter %d: %d trims saved", chapter, len(ops))
    cache.pop_seg_caches_affected_by_segment_edit(args.slug)
    log.info("DONE: %d chapters, %d failed", len({g.chapter for g in gaps}), failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
