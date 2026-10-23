"""Read a reciter's ``specials.json`` and record a maintainer's voice call on one entry.

The automatic Basmala voice check leaves a ``needs_confirmation`` band; a holder
of ``segments.confirm_basmala_voice`` settles it (or overrules a verdict) here.
The call is stored as ``voice_confirmed`` on the entry — it wins over the
automatic verdict wherever ``same_voice`` is read — and audited.
"""

from __future__ import annotations

import threading

from qua_shared.schemas import Actor
from qua_shared.schemas.bucket.specials import SpecialsDoc
from services.state import audit
from services.storage import data_dir

#: Serialises read-modify-write of any reciter's specials.json (writes are rare).
_WRITE_LOCK = threading.Lock()


def read_doc(slug: str) -> SpecialsDoc | None:
    raw = data_dir.read_specials_doc(slug)
    return SpecialsDoc.model_validate(raw) if raw is not None else None


def confirm_voice(
    slug: str, *, chapter: int, start_ms: int, same_voice: bool, actor: Actor
) -> bool:
    """Store the call on the entry starting at ``start_ms``; ``False`` when there is no such entry."""
    with _WRITE_LOCK:
        doc = read_doc(slug)
        entries = doc.chapters.get(str(chapter), []) if doc else []
        entry = next((e for e in entries if e.start_ms == start_ms), None)
        if entry is None:
            return False
        before = entry.voice_confirmed
        entry.voice_confirmed = same_voice
        data_dir.write_specials_doc(slug, doc.model_dump(mode="json"))
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
    return True
