"""Apply the align pipeline's verse-end verdicts (``verse_ends_v1.json``) to a published delivery.

Each entry names a segment (by uid, with the bounds it had when judged) and its verse
ends read WAQF or WASL without review (``align_pipeline.pause_sidecar``). Per segment,
one op through the same full-replace save a reviewer's edit takes, logged with
``op_context_category`` ``cross_verse`` and ``fix_kind`` ``auto_fix``:

* any WAQF: ``split_segment`` at the WAQF cursors, every new boundary WAQF, the WASL
  answers stored on the piece holding their word;
* WASL only: ``ignore_issue`` storing the answers, settled for ``cross_verse`` when
  every verse end inside the segment is answered.

A segment that is gone, moved, already answered, or whose cursors do not fall inside
it is skipped, so a re-run applies nothing twice.
Chapters save one at a time, each waiting until its history reads back.
"""

from __future__ import annotations

import copy
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable

from qua_shared.schemas import Actor

log = logging.getLogger("inspector")

CATEGORY = "cross_verse"
FIX_KIND = "auto_fix"
WAQF = "waqf"
WASL = "wasl"
VISIBLE_TIMEOUT_S = 300
VISIBLE_POLL_S = 3


def _snapshot(seg: dict, index: int, chapter: int, entry: dict) -> dict:
    """The frontend's ``snapshotSeg`` for a detailed.json segment."""
    snap = {
        "segment_uid": seg.get("segment_uid"),
        "index_at_save": index,
        "audio_url": entry.get("audio") or None,
        "time_start": seg["time_start"],
        "time_end": seg["time_end"],
        "matched_ref": seg.get("matched_ref") or "",
        "confidence": seg.get("confidence") or 0,
        "entry_ref": entry.get("ref"),
        "chapter": chapter,
    }
    for key in ("wrap_word_ranges", "ignored_categories", "join_verdicts"):
        if seg.get(key):
            snap[key] = copy.deepcopy(seg[key])
    if seg.get("is_wasl"):
        snap["is_wasl"] = True
    return snap


def _answers(joins: list[dict]) -> list[dict]:
    return [{"after_ref": j["after_ref"], "at_ms": j["cursor_ms"], "verdict": WASL} for j in joins]


def _holds(ref: str, word: str) -> bool:
    from services.validation.join_answers import ref_key

    first, _, last = ref.partition("-")
    return ref_key(first) <= ref_key(word) < ref_key(last)


def _split(seg: dict, cuts: list[dict], wasl: list[dict], new_uid: Callable[[], str]) -> list[dict]:
    """``_reduceSplit`` at ``cuts``: piece 0 keeps the uid, new boundaries are WAQF, the
    last piece keeps the parent's ``is_wasl``; each WASL answer lands on its piece."""
    first, _, last = seg["matched_ref"].partition("-")
    bounds = [seg["time_start"], *(c["cursor_ms"] for c in cuts), seg["time_end"]]
    refs = [f"{a}-{b}" for a, b in zip([first, *(c["next_ref"] for c in cuts)],
                                      [*(c["after_ref"] for c in cuts), last], strict=True)]  # fmt: skip
    pieces = []
    for i, ref in enumerate(refs):
        piece = copy.deepcopy(seg)
        for key in ("wrap_word_ranges", "word_timings", "join_verdicts"):
            piece.pop(key, None)
        piece["time_start"], piece["time_end"] = bounds[i], bounds[i + 1]
        piece["matched_ref"] = ref
        if i:
            piece["segment_uid"] = new_uid()
        piece["is_wasl"] = bool(seg.get("is_wasl")) if i == len(refs) - 1 else False
        held = [a for a in _answers(wasl) if _holds(ref, a["after_ref"])]
        if held:
            piece["join_verdicts"] = held
        pieces.append(piece)
    return pieces


def _answered(seg: dict, wasl: list[dict], verse_ends: list[str]) -> dict:
    out = copy.deepcopy(seg)
    out.pop("word_timings", None)
    verdicts = [*(out.get("join_verdicts") or []), *_answers(wasl)]
    out["join_verdicts"] = sorted(verdicts, key=lambda j: (j["at_ms"], j["after_ref"]))
    answered = {j["after_ref"] for j in verdicts}
    if CATEGORY not in (out.get("ignored_categories") or []) and set(verse_ends) <= answered:
        out["ignored_categories"] = [*(out.get("ignored_categories") or []), CATEGORY]
        out["confidence"] = 1.0
    return out


def _op(
    op_type: str, before: list[dict], after: list[dict], chapter: int, uid: Callable[[], str]
) -> dict:
    inserted = [s["segment_uid"] for s in after[1:]] if op_type == "split_segment" else []
    return {
        "op_id": uid(),
        "op_type": op_type,
        "op_context_category": CATEGORY,
        "fix_kind": FIX_KIND,
        "targets_before": before,
        "targets_after": after,
        "affected_chapters": [chapter],
        "patch": {
            "before": before,
            "after": after,
            "removedIds": [],
            "insertedIds": inserted,
            "affectedChapterIds": [chapter],
        },  # fmt: skip
    }


