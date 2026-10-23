"""Project a bucket ``specials.json`` into the public release ``specials.json``.

Only openers are released, one per type per chapter: when a chapter holds
several (a repeated Basmala) the earliest confident one is the chapter's.
"""

from __future__ import annotations

from collections.abc import Iterable

from qua_shared.schemas.bucket.specials import SpecialEntry, SpecialsDoc
from qua_shared.schemas.wire.release import (
    ReleaseChapterSpecials,
    ReleaseDefaultBasmala,
    ReleaseSpecial,
    ReleaseSpecialsDoc,
)

_RELEASE_KEY = {"Isti'adha": "istiadha", "Basmala": "basmala"}


def _release_special(entry: SpecialEntry) -> ReleaseSpecial:
    return ReleaseSpecial(
        start_ms=entry.start_ms,
        end_ms=entry.end_ms,
        same_voice=entry.same_voice,
        words=[[n, a, b] for n, (a, b) in enumerate(entry.words, start=1)],
    )


def _chapter_openers(items: list[SpecialEntry]) -> dict[str, SpecialEntry]:
    picked: dict[str, SpecialEntry] = {}
    for entry in items:
        key = _RELEASE_KEY.get(entry.type)
        if key and entry.position == "pre" and key not in picked:
            picked[key] = entry
    return picked


def default_basmala(doc: SpecialsDoc, chapters: Iterable[int]) -> ReleaseDefaultBasmala | None:
    """The cleanest own-voice Basmala among ``chapters``: confirmed same voice,
    not clipped, highest confidence, then the duration closest to the median."""
    candidates = [
        (ch, entry)
        for ch in chapters
        for entry in _chapter_openers(doc.chapters.get(str(ch), [])).values()
        if entry.type == "Basmala" and entry.same_voice is True and not entry.clipped
    ]
    if not candidates:
        return None
    durations = sorted(e.end_ms - e.start_ms for _, e in candidates)
    median = durations[len(durations) // 2]
    ch, best = max(
        candidates,
        key=lambda c: (c[1].confidence or 0.0, -abs((c[1].end_ms - c[1].start_ms) - median), -c[0]),
    )
    return ReleaseDefaultBasmala(chapter=ch, start_ms=best.start_ms, end_ms=best.end_ms)


def project_release(doc: SpecialsDoc, slug: str, chapters: Iterable[int]) -> ReleaseSpecialsDoc:
    """``chapters`` = the chapters the release ships (excluded chapters left out)."""
    shipped = sorted(set(chapters))
    out: dict[str, ReleaseChapterSpecials] = {}
    for ch in shipped:
        openers = _chapter_openers(doc.chapters.get(str(ch), []))
        if openers:
            out[str(ch)] = ReleaseChapterSpecials(**{k: _release_special(e) for k, e in openers.items()})
    return ReleaseSpecialsDoc(slug=slug, chapters=out, default_basmala=default_basmala(doc, shipped))
