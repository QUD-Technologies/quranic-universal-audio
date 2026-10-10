"""Aligner lattice ``pauses`` on a persisted segment: schema, save inheritance, patch drop."""

import pytest
from pydantic import ValidationError

from adapters.save_payload import make_seg
from qua_shared.schemas.bucket.segment import DetailedSegment
from services.segments import save

PAUSES = [{"after_ref": "2:2:2", "gain": 1.4, "separability": 2.0}]


def _existing() -> dict:
    return {
        "segment_uid": "019e32bb-bef6-7132-b006-72aa4ee485cb",
        "time_start": 100,
        "time_end": 5000,
        "matched_ref": "2:2:1-2:2:5",
        "confidence": 0.9,
        "pauses": PAUSES,
    }


def _payload(**changes) -> dict:
    existing = _existing()
    return {
        "segment_uid": existing["segment_uid"],
        "time_start": existing["time_start"],
        "time_end": existing["time_end"],
        "matched_ref": existing["matched_ref"],
        "confidence": 1.0,
        **changes,
    }


def _make(payload: dict) -> dict:
    existing = _existing()
    return make_seg(
        payload,
        {(existing["time_start"], existing["time_end"]): existing},
        {existing["segment_uid"]: existing},
        {},
    )


def test_schema_round_trips_pauses_and_accepts_null():
    seg = DetailedSegment.model_validate(_existing())
    assert seg.model_dump(exclude_none=True)["pauses"] == PAUSES
    nulled = DetailedSegment.model_validate({**_existing(), "pauses": None})
    assert "pauses" not in nulled.model_dump(exclude_none=True)


def test_schema_rejects_an_unknown_pause_field():
    with pytest.raises(ValidationError):
        DetailedSegment.model_validate({**_existing(), "pauses": [{**PAUSES[0], "token_pos": 3}]})


def test_full_replace_keeps_pauses_on_an_unchanged_row():
    assert _make(_payload())["pauses"] == PAUSES


def test_full_replace_drops_pauses_after_a_trim():
    assert "pauses" not in _make(_payload(time_end=4000))


def test_full_replace_drops_pauses_after_a_reference_edit():
    assert "pauses" not in _make(_payload(matched_ref="2:2:1-2:2:4"))


def test_patch_drops_pauses_after_reference_change(monkeypatch):
    existing = _existing()
    matching = [{"ref": "2", "segments": [existing]}]
    monkeypatch.setattr(save, "normalize_ref_with_wc", lambda ref, _riwayah: ref)
    monkeypatch.setattr(save, "stamp_segment", lambda *_args: None)

    save._apply_patch(
        matching, {"segments": [{"index": 0, "matched_ref": "2:2:1-2:2:4", "confidence": 1.0}]}
    )

    assert "pauses" not in matching[0]["segments"][0]
