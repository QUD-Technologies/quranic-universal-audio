"""Join answers survive both save modes, wire schemas and undo snapshots."""

import pytest

from adapters.save_payload import make_seg
from domain.command import apply_inverse_patch
from qua_shared.schemas.bucket.segment import DetailedSegment, JoinVerdict
from qua_shared.schemas.wire.seg import SegAllSegment, SegSavePatchSegment
from services.segments import save

ANSWER = {"at_ms": 500, "after_ref": "1:3:2", "verdict": "waqf"}


def seg(**extra):
    return {
        "segment_uid": "root",
        "time_start": 100,
        "time_end": 500,
        "matched_ref": "1:3:1-1:3:2",
        "confidence": 0.9,
        **extra,
    }


def test_full_replace_preserves_explicit_false_answer():
    existing = seg(is_wasl=True)
    payload = seg(is_wasl=False, join_verdicts=[ANSWER])
    result = make_seg(payload, {}, {"root": existing}, {})
    assert DetailedSegment.model_validate(result).join_verdicts == [JoinVerdict(**ANSWER)]
    assert result.get("is_wasl", False) is False


def test_legacy_payload_preserves_answers_only_for_unchanged_geometry():
    existing = seg(join_verdicts=[ANSWER])
    assert make_seg(seg(), {}, {"root": existing}, {})["join_verdicts"] == [ANSWER]
    assert "join_verdicts" not in make_seg(seg(time_end=600), {}, {"root": existing}, {})
    assert make_seg(seg(join_verdicts=[]), {}, {"root": existing}, {})["join_verdicts"] == []


def test_patch_and_undo_roundtrip(monkeypatch):
    before = seg(is_wasl=False)
    current = seg(is_wasl=False)
    monkeypatch.setattr(save, "normalize_ref_with_wc", lambda ref, _riwayah: ref)
    monkeypatch.setattr(save, "get_single_word_verses", lambda _riwayah: set())
    monkeypatch.setattr(save, "stamp_segment", lambda *_: None)
    payload = {
        "segment_uid": "root",
        "confidence": 0.9,
        "index": 0,
        "matched_ref": current["matched_ref"],
        "join_verdicts": [ANSWER],
    }
    SegSavePatchSegment.model_validate(payload)
    entries = [{"ref": "1", "segments": [current]}]
    save._apply_patch(entries, {"segments": [payload]})
    assert current["join_verdicts"] == [ANSWER]
    wire = {**current, "chapter": 1, "entry_idx": 0, "index": 0, "entry_ref": "1"}
    assert SegAllSegment.model_validate(wire).join_verdicts == [JoinVerdict(**ANSWER)]
    monkeypatch.setattr("services.quran_refs.dk_text_for_ref", lambda *_: "")
    apply_inverse_patch(
        entries,
        {
            "before": [before],
            "after": [current],
            "removedIds": [],
            "insertedIds": [],
            "affectedChapterIds": [1],
        },
    )
    assert entries[0]["segments"][0].get("join_verdicts") is None


@pytest.mark.parametrize("change", [{"verdict": "unset"}, {"at_ms": -1}, {"after_ref": "bad"}])
def test_reject_invalid_answers(change):
    with pytest.raises(ValueError):
        JoinVerdict.model_validate({**ANSWER, **change})
