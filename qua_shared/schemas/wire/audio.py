"""Audio-tab HTTP wire schemas (``/api/audio/*`` JSON responses).

The audio blueprint serves exactly one JSON-success shape — the rest of its
routes (segment-clip, audio-proxy) stream raw MP3 bytes on success and only
ever return JSON on failure (the canonical ``ErrorEnvelope`` in
``_envelopes.py``). So the only response modelled here is the per-delivery
chapter metadata map returned by ``GET /api/audio/surahs/<category>/<source>/<slug>``
(see ``inspector/routes/audio/metadata.py``).

Every entry always carries ``url``, ``duration_ms`` and ``size_bytes``. Both
are required, nullable fields: ``duration_ms`` is ``None`` only when neither the
manifest nor the slim-peaks header yields a length; ``size_bytes`` is ``None``
when the manifest records no source-file size.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AudioSurahEntry(BaseModel):
    """One chapter's playback metadata in the ``surahs`` map.

    ``duration_ms`` is ``None`` only when the manifest carries no duration and
    the slim-peaks fallback also cannot provide one. ``size_bytes`` is the
    manifest size of the source file the delivery was aligned on; the FE plays
    the CDN URL directly only when the live file still has that size. All keys
    are always serialized.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    duration_ms: int | None
    size_bytes: int | None


class AudioSurahsResponse(BaseModel):
    """``GET /api/audio/surahs/<category>/<source>/<slug>`` success body.

    ``surahs`` is keyed by chapter number as a string (the manifest sidecar's
    ``chapters`` keys pass through verbatim).
    """

    model_config = ConfigDict(extra="forbid")

    surahs: dict[str, AudioSurahEntry]
