"""Round-trip tests for the ``PipelineMeta`` Pydantic schema.

Schema lives at ``qua_shared/schemas/bucket/pipeline_meta.py``. The artefact
is the per-reciter ``reciters/<slug>/pipeline_meta.json`` — immutable facts
emitted by the offline extraction pipeline (currently the edition it ran under).

These assert the canonical shape round-trips byte/shape-equal, the required
``generated_at`` provenance, and that the artefact is pure ``extra="forbid"``
— any unknown key raises ``ValidationError`` rather than being stripped.
"""

from __future__ import annotations

import pytest

from qua_shared.schemas import PipelineMeta


def _canonical_meta() -> dict:
    """Canonical pipeline_meta — schema version + generated_at."""
    return {
        "schema_version": 1,
        "generated_at": "2026-05-16T21:46:39.362Z",
    }


# -- Validation tests ---------------------------------------------------


def test_canonical_meta_validates():
    m = PipelineMeta.model_validate(_canonical_meta())
    assert m.schema_version == 1
    assert m.generated_at == "2026-05-16T21:46:39.362Z"


def test_generated_at_required():
    """``generated_at`` has no default — a record without it is corrupt."""
    raw = _canonical_meta()
    raw.pop("generated_at")
    with pytest.raises(ValueError):
        PipelineMeta.model_validate(raw)


def test_legacy_deleted_basmala_validates_and_is_never_written():
    """Old sidecars carry the deprecated ``deleted_basmala_chapters`` list: it
    must still validate under ``extra="forbid"``, and no dump writes it back."""
    raw = {**_canonical_meta(), "deleted_basmala_chapters": [9, 1]}
    m = PipelineMeta.model_validate(raw)
    assert m.deleted_basmala_chapters == [9, 1]
    assert m.model_dump(exclude_none=True) == _canonical_meta()


# -- pure extra="forbid" policy ----------------------------------------


def test_unknown_key_rejected():
    """An unknown key raises ``ValidationError`` under pure ``extra="forbid"``
    — no silent strip, no absorbed extra, no crash-on-read tolerance."""
    raw = _canonical_meta()
    raw["bogus_field"] = 123
    with pytest.raises(ValueError):
        PipelineMeta.model_validate(raw)


# -- Round-trip emission ------------------------------------------------


def test_round_trip_byte_shape_equal():
    """``model_dump(exclude_none=True)`` reproduces the canonical artefact
    shape exactly — no field renamed or dropped, no extra key introduced.

    ``exclude_none`` is the writer/reader convention for these artefacts (see
    ``schemas/bucket/segment.py``): an optional field the document does not
    carry stays absent rather than serializing as ``null``.
    """
    raw = _canonical_meta()
    m = PipelineMeta.model_validate(raw)
    out = m.model_dump(exclude_none=True)
    assert out == raw


def test_round_trip_preserves_riwayah_when_present():
    """A non-Hafs sidecar names the coordinate system its extraction ran in."""
    raw = {**_canonical_meta(), "riwayah": "warsh_an_nafi"}
    out = PipelineMeta.model_validate(raw).model_dump(exclude_none=True)
    assert out == raw
