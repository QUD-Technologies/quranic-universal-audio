"""``missed_waqf_v2.json`` (Low Confidence Waqf) and ``verse_ends_v1.json`` from the
aligner's lattice pauses and the chapter's loudness levels.

The aligner reports the stops its phoneme lattice heard inside a segment (``pauses``)
and never cuts on them. Every join a segment holds is judged here by the boundary-head
lab's rules (``hidden_pause.sidecar`` / ``hidden_pause.cross_verse``), with the silence
measured at the join (:mod:`.join_silence`):

* **Mid-verse stop** (a lattice pause): asked when the reciter went quiet
  (``MIN_SILENCE_MS`` under the speech level, ``MIN_DIP_DB`` deep) and either the
  mushaf marks a pause after the word (ۖ ۗ ۘ ۚ) or the silence reaches the noise floor
  for ``MIN_FLOOR_MS``; a non-Hafs join also needs ``MIN_EDITION_FLOOR_MS`` of floor.
  A chapter without levels asks every pause.
* **Verse end** inside a segment: the lattice (paused or not) and ``VERSE_END_FLOOR_MS``
  of floor silence each read WAQF or WASL; when they agree the verdict goes to
  ``verse_ends_v1`` and is applied after publish (``services.segments.verse_end_verdicts``),
  otherwise it is asked. Repetition segments, low-confidence segments and chapters
  without levels are always asked.

Non-Hafs rows carry edition refs but Hafs word timings and pauses (the aligner decodes
on Hafs): each join is projected onto the edition's words (``hidden_pause.edition``),
verse ends are judged on the edition's numbering, and cuts and verdicts are written in
edition refs; the pause mark is read off the Hafs word. A join the edition does not
have (inside a merged word, or a word it does not recite) is skipped.

A cut sits at the middle of the silence (else the midpoint between the two words'
timings). Items are keyed by ``derive_uid(chapter, index, time_start)`` over the
chapter's kept rows, share the ``missed_waqf_v1`` item shape with a single ``lattice``
axis, and ``gap_ms`` carries the floor silence.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH

from . import adapt
from .join_silence import ChapterLevels, Silence
from .params import AUTO_SPLIT_TIMING_SOURCE

log = logging.getLogger("inspector")

SIDECAR_FILE = "missed_waqf_v2.json"
VERSE_ENDS_FILE = "verse_ends_v1.json"
KIND = "missed_waqf"
AXIS = "lattice"
WAQF = "waqf"
WASL = "wasl"
#: ``score`` is ``gain`` in thousandths plus the floor silence (capped), as an int.
SCORE_SCALE = 1000
SCORE_SILENCE_CAP_MS = 999
MIN_SILENCE_MS = 40
MIN_DIP_DB = 10
MIN_FLOOR_MS = 160
MIN_EDITION_FLOOR_MS = 120
VERSE_END_FLOOR_MS = 200
#: The aligner reports no pause past its lattice cost ceiling (0.3); confidence is 1 - cost.
MIN_CONFIDENCE = 0.7
PAUSE_MARKS = frozenset("ۖۗۘۚ")


def _ref_key(ref: str) -> tuple[int, ...] | None:
    try:
        parts = tuple(int(p) for p in str(ref).split(":"))
    except ValueError:
        return None
    return parts if len(parts) == 3 else None


def _verse_end(a: str, b: str) -> bool:
    ka, kb = _ref_key(a), _ref_key(b)
    return ka is not None and kb is not None and ka[:2] != kb[:2]


def _midpoint_ms(prev_word: dict, next_word: dict) -> int | None:
    end, start = prev_word.get("end"), next_word.get("start")
    if end is None or start is None:
        return None
    return adapt.to_ms((float(end) + float(start)) / 2.0)


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


Project = Callable[[str, bool], "str | None"]


def _projector(riwayah: str) -> Project:
    """Hafs word -> the delivery edition's first / last word for it (identity on Hafs)."""
    if riwayah == DEFAULT_SDK_RIWAYAH:
        return lambda ref, _last: ref
    from services.reference import editions

    projection = editions.projection(riwayah)

    def to_edition(ref: str, last: bool) -> str | None:
        try:
            targets = projection.relation_for_source(ref).target_refs
        except (KeyError, ValueError):
            return None
        return (targets[-1] if last else targets[0]) if targets else None

    return to_edition


