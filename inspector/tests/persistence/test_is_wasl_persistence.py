"""``is_wasl`` reaches detailed.json on both save paths, set and cleared."""

import pytest

from adapters.save_payload import make_seg
from services.segments import save


def _seg(is_wasl: bool) -> dict:
    return {
        "segment_uid": "019e32bb-bef6-7132-b006-72aa4ee485cb",
        "time_start": 100,
        "time_end": 500,
        "matched_ref": "1:3:1-1:3:2",
        "confidence": 0.9,
        "is_wasl": is_wasl,
    }


@pytest.mark.parametrize("before,sent", [(False, True), (True, False)])
def test_patch_applies_is_wasl(monkeypatch, before, sent):
    seg = _seg(before)
    monkeypatch.setattr(save, "normalize_ref_with_wc", lambda ref, _riwayah: ref)
    monkeypatch.setattr(save, "get_single_word_verses", lambda _riwayah: set())
    monkeypatch.setattr(save, "stamp_segment", lambda *_args: None)

    save._apply_patch(
        [{"ref": "1", "segments": [seg]}],
        {
            "segments": [
                {"index": 0, "matched_ref": seg["matched_ref"], "confidence": 0.9, "is_wasl": sent}
            ]
        },
    )

    assert seg["is_wasl"] is sent


def test_patch_without_is_wasl_keeps_it(monkeypatch):
    seg = _seg(True)
    monkeypatch.setattr(save, "normalize_ref_with_wc", lambda ref, _riwayah: ref)
    monkeypatch.setattr(save, "get_single_word_verses", lambda _riwayah: set())
    monkeypatch.setattr(save, "stamp_segment", lambda *_args: None)

    save._apply_patch(
        [{"ref": "1", "segments": [seg]}],
        {"segments": [{"index": 0, "matched_ref": seg["matched_ref"], "confidence": 0.9}]},
    )

    assert seg["is_wasl"] is True


def test_full_replace_false_clears_existing_wasl():
    existing = _seg(True)
    payload = {**_seg(False), "time_end": 300}

    result = make_seg(payload, {}, {existing["segment_uid"]: existing}, {})

    assert "is_wasl" not in result


def test_full_replace_without_is_wasl_inherits_it():
    existing = _seg(True)
    payload = {k: v for k, v in existing.items() if k != "is_wasl"}

    result = make_seg(payload, {(100, 500): existing}, {}, {})

    assert result["is_wasl"] is True
