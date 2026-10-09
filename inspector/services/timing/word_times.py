"""Stored segment times read back as word intervals for the segment cards.

A stored entry is shown only while it still describes its segment: same uid, same span
and the same timed ref (``source_ref`` before ``matched_ref``, as the aligner times it).
Intervals are chapter-audio ms, the clock segment playback runs on.
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


def chapter_word_times(entries: list[dict], doc: dict | None) -> dict[str, list[dict]]:
    """``{segment uid: [{location, start_ms, end_ms}, ...]}`` for ``entries``' segments
    whose stored times are still theirs."""
    if not doc:
        return {}
    stored = doc.get("segments") or {}
    out: dict[str, list[dict]] = {}
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
            out[seg["segment_uid"]] = _absolute(held["words"], start)
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