def _join_refs(words: list[dict], i: int, project: Project) -> tuple[str, str] | None:
    """The join after word ``i`` in the delivery's refs, or ``None`` when it has none."""
    after = project(str(words[i].get("location")), True)
    nxt = project(str(words[i + 1].get("location")), False)
    return (after, nxt) if after and nxt and after != nxt else None


def _joins(
    words: list[dict], pauses: list[dict], tally: dict, project: Project
) -> list[tuple[int, dict | None]]:
    """``(word index, pause or None)`` for every lattice pause and verse end, in order."""
    held: dict[int, dict | None] = {}
    search_from = 0
    for pause in pauses:
        tally["pauses"] += 1
        index = _join_index(words, pause["after_ref"], search_from)
        if index is None:
            tally["untimed"] += 1
            continue
        search_from = index + 1
        held[index] = pause
    for i in range(len(words) - 1):
        refs = _join_refs(words, i, project)
        if refs and _verse_end(*refs):
            held.setdefault(i, None)
    return sorted(held.items(), key=lambda kv: kv[0])


def keep_stop(silence: Silence | None, marked: bool, hafs: bool) -> bool:
    """Whether a mid-verse lattice pause is asked (see module doc)."""
    if silence is None:
        return True
    if silence.silence_ms < MIN_SILENCE_MS or silence.dip_db < MIN_DIP_DB:
        return False
    if not hafs and silence.floor_ms < MIN_EDITION_FLOOR_MS:
        return False
    return marked or silence.floor_ms >= MIN_FLOOR_MS


def verse_end_verdict(paused: bool, silence: Silence | None) -> str | None:
    """``WAQF`` / ``WASL`` when the lattice and the floor silence agree, else ``None``."""
    if silence is None:
        return None
    stopped = silence.floor_ms >= VERSE_END_FLOOR_MS
    if paused == stopped:
        return WAQF if paused else WASL
    return None


def _cut(pause, refs, cursor, word, silence, verse_end) -> dict:
    gain = float(pause["gain"]) if pause else 0.0
    quiet = silence.floor_ms if silence else 0
    lattice = {"paused": pause is not None, "after_ref": refs[0], "next_ref": refs[1]}
    if pause:
        lattice |= {"gain": pause["gain"], "separability": pause["separability"]}
    if silence:
        lattice |= {"floor_ms": silence.floor_ms, "silence_ms": silence.silence_ms,
                    "dip_db": silence.dip_db}  # fmt: skip
    return {
        "cursor_ms": cursor,
        "axes": [AXIS],
        "gap_ms": quiet,
        "score": round(gain * SCORE_SCALE) + min(quiet, SCORE_SILENCE_CAP_MS),
        "word": word,
        "verse_end": verse_end,
        "evidence": {AXIS: lattice},
    }