def _fits(seg: dict, item: dict, verse_ends: list[str]) -> str | None:
    """Why ``item`` no longer fits ``seg``, or ``None``."""
    if (seg["time_start"], seg["time_end"]) != (item["start_ms"], item["end_ms"]):
        return "moved"
    cursors = [j["cursor_ms"] for j in item["joins"]]
    if not all(seg["time_start"] < c < seg["time_end"] for c in cursors) or cursors != sorted(
        set(cursors)
    ):
        return "cursor_outside"
    if not all(j["after_ref"] in verse_ends for j in item["joins"]):
        return "ref_moved"
    answered = {j.get("after_ref") for j in seg.get("join_verdicts") or []}
    if all(j["after_ref"] in answered for j in item["joins"]):
        return "already_answered"
    return None


def chapter_save(
    entries: list[dict],
    chapter: int,
    by_uid: dict[str, dict],
    verse_ends_of: Callable[[str], list[str]],
    chapter_of: Callable[[str], int],
    uid: Callable[[], str],
    tally: Counter,
) -> dict:
    """The full-replace save payload for ``chapter`` with its verdicts applied."""
    segments: list[dict] = []
    operations: list[dict] = []
    for entry in entries:
        if chapter_of(entry["ref"]) != chapter:
            continue
        for seg in entry.get("segments", []):
            item = by_uid.get(seg.get("segment_uid") or "")
            ends = verse_ends_of(seg.get("matched_ref") or "") if item else []
            skip = _fits(seg, item, ends) if item else None
            if item is None or skip:
                if skip:
                    tally[skip] += 1
                segments.append({**seg, "audio_url": entry.get("audio") or ""})
                continue
            index = len(segments)
            cuts = [j for j in item["joins"] if j["verdict"] == WAQF]
            wasl = [j for j in item["joins"] if j["verdict"] == WASL]
            before = [_snapshot(seg, index, chapter, entry)]
            if cuts:
                after_segs = _split(seg, cuts, wasl, uid)
                op_type = "split_segment"
            else:
                after_segs = [_answered(seg, wasl, ends)]
                op_type = "ignore_issue"
            after = [_snapshot(s, index + i, chapter, entry) for i, s in enumerate(after_segs)]
            operations.append(_op(op_type, before, after, chapter, uid))
            segments.extend({**s, "audio_url": entry.get("audio") or ""} for s in after_segs)
            tally[WAQF] += len(cuts)
            tally[WASL] += len(wasl)
    return {"full_replace": True, "segments": segments, "operations": operations}


def apply(slug: str, by_uid: dict[str, dict], actor: Actor) -> dict[str, int]:
    """Apply ``by_uid`` (``verse_ends_v1`` entries) to ``slug``; returns the tally."""
    from services.reference.delivery_edition import sdk_riwayah_for
    from services.segments.save import save_seg_data
    from services.storage import cache, data_dir
    from services.storage.data_loader import get_word_counts, load_detailed
    from services.validation.detail import _verse_end_refs
    from utils.references import chapter_from_ref
    from utils.uuid7 import uuid7

    if not by_uid:
        return {}
    word_counts = get_word_counts(sdk_riwayah_for(slug))
    tally: Counter[str] = Counter()
    chapters: dict[int, dict[str, dict]] = defaultdict(dict)
    for uid, item in by_uid.items():
        chapters[int(item["chapter"])][uid] = item
    for chapter, items in sorted(chapters.items()):
        cache.pop_seg_caches_affected_by_segment_edit(slug)
        payload = chapter_save(
            load_detailed(slug), chapter, items,
            lambda ref: _verse_end_refs(ref, word_counts), chapter_from_ref, uuid7, tally,
        )  # fmt: skip
        if not payload["operations"]:
            continue
        result = save_seg_data(slug, chapter, payload, actor=actor)
        if isinstance(result, tuple):
            raise RuntimeError(f"{slug} ch {chapter}: verse-end save failed {result}")
        _await_history(slug, [op["op_id"] for op in payload["operations"]], data_dir)
        tally["segments"] += len(payload["operations"])
    cache.pop_seg_caches_affected_by_segment_edit(slug)
    cache.pop_seg_split_group_index(slug)
    log.info("verse ends %s: %s", slug, dict(tally))
    return dict(tally)


def _await_history(slug: str, op_ids: list[str], data_dir) -> None:
    deadline = time.monotonic() + VISIBLE_TIMEOUT_S
    while True:
        try:
            raw = data_dir.get_backend().read_bytes(data_dir.edit_history_path(slug)) or b""
        except Exception:  # noqa: BLE001 — absent or mid-write; the poll retries
            raw = b""
        if all(op.encode() in raw for op in op_ids):
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"{slug}: verse-end history not visible after {VISIBLE_TIMEOUT_S}s")
        time.sleep(VISIBLE_POLL_S)
