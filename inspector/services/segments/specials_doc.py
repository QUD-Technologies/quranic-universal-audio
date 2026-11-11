"""Build a reciter's ``specials.json`` from an align run.

The pipeline strips every special row before ``detailed.json`` is written and
records each as a ``delete_segment`` audit event; those events carry the span,
name and confidence, so they are the run's record of its specials. A special
before ayah 1 is ``pre`` (clipped at ayah 1 if it ran into it), one after the
last ayah is ``post``; a special inside the chapter (a repeated take) is not a
chapter edge and is left out. In editions that number Al-Fatiha's Basmala as
1:1, that verse is also listed here — it stays a verse — carrying the
aligner's ``basmala_voice`` verdict on it when the run asked for one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import cast, get_args

from qua_shared.schemas.bucket.specials import (
    SpecialEntry,
    SpecialsDoc,
    SpecialSource,
    SpecialType,
    VoiceVerdict,
)
from qua_shared.schemas.bucket.staged_run import PipelineAuditEvent

SPECIAL_NAMES = frozenset(get_args(SpecialType))
_VERDICTS = set(get_args(VoiceVerdict))
_VERSE = re.compile(r"^(\d+):(\d+):\d+")
#: SDK riwayah slugs whose Al-Fatiha numbers the Basmala as verse 1:1.
NUMBERED_BASMALA = frozenset({"hafs", "shuba"})
#: A special may end this far past ayah 1's start and still count as before it
#: (the strip and the verse row share a boundary pad).
EDGE_TOL_MS = 300


def verse_bounds(segments: list[dict], chapter: int) -> tuple[int, int] | None:
    """``(first verse start, last verse end)`` of ``chapter``'s verse rows; ``None`` without any."""
    spans = [
        (s["time_start"], s["time_end"])
        for s in segments
        if (m := _VERSE.match(s.get("matched_ref") or "")) and int(m.group(1)) == chapter
    ]
    if not spans:
        return None
    return min(a for a, _ in spans), max(b for _, b in spans)


def edge_entry(row: dict, bounds: tuple[int, int], *, source: SpecialSource) -> SpecialEntry | None:
    """``row`` (``matched_ref``/``time_start``/``time_end``/``confidence``) as a chapter-edge
    special, or ``None`` when it is not a special or sits inside the chapter."""
    name = row.get("matched_ref")
    if name not in SPECIAL_NAMES:
        return None
    first, last = bounds
    start, end = row["time_start"], row["time_end"]
    if end <= first + EDGE_TOL_MS:
        position, clipped = "pre", False
    elif start < first:  # ran into ayah 1 (a swallowed muqattaʿ): keep the part before it
        position, clipped, end = "pre", True, first
    elif start >= last - EDGE_TOL_MS:
        position, clipped = "post", False
    else:
        return None
    if end <= start:
        return None
    return SpecialEntry(
        type=cast(SpecialType, name),
        position=position,
        start_ms=start,
        end_ms=end,
        confidence=row.get("confidence"),
        source=source,
        clipped=clipped,
    )


def fatiha_basmala(segments: list[dict], voice: dict | None) -> SpecialEntry | None:
    row = next((s for s in segments if (s.get("matched_ref") or "") == "1:1:1-1:1:4"), None)
    if row is None:
        return None
    # ``unavailable`` (no isolated Basmala, too little reference audio) is no verdict.
    voice = voice or {}
    decided = voice.get("verdict") in _VERDICTS
    return SpecialEntry(
        type="Basmala",
        start_ms=row["time_start"],
        end_ms=row["time_end"],
        confidence=row.get("confidence"),
        source="fatiha_verse",
        voice_verdict=voice.get("verdict") if decided else None,
        voice_z=voice.get("z") if decided else None,
    )


def specials_from_run(
    events: Iterable[PipelineAuditEvent],
    by_chapter: dict[int, dict],
    *,
    generated_at: str,
    riwayah: str,
    voice: dict | None = None,
) -> SpecialsDoc | None:
    """The run's ``SpecialsDoc``, or ``None`` when it recorded no special.

    ``voice`` is the aligner's ``basmala_voice`` result for Al-Fatiha, if any.
    """
    chapters: dict[int, list[SpecialEntry]] = {}
    for ev in events:
        if ev.kind != "delete_segment" or not ev.targets_before or ev.chapter not in by_chapter:
            continue
        bounds = verse_bounds(by_chapter[ev.chapter]["segments"], ev.chapter)
        snap = ev.targets_before[0].model_dump()
        entry = bounds and edge_entry(snap, bounds, source="align")
        if entry:
            chapters.setdefault(ev.chapter, []).append(entry)
    if riwayah in NUMBERED_BASMALA and 1 in by_chapter:
        fatiha = fatiha_basmala(by_chapter[1]["segments"], voice)
        if fatiha:
            chapters.setdefault(1, []).append(fatiha)
    if not chapters:
        return None
    return SpecialsDoc(
        generated_at=generated_at,
        chapters={
            str(ch): sorted(items, key=lambda s: s.start_ms)
            for ch, items in sorted(chapters.items())
        },
    )
