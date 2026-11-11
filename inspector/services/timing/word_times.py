"""Stored segment times read back as word intervals for the segment cards.

A stored entry is shown only while it still describes its segment: same uid, same span
and the same timed ref (``source_ref`` before ``matched_ref``, as the aligner times it).
Intervals are chapter-audio ms, the clock segment playback runs on. Each segment carries
the span it was timed on, so the frontend can tell its cached copy is stale after a trim.

A projected delivery is timed on its Hafs ``source_ref`` while its cards show the
delivery's ``matched_ref``: when both ranges have the same number of words, the words are
relabelled in order; otherwise the segment gets no highlight.
"""

from __future__ import annotations

import json

import brotli

from services.storage import storage_paths
from services.storage.hf_bucket import StorageNotFound, get_backend

#: The stored-times schema this reader understands.
SCHEMA_VERSION = 1


def timing_ref(seg: dict) -> str | None:
    """The ref the aligner times ``seg`` with, or ``None`` for an untimed segment."""
    ref = seg.get("source_ref") or seg.get("matched_ref", "")
    if not ref or seg.get("confidence", 0) <= 0 or ":" not in ref:
        return None
    return ref


def read_doc(slug: str, chapter: int) -> dict | None:
    try:
        raw = get_backend().read_bytes(storage_paths.timing_path_br(slug, chapter))
    except StorageNotFound:
        return None
    doc = json.loads(brotli.decompress(raw))
    if (doc.get("_meta") or {}).get("schema_version") != SCHEMA_VERSION:
        return None
    return doc


def chapter_word_times(
    entries: list[dict],
    doc: dict | None,
    word_counts: dict[tuple[int, int], int] | None = None,
) -> dict[str, dict]:
    """``{segment uid: {start_ms, end_ms, words: [{location, start_ms, end_ms}]}}`` for
    ``entries``' segments whose stored times are still theirs. ``word_counts`` is the
    delivery edition's ``{(surah, ayah): words}``, needed to relabel a projected segment."""
    if not doc:
        return {}
    stored = doc.get("segments") or {}
    out: dict[str, dict] = {}
    for entry in entries:
        for seg in entry.get("segments", []):
            held = stored.get(seg.get("segment_uid") or "")
            start, end = int(seg.get("time_start", 0)), int(seg.get("time_end", 0))
            if (
                not held
                or held.get("status") != "ok"
                or held.get("span") != [start, end]
                or held.get("ref") != timing_ref(seg)
            ):
                continue
            words = _absolute(held["words"], start)
            shown = seg.get("matched_ref", "")
            if held["ref"] != shown:
                words = _relabel(words, shown, word_counts)
            if words:
                out[seg["segment_uid"]] = {"start_ms": start, "end_ms": end, "words": words}
    return out


def _absolute(words: list[list], start: int) -> list[dict]:
    """Words in chapter-audio ms; an untimed edge takes its neighbour's, so every word
    keeps its place in the row."""
    out, prev_end = [], start
    for location, s, e, *_ in words:
        s = prev_end - start if s is None else s
        e = s if e is None else e
        out.append({"location": location, "start_ms": start + s, "end_ms": start + e})
        prev_end = start + e
    return out


def _relabel(words: list[dict], ref: str, word_counts) -> list[dict]:
    locations = _locations(ref, word_counts) if word_counts else None
    if not locations or len(locations) != len(words):
        return []
    return [{**w, "location": loc} for w, loc in zip(words, locations, strict=True)]


def _locations(ref: str, word_counts: dict[tuple[int, int], int]) -> list[str] | None:
    """Every word location of ``s:a:w[-s:b:w]`` in order, or ``None`` for a malformed ref."""
    try:
        first, _, last = ref.partition("-")
        s1, a1, w1 = (int(x) for x in first.split(":"))
        s2, a2, w2 = (int(x) for x in (last or first).split(":"))
    except ValueError:
        return None
    if s1 != s2 or (a1, w1) > (a2, w2):
        return None
    out = []
    for ayah in range(a1, a2 + 1):
        lo = w1 if ayah == a1 else 1
        hi = w2 if ayah == a2 else word_counts.get((s1, ayah), 0)
        out.extend(f"{s1}:{ayah}:{w}" for w in range(lo, hi + 1))
    return out
