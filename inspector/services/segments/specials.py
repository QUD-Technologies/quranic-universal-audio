"""Read a reciter's ``specials.json`` and record a maintainer's voice call on one entry.

The automatic Basmala voice check leaves a ``needs_confirmation`` band; a holder
of ``segments.confirm_basmala_voice`` settles it (or overrules a verdict) here.
The call is stored as ``voice_confirmed`` on the entry — it wins over the
automatic verdict wherever ``same_voice`` is read — audited, and, when it
changes the answer, the reciter's published GH member is stamped stale so the
next cut ships the new ``same_voice``.

A special a reviewer labels in ``detailed.json`` (Edit Ref → ``Basmala`` …) is
mirrored here as source ``in_detailed`` on every persist, so timing and the
release pick it up like an aligner-recorded one.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from qua_shared.schemas import Actor
from qua_shared.schemas.bucket.specials import SpecialEntry, SpecialsDoc
from qua_shared.schemas.wire.release import StaleReason
from services.db import repo_releases
from services.db import sync as _sync
from services.segments.specials_doc import edge_entry, verse_bounds
from services.state import audit
from services.storage import data_dir

log = logging.getLogger(__name__)

#: Specials mirrored from ``detailed.json`` rows; re-derived on every persist.
IN_DETAILED = "in_detailed"

#: Serialises read-modify-write of any reciter's specials.json (writes are rare).
WRITE_LOCK = threading.Lock()


def read_doc(slug: str) -> SpecialsDoc | None:
    raw = data_dir.read_specials_doc(slug)
    return SpecialsDoc.model_validate(raw) if raw is not None else None


def confirm_voice(
    slug: str, *, chapter: int, start_ms: int, same_voice: bool, actor: Actor
) -> bool:
    """Store the call on the entry starting at ``start_ms``; ``False`` when there is no such entry."""
    with WRITE_LOCK:
        doc = read_doc(slug)
        if doc is None:
            return False
        entries = doc.chapters.get(str(chapter), [])
        entry = next((e for e in entries if e.start_ms == start_ms), None)
        if entry is None:
            return False
        before = entry.voice_confirmed
        entry.voice_confirmed = same_voice
        data_dir.write_specials_doc(slug, doc.model_dump(mode="json"))
    with _sync.durable_transaction():
        audit.append(
            event="specials.voice_confirmed",
            actor=actor,
            slug=slug,
            payload={
                "chapter": chapter,
                "start_ms": start_ms,
                "type": entry.type,
                "verdict": entry.voice_verdict,
                "before": before,
                "after": same_voice,
            },
        )
        if before != same_voice:
            # Only the GH cut ships specials.json, so only its member goes stale.
            repo_releases.stamp_stale(
                slug, at=datetime.now(UTC), reason=StaleReason.CATALOG_EDIT, tracks=("gh",)
            )
    return True


def _detailed_entries(segments: list[dict], chapter: int) -> list[SpecialEntry]:
    bounds = verse_bounds(segments, chapter)
    if bounds is None:
        return []
    found = (edge_entry(seg, bounds, source=IN_DETAILED) for seg in segments)
    return [e for e in found if e is not None]


def _same_span(a: SpecialEntry, b: SpecialEntry) -> bool:
    return (a.type, a.position, a.start_ms, a.end_ms) == (b.type, b.position, b.start_ms, b.end_ms)


def _merge_chapter(old: list[SpecialEntry], labelled: list[SpecialEntry]) -> list[SpecialEntry]:
    """``old`` with its ``in_detailed`` entries replaced by ``labelled``.

    A labelled row supersedes a recorded entry of the same type and position (the
    reviewer re-timed it); one already timed keeps its words.
    """
    kept = [
        e
        for e in old
        if e.source == "fatiha_verse"
        or (
            e.source != IN_DETAILED
            and not any(n.type == e.type and n.position == e.position for n in labelled)
        )
    ]
    for new in labelled:
        prior = next((e for e in old if _same_span(e, new) and e.words), None)
        if prior is not None:
            new.words = list(prior.words)
    return sorted(kept + labelled, key=lambda e: e.start_ms)


def sync_from_detailed(
    slug: str, segments_by_chapter: dict[int, list[dict]], *, generated_at: str
) -> bool:
    """Mirror each chapter's labelled special rows into ``specials.json``; ``True`` if it changed."""
    with WRITE_LOCK:
        doc = read_doc(slug) or SpecialsDoc(generated_at=generated_at, chapters={})
        changed = False
        for chapter, segments in segments_by_chapter.items():
            old = doc.chapters.get(str(chapter), [])
            labelled = _detailed_entries(segments, chapter)
            if not labelled and all(e.source != IN_DETAILED for e in old):
                continue
            merged = _merge_chapter(old, labelled)
            if merged == old:
                continue
            changed = True
            if merged:
                doc.chapters[str(chapter)] = merged
            else:
                doc.chapters.pop(str(chapter), None)
        if changed:
            data_dir.write_specials_doc(slug, doc.model_dump(mode="json"))
    return changed
