"""Audio-tab HTTP wire schemas (``/api/audio/*`` JSON responses).

The audio blueprint serves exactly one JSON-success shape — the rest of its
routes (segment-clip, audio-proxy) stream raw MP3 bytes on success and only
ever return JSON on failure (the canonical ``ErrorEnvelope`` in
``_envelopes.py``). So the only response modelled here is the per-delivery
chapter metadata map returned by ``GET /api/audio/surahs/<category>/<source>/<slug>``
(see ``inspector/routes/audio/metadata.py``).

Every entry always carries ``url`` and ``duration_ms``. The latter is a
required, nullable field and is ``None`` only when neither the manifest nor the
slim-peaks header yields a length.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AudioSurahEntry(BaseModel):
    """One chapter's playback metadata in the ``surahs`` map.

    ``duration_ms`` is ``None`` only when the manifest carries no duration and
    the slim-peaks fallback also cannot provide one. Both keys are always
    serialized.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    duration_ms: int | None


class AudioSurahsResponse(BaseModel):
    """``GET /api/audio/surahs/<category>/<source>/<slug>`` success body.

    ``surahs`` is keyed by chapter number as a string (the manifest sidecar's
    ``chapters`` keys pass through verbatim).
    """

    model_config = ConfigDict(extra="forbid")

    surahs: dict[str, AudioSurahEntry]
