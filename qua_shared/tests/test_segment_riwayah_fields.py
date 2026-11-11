"""The multi-riwayah coordinate-provenance fields on ``detailed.json``.

The load-bearing property is that they are *invisible* to Hafs: 37 existing
reciters must round-trip byte-identically, or the first save after this change
rewrites every segment.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from qua_shared.schemas.bucket.pipeline_meta import PipelineMeta
from qua_shared.schemas.bucket.segment import (
    DetailedDocument,
    DetailedMeta,
    DetailedSegment,
    parse_detailed_segment,
)

HAFS_SEG = {
    "time_start": 1200,
    "time_end": 5400,
    "matched_ref": "1:1:1-1:1:4",
    "qalqala_letter": None,
    "confidence": 1.0,
    "segment_uid": "0198e0a1-0000-7000-8000-000000000001",
}


def test_hafs_segment_round_trips_byte_identically():
    """No NEW key may appear in a Hafs seg's serialization.

    ``is_wasl`` is a non-None-defaulted boolean that ``exclude_none`` keeps
    (writers omit it when False via ``adapters/save_payload``, not via the
    model). The property
    under test is that the two provenance fields add nothing for Hafs.
    """
    seg = parse_detailed_segment(HAFS_SEG)
    assert seg.source_ref is None
    assert seg.projection_support is None
    dumped = seg.model_dump(exclude_none=True)
    assert set(dumped) == {
        "time_start",
        "time_end",
        "matched_ref",
        "confidence",
        "segment_uid",
        "is_wasl",
    }
    assert json.dumps(dumped, sort_keys=True) == json.dumps(
        {**{k: v for k, v in HAFS_SEG.items() if v is not None}, "is_wasl": False},
        sort_keys=True,
    )


def test_hafs_meta_round_trips_byte_identically():
    meta = DetailedMeta.model_validate({"created_at": "2026-01-01T00:00:00Z"})
    assert meta.riwayah is None
    assert meta.model_dump(exclude_none=True) == {"created_at": "2026-01-01T00:00:00Z"}


def test_non_hafs_segment_carries_source_evidence():
    seg = parse_detailed_segment(
        {
            **HAFS_SEG,
            "matched_ref": "40:26:13-40:26:13",
            "source_ref": "40:26:13-40:26:14",
            "projection_support": "full",
        }
    )
    assert seg.source_ref == "40:26:13-40:26:14"
    assert seg.projection_support == "full"


def test_projection_support_is_closed():
    with pytest.raises(ValidationError):
        parse_detailed_segment({**HAFS_SEG, "projection_support": "partial-ish"})


def test_document_carries_the_riwayah_of_its_coordinates():
    doc = DetailedDocument.model_validate(
        {
            "_meta": {"riwayah": "warsh_an_nafi"},
            "entries": [{"ref": "112", "segments": [HAFS_SEG]}],
        }
    )
    assert doc.meta.riwayah == "warsh_an_nafi"
    assert doc.model_dump(by_alias=True, exclude_none=True)["_meta"]["riwayah"] == (
        "warsh_an_nafi"
    )


def test_pipeline_meta_riwayah_defaults_to_absent():
    meta = PipelineMeta(generated_at="2026-01-01T00:00:00Z")
    assert meta.riwayah is None
    assert "riwayah" not in meta.model_dump(exclude_none=True)


def test_segment_still_forbids_unknown_fields():
    """The new optional fields must not have loosened ``extra="forbid"``."""
    with pytest.raises(ValidationError):
        DetailedSegment.model_validate({**HAFS_SEG, "matched_text": "…"})