def item_for(
    chapter: int,
    seg: dict,
    row: dict,
    riwayah: str,
    tally: dict,
    levels: ChapterLevels | None = None,
    project: Project | None = None,
) -> tuple[dict | None, dict | None]:
    """The asked item and the verse-end verdicts for a published ``seg`` (its aligner ``row``)."""
    from services.reference.quran_refs import dk_text_for_ref

    raw_words = row.get("words")
    words: list[dict] = raw_words if isinstance(raw_words, list) else []
    hafs = riwayah == DEFAULT_SDK_RIWAYAH
    project = project or _projector(riwayah)
    judged = not seg.get("wrap_word_ranges") and (row.get("confidence") or 0) >= MIN_CONFIDENCE
    answered = {j.get("after_ref") for j in seg.get("join_verdicts") or []}
    cuts: list[dict] = []
    pieces: list[tuple[str, str]] = []
    verdicts: list[dict] = []
    last = seg["time_start"]
    for index, pause in _joins(words, row.get("pauses") or [], tally, project):
        prev_word, next_word = words[index], words[index + 1]
        refs = _join_refs(words, index, project)
        if refs is None:
            tally["unprojected"] += 1
            continue
        after_ref, next_ref = refs
        verse_end = _verse_end(after_ref, next_ref)
        tally["verse_end" if verse_end else "mid_verse"] += 1
        relative = _midpoint_ms(prev_word, next_word)
        cursor = None if relative is None else seg["time_start"] + relative
        if cursor is None or not last < cursor < seg["time_end"]:
            tally["untimed"] += 1
            continue
        if after_ref in answered:
            tally["answered"] += 1
            continue
        silence = levels.measure(cursor, seg["time_start"], seg["time_end"]) if levels else None
        if silence and last < silence.at_ms < seg["time_end"]:
            cursor = silence.at_ms
        word = dk_text_for_ref(f"{after_ref}-{after_ref}", riwayah)
        hafs_word = str(prev_word["location"])
        marked = bool(
            PAUSE_MARKS & set(dk_text_for_ref(f"{hafs_word}-{hafs_word}", DEFAULT_SDK_RIWAYAH))
        )
        if verse_end:
            verdict = verse_end_verdict(pause is not None, silence) if judged else None
            if verdict:
                tally[verdict] += 1
                verdicts.append({"after_ref": after_ref, "next_ref": next_ref, "verdict": verdict,
                                 "cursor_ms": cursor})  # fmt: skip
                last = cursor
                continue
        elif not keep_stop(silence, marked, hafs):
            tally["dropped"] += 1
            continue
        last = cursor
        cuts.append(_cut(pause, refs, cursor, word, silence, verse_end))
        pieces.append((after_ref, next_ref))
    item = None
    if cuts:
        refs = None if seg.get("wrap_word_ranges") else _pieces(seg["matched_ref"], pieces)
        item = {
            "kind": KIND,
            "chapter": chapter,
            "cursors": [c["cursor_ms"] for c in cuts],
            "refs": refs,
            "score": max(c["score"] for c in cuts),
            "cuts": cuts,
        }
    applied = None
    if verdicts:
        applied = {"chapter": chapter, "start_ms": seg["time_start"], "end_ms": seg["time_end"],
                   "joins": verdicts}  # fmt: skip
    return item, applied


def build(
    reciter: str,
    docs: dict[int, dict],
    sources: dict[int, str],
    riwayah: str,
    live_entries: list[dict] | None = None,
) -> tuple[dict, dict]:
    """The ``missed_waqf_v2`` and ``verse_ends_v1`` docs for the staged aligner results ``docs``."""
    from collections import Counter

    from domain.identity import derive_uid

    tally: Counter[str] = Counter()
    by_uid: dict[str, dict] = {}
    applied: dict[str, dict] = {}
    unmeasured: list[int] = []
    project = _projector(riwayah)
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
        levels = ChapterLevels.load(reciter, chapter)
        if levels is None:
            unmeasured.append(chapter)
        kept = [r for r in docs[chapter].get("segments") or [] if not adapt.is_special(r)]
        for index, (seg, row) in enumerate(
            zip(candidate["entries"][0]["segments"], kept, strict=True)
        ):
            answers = reviewed.get(
                (chapter, seg["time_start"], seg["time_end"], seg["matched_ref"])
            )
            if answers:
                seg = {**seg, "join_verdicts": answers}
            item, verdicts = item_for(chapter, seg, row, riwayah, tally, levels, project)
            uid = derive_uid(chapter, index, seg["time_start"])
            if item is not None:
                by_uid[uid] = item
            if verdicts is not None:
                applied[uid] = verdicts
    if tally["untimed"]:
        log.warning("align %s: %d join(s) had no word timing, skipped", reciter, tally["untimed"])
    if unmeasured:
        log.warning("align %s: no levels for chapter(s) %s, every join asked", reciter, unmeasured)
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    missed = {
        "_meta": {
            "created_at": created,
            "reciter": reciter,
            "kind": KIND,
            "arms": {AXIS: {"source": "aligner_pauses+levels", "timing": AUTO_SPLIT_TIMING_SOURCE}},
            "segments": len(by_uid),
            "by_axes": {AXIS: len(by_uid)},
            "unmeasured_chapters": unmeasured,
            **dict(sorted(tally.items())),
        },
        "by_uid": dict(sorted(by_uid.items())),
    }
    verse_ends = {
        "_meta": {"created_at": created, "reciter": reciter, "segments": len(applied)},
        "by_uid": dict(sorted(applied.items())),
    }
    return missed, verse_ends
