"""``catalog/review_signals.json`` — per-delivery open review counts for the reciter picker.

One small fleet-wide file: how many open Low Confidence items (confidence under the
delivery's riwayah threshold) and Low Confidence v2 items each delivery has, so the
Segments reciter picker can badge and filter rows without reading any reciter's segments.

Written by ``scripts/backfills/review_signals.py`` (offline, every delivery or a list) and
by the Inspector's validate route whenever a reciter's counts change; read by
``services/validation/review_signals.py``.

Extras handling: pure ``extra="forbid"``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

REVIEW_SIGNALS_SCHEMA_VERSION = 1


class ReviewSignalCounts(BaseModel):
    """One delivery's open items."""

    model_config = ConfigDict(extra="forbid")

    low_confidence: int = Field(0, ge=0)
    low_confidence_v2: int = Field(0, ge=0)


class ReviewSignalsDoc(BaseModel):
    """The whole file: counts by delivery slug."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = REVIEW_SIGNALS_SCHEMA_VERSION
    updated_at: str | None = None
    signals: dict[str, ReviewSignalCounts] = Field(default_factory=dict)
