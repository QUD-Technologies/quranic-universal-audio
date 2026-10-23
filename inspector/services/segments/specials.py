"""Read a reciter's ``specials.json`` and record a maintainer's voice call on one entry.

The automatic Basmala voice check leaves a ``needs_confirmation`` band; a holder
of ``segments.confirm_basmala_voice`` settles it (or overrules a verdict) here.
The call is stored as ``voice_confirmed`` on the entry — it wins over the
automatic verdict wherever ``same_voice`` is read — audited, and, when it
changes the answer, the reciter's published GH member is stamped stale so the
next cut ships the new ``same_voice``.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from qua_shared.schemas import Actor
from qua_shared.schemas.bucket.specials import SpecialsDoc
from qua_shared.schemas.wire.release import StaleReason
from services.db import repo_releases
from services.db import sync as _sync
from services.state import audit
from services.storage import data_dir

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
