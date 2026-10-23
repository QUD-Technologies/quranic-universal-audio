"""Per-reciter ``pipeline_meta.json`` schema.

Persistent record of immutable facts emitted by the offline extraction
pipeline. Lives at ``reciters/<slug>/pipeline_meta.json``.

Currently records:

- ``riwayah`` — the edition the extraction ran under.

Extras handling: pure ``extra="forbid"``. The model is lean and
well-defined; any unknown field raises a ``ValidationError``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PipelineMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    generated_at: str  # ISO-8601 timestamp, UTC

    #: Deprecated. Legacy: the chapters whose Basmala the pipeline stripped, once
    #: read by the retired ``basmala_amin`` validation category. Nothing reads it
    #: now; it is accepted so old sidecars still validate, and ``exclude=True``
    #: keeps it out of every new write.
    deleted_basmala_chapters: list[int] = Field(default_factory=list, exclude=True)

    #: Inspector vocabulary slug the extraction ran under. ``None`` on every
    #: pre-multi-riwayah sidecar, which means Hafs. Mirrors
    #: ``DetailedMeta.riwayah`` — the two are written by the same extraction
    #: run and must agree.
    riwayah: str | None = None
