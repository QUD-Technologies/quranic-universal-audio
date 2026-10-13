"""Verse-level timestamps-validation sidecar — ``reciters/<slug>/ts_validation.json``.

The verse-level analogue of the segment-level ``low_confidence_v2.json`` probe.
Written as a plain dict alongside the shards: MFA runs filled ``verses`` with
beam flags; aligner runs leave it empty and list the segments they could not
time under ``failed_segments`` (an extra key). These models exist for the
*readers*: the
Inspector read-path that serves the sidecar owner-gated, and the
``pydantic-to-typescript`` FE codegen (re-exported via ``fe_types.py``) that
gives the Timestamps-tab "ts-validation" accordion a typed shape.

A verse is flagged when it aligns under the canonical (widest) beam but fails
under one or more tighter probe beams — tighter-beam disagreement is the
low-confidence signal. ``min_passing_beam`` is the narrowest beam at which the
verse still fully aligned (``None`` when it never aligned).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TsValidationVerse(BaseModel):
    """Per-verse beam-agreement summary."""

    model_config = ConfigDict(extra="allow")

    failed_beams: list[int] = Field(default_factory=list)
    min_passing_beam: int | None = None


class TsValidationMeta(BaseModel):
    """Provenance for one ts-validation run."""

    model_config = ConfigDict(extra="allow")

    created_at: str = ""
    reciter: str = ""
    aligner_model: str = ""
    method: str = ""
    beams: list[int] = Field(default_factory=list)
    canonical_beam: int = 0


class TsValidationDoc(BaseModel):
    """The ``ts_validation.json`` document — meta + verse-keyed flags."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    meta: TsValidationMeta = Field(default_factory=TsValidationMeta, alias="_meta")
    verses: dict[str, TsValidationVerse] = Field(default_factory=dict)

    @classmethod
    def from_doc(cls, raw: Any) -> TsValidationDoc:
        """Parse a stored doc, tolerating forward/extra fields."""
        return cls.model_validate(raw)
