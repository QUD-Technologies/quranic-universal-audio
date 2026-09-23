"""Preservation rules for historical word timing review data."""

from adapters.save_payload import make_seg
from services.segments import save


def _existing() -> dict:
    return {
        "segment_uid": "019e32bb-bef6-7132-b006-72aa4ee485cb",
        "time_start": 100,
        "time_end": 500,
        "matched_ref": "Basmala",
        "confidence": 0.9,
        "word_timings": [
            {
                "word": "بِسْمِ",
                "location": "1:1:1",
                "start_ms": 100,
                "end_ms": 500,
            }
        ],
    }


def test_full_replace_preserves_word_timings_for_unchanged_row():
    existing = _existing()
    payload = {
        "segment_uid": existing["segment_uid"],
        "time_start": existing["time_start"],
        "time_end": existing["time_end"],
        "matched_ref": existing["matched_ref"],
        "confidence": 1.0,
    }

    result = make_seg(
        payload,
        {(existing["time_start"], existing["time_end"]): existing},
        {existing["segment_uid"]: existing},
        {},
    )

    assert result["word_timings"] == existing["word_timings"]


def test_full_replace_drops_word_timings_after_geometry_change():
    existing = _existing()
    payload = {
        "segment_uid": existing["segment_uid"],
        "time_start": 150,
        "time_end": existing["time_end"],
        "matched_ref": existing["matched_ref"],
        "confidence": 1.0,
    }

    result = make_seg(
        payload,
        {},
        {existing["segment_uid"]: existing},
        {},
    )

    assert "word_timings" not in result


def test_patch_drops_word_timings_after_reference_change(monkeypatch):
    existing = _existing()
    matching = [{"ref": "1", "segments": [existing]}]
    monkeypatch.setattr(save, "normalize_ref_with_wc", lambda ref, _riwayah: ref)
    monkeypatch.setattr(save, "get_single_word_verses", lambda _riwayah: set())
    monkeypatch.setattr(save, "stamp_segment", lambda *_args: None)

    save._apply_patch(
        matching,
        {
            "segments": [
                {
                    "index": 0,
                    "matched_ref": "Amin",
                    "confidence": 1.0,
                }
            ]
        },
    )

    assert "word_timings" not in existing
