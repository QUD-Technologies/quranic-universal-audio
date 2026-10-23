"""Time a chapter's openers with its segments, and keep their word times in specials.json.

The Basmala / Isti'adha before ayah 1 are stripped from ``detailed.json`` and live in
``specials.json``. A timing call puts them back in front of the chapter's segments
under ``specials: true``, so the aligner times them, builds each its own shard
reading, and returns their words; those words are stored on the specials entries.
Al-Fatiha's 1:1 is a verse and is timed as one, so it is never sent from here.
"""

from __future__ import annotations

import json
import logging

import brotli

from domain.identity import derive_uid
from qua_shared.schemas.bucket.specials import SpecialsDoc
from services.segments import specials as specials_service
from services.segments.specials_doc import SPECIAL_NAMES
from services.storage import data_dir

log = logging.getLogger(__name__)

#: The openers the aligner times and gives a shard reading.
OPENERS = ("Isti'adha", "Basmala")
#: Keeps an opener's derived uid clear of every real segment index in its chapter.
_UID_INDEX_BASE = 1_000_000


def opener_segments(slug: str, chapter: int) -> list[dict]:
    """The chapter's recorded openers as timing segments, in time order."""
    doc = specials_service.read_doc(slug)
    entries = doc.chapters.get(str(chapter), []) if doc else []
    return [
        {
            "segment_uid": derive_uid(chapter, _UID_INDEX_BASE + i, e.start_ms),
            "matched_ref": e.type,
            "confidence": e.confidence or 1.0,
            "time_start": e.start_ms,
            "time_end": e.end_ms,
        }
        for i, e in enumerate(entries)
        if e.type in OPENERS and e.position == "pre" and e.source != "fatiha_verse"
    ]


def with_openers(entries: list[dict], openers: list[dict]) -> list[dict]:
    """``entries`` without their labelled special rows, the openers in front.

    A row a reviewer labelled ``Basmala`` … is not a verse; at a chapter edge it
    is already mirrored into ``specials.json`` and comes back as an opener.
    """
    verses = [
        {
            **e,
            "segments": [
                s for s in e.get("segments", []) if s.get("matched_ref") not in SPECIAL_NAMES
            ],
        }
        for e in entries
    ]
    if not openers:
        return verses
    first, *rest = verses
    return [{**first, "segments": openers + first["segments"]}, *rest]


def store_words(slug: str, chapter: int, openers: list[dict], times: bytes) -> int:
    """Write each timed opener's word intervals (chapter ms) onto its specials entry."""
    stored = json.loads(brotli.decompress(times)).get("segments") or {}
    words_at: dict[int, list[tuple[int, int]]] = {}
    for seg in openers:
        held = stored.get(seg["segment_uid"])
        if not held or held.get("status") != "ok":
            continue
        start = seg["time_start"]
        words_at[start] = [
            (start + s, start + e)
            for _, s, e, *_ in held["words"]
            if s is not None and e is not None
        ]
    if not words_at:
        return 0
    with specials_service.WRITE_LOCK:
        doc = specials_service.read_doc(slug)
        if doc is None:
            return 0
        for entry in doc.chapters.get(str(chapter), []):
            if entry.type in OPENERS and entry.start_ms in words_at:
                entry.words = words_at[entry.start_ms]
        doc = SpecialsDoc.model_validate(doc.model_dump())  # re-check spans after the edit
        data_dir.write_specials_doc(slug, doc.model_dump(mode="json"))
    return len(words_at)


def verse_failures(slug: str, chapter: int, reply: dict) -> list[dict]:
    """The reply's failed segments without its openers: an opener that cannot be timed
    loses its shard reading, it does not hold back the chapter's verses."""
    failed = reply.get("failed_segments") or []
    openers = [f for f in failed if f.get("special")]
    if openers:
        log.warning("[timing] %s ch%s: opener(s) not timed: %s", slug, chapter, openers)
    return [f for f in failed if not f.get("special")]
