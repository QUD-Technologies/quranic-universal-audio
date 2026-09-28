"""``missed_waqf_v2.json`` — Low Confidence Waqf items from the aligner's lattice pauses.

The aligner reports the stops its phoneme lattice heard inside a segment
(``pauses``) and never cuts on them. Every mid-verse stop becomes one proposed
cut the reviewer answers: WAQF splits there, WASL keeps the segment whole. A
stop at a verse end is left out — the verse boundary is not this review's
question.

The cursor is the midpoint between the end of the ``after_ref`` word and the
start of the next word in the row's word timings, the rule Auto Split uses for
its section boundaries. A pause on a row without word timings (or whose word
is not in them) gets no cursor and is counted in ``_meta.untimed``.

Items are keyed by the uid the published row derives —
``derive_uid(chapter, index, time_start)`` over the chapter's kept rows — and
share the ``missed_waqf_v1`` item shape with a single ``lattice`` axis.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from . import adapt
from .params import AUTO_SPLIT_TIMING_SOURCE

log = logging.getLogger("inspector")

SIDECAR_FILE = "missed_waqf_v2.json"
KIND = "missed_waqf"
AXIS = "lattice"
#: ``score`` is ``gain`` in thousandths, so the accordion sorts by it as an int.
SCORE_SCALE = 1000


def _ref_key(ref: str) -> tuple[int, ...] | None:
    try:
        parts = tuple(int(p) for p in str(ref).split(":"))
    except ValueError:
        return None
    return parts if len(parts) == 3 else None


def _same_verse(a: str, b: str) -> bool:
    ka, kb = _ref_key(a), _ref_key(b)
    return ka is not None and kb is not None and ka[:2] == kb[:2]


def _midpoint_ms(prev_word: dict, next_word: dict) -> int | None:
    end, start = prev_word.get("end"), next_word.get("start")
    if end is None or start is None:
        return None
    return adapt.to_ms((float(end) + float(start)) / 2.0)


def _gap_ms(prev_word: dict, next_word: dict) -> int:
    return max(0, adapt.to_ms(float(next_word["start"]) - float(prev_word["end"])))


def _pieces(matched_ref: str, joins: list[tuple[str, str]]) -> list[str] | None:
    """The refs the cursors cut ``matched_ref`` into, or ``None`` when they do not fit it."""
    start, _, end = matched_ref.partition("-")
    bounds = [start, *(ref for join in joins for ref in join), end]
    keys = [k for k in (_ref_key(r) for r in bounds) if k is not None]
    if len(keys) != len(bounds) or any(a > b for a, b in zip(keys, keys[1:], strict=False)):
        return None
    return [f"{bounds[i]}-{bounds[i + 1]}" for i in range(0, len(bounds), 2)]


def _join_index(words: list[dict], after_ref: str, start: int) -> int | None:
    """Index of the first word at or after ``start`` that is ``after_ref`` and has a successor."""
    for i in range(start, len(words) - 1):
        if words[i].get("location") == after_ref:
            return i
    return None


def _cut(pause: dict, prev_word: dict, next_word: dict, cursor: int, word: str) -> dict:
    after_ref, next_ref = prev_word["location"], next_word["location"]
    return {
        "cursor_ms": cursor,
        "axes": [AXIS],
        "gap_ms": _gap_ms(prev_word, next_word),
        "score": round(float(pause["gain"]) * SCORE_SCALE),
        "word": word,
        "verse_end": False,
        "evidence": {
            AXIS: {
                "gain": pause["gain"],
                "separability": pause["separability"],
                "after_ref": after_ref,
                "next_ref": next_ref,
            }
        },
    }


def item_for(chapter: int, seg: dict, row: dict, riwayah: str, tally: dict) -> dict | None:
    """One sidecar item for a published ``seg`` (its aligner ``row`` alongside), or ``None``."""
    from services.reference.quran_refs import dk_text_for_ref

    row_words = row.get("words")
    words = row_words if isinstance(row_words, list) else []
    cuts: list[dict] = []
    joins: list[tuple[str, str]] = []
    search_from = 0
    last_cursor = seg["time_start"]
    for pause in row.get("pauses") or []:
        tally["pauses"] += 1
        after_ref = pause["after_ref"]
        index = _join_index(words, after_ref, search_from)
        if index is None:
            tally["untimed"] += 1
            continue
        prev_word, next_word = words[index], words[index + 1]
        search_from = index + 1
        if not _same_verse(after_ref, str(next_word.get("location") or "")):
            tally["verse_end"] += 1
            continue
        relative = _midpoint_ms(prev_word, next_word)
        cursor = None if relative is None else seg["time_start"] + relative
        if cursor is None or not last_cursor < cursor < seg["time_end"]:
            tally["untimed"] += 1
            continue
        last_cursor = cursor
        if any(j["after_ref"] == after_ref for j in seg.get("join_verdicts") or []):
            tally["answered"] = tally.get("answered", 0) + 1
            continue
        word = dk_text_for_ref(f"{after_ref}-{after_ref}", riwayah)
        cuts.append(_cut(pause, prev_word, next_word, cursor, word))
        joins.append((after_ref, next_word["location"]))
    if not cuts:
        return None
    refs = None if seg.get("wrap_word_ranges") else _pieces(seg["matched_ref"], joins)
    return {
        "kind": KIND,
        "chapter": chapter,
        "cursors": [c["cursor_ms"] for c in cuts],
        "refs": refs,
        "score": max(c["score"] for c in cuts),
        "cuts": cuts,
    }


def build(
    reciter: str,
    docs: dict[int, dict],
    sources: dict[int, str],
    riwayah: str,
    live_entries: list[dict] | None = None,
) -> dict:
    """The whole ``missed_waqf_v2`` doc for the staged aligner results ``docs``."""
    from domain.identity import derive_uid

    tally = {"pauses": 0, "verse_end": 0, "untimed": 0}
    by_uid: dict[str, dict] = {}
    reviewed = {
        (int(e["ref"].split(":")[0]), s["time_start"], s["time_end"], s["matched_ref"]): s.get(
            "join_verdicts"
        )
        for e in live_entries or []
        for s in e.get("segments", [])
    }
    for chapter in sorted(docs):
        candidate, _events, _basmala = adapt.adapt_chapter(
            chapter, docs[chapter], source_url=sources[chapter], riwayah=riwayah
        )
        kept = [r for r in docs[chapter].get("segments") or [] if not adapt.is_special(r)]
        for index, (seg, row) in enumerate(
            zip(candidate["entries"][0]["segments"], kept, strict=True)
        ):
            if not row.get("pauses"):
                continue
            answers = reviewed.get(
                (chapter, seg["time_start"], seg["time_end"], seg["matched_ref"])
            )
            if answers:
                seg = {**seg, "join_verdicts": answers}
            item = item_for(chapter, seg, row, riwayah, tally)
            if item is not None:
                by_uid[derive_uid(chapter, index, seg["time_start"])] = item
    if tally["untimed"]:
        log.warning(
            "align %s: %d lattice pause(s) had no word timing, skipped", reciter, tally["untimed"]
        )
    return {
        "_meta": {
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reciter": reciter,
            "kind": KIND,
            "arms": {AXIS: {"source": "aligner_pauses", "timing": AUTO_SPLIT_TIMING_SOURCE}},
            "segments": len(by_uid),
            "by_axes": {AXIS: len(by_uid)},
            **tally,
        },
        "by_uid": dict(sorted(by_uid.items())),
    }
