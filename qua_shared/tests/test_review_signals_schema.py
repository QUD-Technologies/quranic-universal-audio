"""``catalog/review_signals.json`` round-trips through ``ReviewSignalsDoc`` and rejects
unknown fields (pure ``extra="forbid"``)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from qua_shared.schemas.bucket.review_signals import ReviewSignalsDoc

CANONICAL = {
    "schema_version": 1,
    "updated_at": "2026-11-11T12:40:52Z",
    "signals": {
        "adel_al_kalbani_qdc": {"low_confidence": 37, "low_confidence_v2": 4, "flagged": 2},
        "walid_atef_way2quran": {"low_confidence": 19, "low_confidence_v2": 14, "flagged": 0},
    },
}


def test_canonical_doc_round_trips_byte_equal():
    raw = json.dumps(CANONICAL, sort_keys=True)
    doc = ReviewSignalsDoc.model_validate_json(raw)
    assert json.dumps(doc.model_dump(mode="json"), sort_keys=True) == raw


@pytest.mark.parametrize(
    "bad",
    [
        {**CANONICAL, "pending": 3},
        {"signals": {"x": {"low_confidence": 1, "extra": 2}}},
        {"signals": {"x": {"low_confidence": -1}}},
    ],
)
def test_unknown_fields_and_negative_counts_are_rejected(bad):
    with pytest.raises(ValidationError):
        ReviewSignalsDoc.model_validate(bad)
