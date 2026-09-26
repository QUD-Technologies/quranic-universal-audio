"""Boundary-review categories (``hidden_pause`` / ``missed_waqf`` / ``false_split`` /
``unmarked_wasl``).

Covers the classifier flags, the detail-list items (with the sidecar
``boundary`` payload round-tripping through ``SegValidateResponse``), and the
``include_boundary_review`` gate on ``validate_reciter_segments``.
"""

from __future__ import annotations

from qua_shared.schemas.wire.seg import SegValidateResponse
from services.validation import (
    BOUNDARY_REVIEW_CATEGORIES,
    classify_flags,
    classify_segment,
    strip_boundary_review,
)
from services.validation.detail import _build_detail_lists

HIDDEN_ENTRY = {
    "kind": "hidden_pause",
    "chapter": 1,
    "cursors": [1500],
    "refs": ["1:1:1-1:1:2", "1:1:3-1:1:4"],
    "score": 2450,
    "cuts": [
        {
            "cursor_ms": 1500,
            "axes": ["lite", "trio"],
            "gap_ms": 450,
            "score": 2450,
            "word": "الرحمن",
            "final_class": "nasal",
            "verse_end": False,
            "evidence": {"trio": {"end_ms": 1480, "next_start_ms": 1930, "gap_ms": 450}},
        }
    ],
}
MISSED_ENTRY = {
    "kind": "missed_waqf",
    "chapter": 1,
    "cursors": [1200, 2400],
    "refs": ["1:1:1-1:1:1", "1:1:2-1:1:3", "1:1:4-1:1:4"],
    "score": 1300,
    "cuts": [
        {
            "cursor_ms": 1200,
            "axes": ["phoneme"],
            "gap_ms": 300,
            "score": 1300,
            "word": "بسم",
            "final_class": "nasal",
            "verse_end": False,
            "evidence": {
                "phoneme": {
                    "gain": 4.2,
                    "gain_pos": 3,
                    "separability": 0.8,
                    "silence_ms": 300,
                    "dip_db": 14.0,
                    "after_ref": "1:1:1",
                    "next_ref": "1:1:2",
                }
            },
        },
        {
            "cursor_ms": 2400,
            "axes": ["phoneme"],
            "gap_ms": 180,
            "score": 1180,
            "word": "الرحمن",
            "final_class": "nasal",
            "verse_end": False,
            "evidence": {"phoneme": {"silence_ms": 180}},
        },
    ],
}
FALSE_ENTRY = {
    "kind": "false_split",
    "chapter": 1,
    "next_uid": "u2",
    "axes": ["trio"],
    "gap_ms": 40,
    "score": 1040,
    "ref_before": "1:1:1-1:1:4",
    "ref_after": "1:2:1-1:2:4",
    "is_wasl": False,
    "word": "الرحيم",
    "final_class": "he",
    "verse_end": True,
    "evidence": {"trio": {"start_ms": 0, "end_ms": 3000, "ref": "1:1:1-1:1:4"}},
}
WASL_ENTRY = {
    "kind": "unmarked_wasl",
    "chapter": 1,
    "next_uid": "u2",
    "axes": ["trio", "lite"],
    "gap_ms": 0,
    "score": 2000,
    "ref_before": "1:1:1-1:1:4",
    "ref_after": "1:2:1-1:2:4",
    "is_wasl": False,
    "word": "الرحيم",
    "final_class": "nasal",
    "verse_end": True,
    "evidence": {"trio": {"start_ms": 0, "end_ms": 6000, "ref": "1:1:1-1:2:4"}},
}


def _seg(uid: str, ref: str, **extra) -> dict:
    return {
        "segment_uid": uid,
        "matched_ref": ref,
        "confidence": 1.0,
        "time_start": 0,
        "time_end": 3000,
        "qalqala_letter": None,
        "is_boundary_adj": False,
        **extra,
    }


def _flags(seg: dict, **kw) -> dict:
    return classify_flags(seg, "1", False, 1, 1, 1, 1, 4, set(), None, **kw)


def test_flags_default_false_without_sidecars():
    flags = _flags(_seg("u1", "1:1:1-1:1:4"))
    assert flags["hidden_pause"] is False
    assert flags["false_split"] is False
    assert flags["unmarked_wasl"] is False


def test_flags_fire_on_uid_membership():
    flags = _flags(
        _seg("u1", "1:1:1-1:1:4"),
        hidden_pause_uids={"u1": HIDDEN_ENTRY},
        false_split_uids={"u1"},
        unmarked_wasl_uids={"u1": WASL_ENTRY},
    )
    assert flags["hidden_pause"] is True
    assert flags["false_split"] is True
    assert flags["unmarked_wasl"] is True
    assert classify_segment(
        _seg("u1", "1:1:1-1:1:4"),
        hidden_pause_uids={"u1"},
        false_split_uids={"u1"},
        unmarked_wasl_uids={"u1"},
    ) == ["hidden_pause", "false_split", "unmarked_wasl"]


def test_unmarked_wasl_flag_is_independent_and_suppressible():
    seg = _seg("u1", "1:1:1-1:1:4")
    flags = _flags(seg, unmarked_wasl_uids={"u1"})
    assert flags["unmarked_wasl"] is True
    assert flags["false_split"] is False
    ignored = _seg("u1", "1:1:1-1:1:4", ignored_categories=["unmarked_wasl"])
    assert _flags(ignored, unmarked_wasl_uids={"u1"})["unmarked_wasl"] is False
    edited = _seg("u1", "1:1:1-1:1:4", _resolved_by_edit=["unmarked_wasl"])
    assert _flags(edited, unmarked_wasl_uids={"u1"})["unmarked_wasl"] is False


def test_flags_respect_ignored_categories():
    seg = _seg("u1", "1:1:1-1:1:4", ignored_categories=["hidden_pause"])
    flags = _flags(seg, hidden_pause_uids={"u1"}, false_split_uids={"u1"})
    assert flags["hidden_pause"] is False
    assert flags["false_split"] is True


def test_flags_respect_resolved_by_edit():
    seg = _seg("u1", "1:1:1-1:1:4", _resolved_by_edit=["false_split"])
    flags = _flags(seg, hidden_pause_uids={"u1"}, false_split_uids={"u1"})
    assert flags["false_split"] is False
    assert flags["hidden_pause"] is True


def _detail(entries, **kw):
    return _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts={(1, 1): 4, (1, 2): 4},
        canonical=None,
        single_word_verses=set(),
        **kw,
    )


def test_detail_items_carry_boundary_payload_and_validate():
    entries = [{"ref": "1", "segments": [_seg("u1", "1:1:1-1:1:4"), _seg("u2", "1:2:1-1:2:4")]}]
    detail = _detail(
        entries,
        hidden_pause_map={"u1": HIDDEN_ENTRY},
        false_split_map={"u1": FALSE_ENTRY},
        unmarked_wasl_map={"u1": WASL_ENTRY},
    )

    (hp,) = detail["hidden_pause"]
    assert hp["segment_uid"] == "u1"
    assert hp["seg_index"] == 0
    assert "hidden_pause" in hp["classified_issues"]
    assert hp["boundary"]["cursors"] == [1500]
    assert hp["boundary"]["refs"] == ["1:1:1-1:1:2", "1:1:3-1:1:4"]
    assert hp["boundary"]["score"] == 2450
    assert hp["boundary"]["cuts"][0]["axes"] == ["lite", "trio"]
    assert hp["boundary"]["cuts"][0]["evidence"]["trio"]["gap_ms"] == 450

    (fs,) = detail["false_split"]
    assert fs["boundary"]["next_uid"] == "u2"
    assert fs["boundary"]["axes"] == ["trio"]
    assert fs["boundary"]["gap_ms"] == 40
    assert fs["boundary"]["verse_end"] is True

    (uw,) = detail["unmarked_wasl"]
    assert uw["segment_uid"] == "u1"
    assert "unmarked_wasl" in uw["classified_issues"]
    assert uw["boundary"]["next_uid"] == "u2"
    assert uw["boundary"]["axes"] == ["trio", "lite"]
    assert uw["boundary"]["gap_ms"] == 0
    assert uw["boundary"]["score"] == 2000
    assert uw["boundary"]["is_wasl"] is False
    assert uw["boundary"]["verse_end"] is True

    resp = SegValidateResponse.model_validate(
        {
            "hidden_pause": detail["hidden_pause"],
            "false_split": detail["false_split"],
            "unmarked_wasl": detail["unmarked_wasl"],
        }
    )
    dumped = resp.model_dump(mode="json", exclude_unset=True, by_alias=True)
    assert dumped["hidden_pause"][0]["boundary"]["cuts"][0]["word"] == "الرحمن"
    assert dumped["false_split"][0]["boundary"]["word"] == "الرحيم"
    assert dumped["unmarked_wasl"][0]["boundary"]["evidence"]["trio"]["end_ms"] == 6000


def test_detail_null_refs_ride_through_as_null():
    entry = {**HIDDEN_ENTRY, "refs": None}
    entries = [{"ref": "1", "segments": [_seg("u1", "1:1:1-1:1:4")]}]
    detail = _detail(entries, hidden_pause_map={"u1": entry})
    assert detail["hidden_pause"][0]["boundary"]["refs"] is None
    SegValidateResponse.model_validate({"hidden_pause": detail["hidden_pause"]})


def test_detail_lists_empty_without_sidecars():
    entries = [{"ref": "1", "segments": [_seg("u1", "1:1:1-1:1:4")]}]
    detail = _detail(entries)
    assert detail["hidden_pause"] == []
    assert detail["false_split"] == []
    assert detail["unmarked_wasl"] == []


def test_strip_boundary_review_removes_arrays_and_zeroes_counts():
    result = {
        "hidden_pause": [{"x": 1}],
        "false_split": [{"y": 2}],
        "unmarked_wasl": [{"w": 4}],
        "hidden_pause_meta": {"a": 1},
        "false_split_meta": {"b": 2},
        "unmarked_wasl_meta": {"c": 3},
        "cross_verse": [{"z": 3}],
        "category_counts": {
            "hidden_pause": 1,
            "false_split": 1,
            "unmarked_wasl": 1,
            "cross_verse": 1,
        },
    }
    out = strip_boundary_review(result)
    for cat in BOUNDARY_REVIEW_CATEGORIES:
        assert cat not in out
        assert f"{cat}_meta" not in out
        assert out["category_counts"][cat] == 0
    assert out["cross_verse"] == [{"z": 3}]
    assert out["category_counts"]["cross_verse"] == 1
    assert result["category_counts"]["hidden_pause"] == 1


def test_validate_reciter_segments_gate(monkeypatch, tmp_reciter_dir):
    from services import validation as val
    from services.storage.data_loader import load_detailed

    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas")
    first = load_detailed(reciter)[0]["segments"][0]["segment_uid"]
    monkeypatch.setattr(
        val, "load_hidden_pause", lambda _r: ({first: HIDDEN_ENTRY}, {"kind": "hidden_pause"})
    )
    monkeypatch.setattr(
        val, "load_false_split", lambda _r: ({first: FALSE_ENTRY}, {"kind": "false_split"})
    )
    monkeypatch.setattr(
        val, "load_unmarked_wasl", lambda _r: ({first: WASL_ENTRY}, {"kind": "unmarked_wasl"})
    )

    full = val.validate_reciter_segments(reciter)
    assert full is not None
    assert full["category_counts"]["hidden_pause"] == 1
    assert full["category_counts"]["false_split"] == 1
    assert full["category_counts"]["unmarked_wasl"] == 1
    assert full["hidden_pause"][0]["segment_uid"] == first
    assert full["unmarked_wasl"][0]["segment_uid"] == first
    assert full["hidden_pause_meta"] == {"kind": "hidden_pause"}
    assert full["false_split_meta"] == {"kind": "false_split"}
    assert full["unmarked_wasl_meta"] == {"kind": "unmarked_wasl"}

    gated = val.validate_reciter_segments(reciter, include_boundary_review=False)
    assert gated is not None
    assert "hidden_pause" not in gated
    assert "false_split_meta" not in gated
    assert "unmarked_wasl" not in gated
    assert "unmarked_wasl_meta" not in gated
    assert gated["category_counts"]["hidden_pause"] == 0
    assert gated["category_counts"]["false_split"] == 0
    assert gated["category_counts"]["unmarked_wasl"] == 0
    assert gated["category_counts"]["cross_verse"] == full["category_counts"]["cross_verse"]


def test_missed_waqf_flag_fires_on_uid_and_is_suppressible():
    assert _flags(_seg("u1", "1:1:1-1:1:4"))["missed_waqf"] is False
    assert _flags(_seg("u1", "1:1:1-1:1:4"), missed_waqf_uids={"u1"})["missed_waqf"] is True
    ignored = _seg("u1", "1:1:1-1:1:4", ignored_categories=["missed_waqf"])
    assert _flags(ignored, missed_waqf_uids={"u1"})["missed_waqf"] is False
    edited = _seg("u1", "1:1:1-1:1:4", _resolved_by_edit=["missed_waqf"])
    assert _flags(edited, missed_waqf_uids={"u1"})["missed_waqf"] is False


def test_missed_waqf_items_carry_boundary_and_validate():
    entries = [{"ref": "1", "segments": [_seg("u1", "1:1:1-1:1:4")]}]
    detail = _detail(entries, missed_waqf_map={"u1": MISSED_ENTRY})
    (mw,) = detail["missed_waqf"]
    assert mw["segment_uid"] == "u1"
    assert "missed_waqf" in mw["classified_issues"]
    assert "resolved" not in mw
    assert mw["boundary"]["cursors"] == [1200, 2400]
    assert mw["boundary"]["refs"] == MISSED_ENTRY["refs"]
    assert mw["boundary"]["cuts"][0]["evidence"]["phoneme"]["dip_db"] == 14.0
    dumped = SegValidateResponse.model_validate({"missed_waqf": detail["missed_waqf"]}).model_dump(
        mode="json", exclude_unset=True
    )
    assert dumped["missed_waqf"][0]["boundary"]["cuts"][1]["word"] == "الرحمن"


def test_labelled_missed_waqf_stays_listed_as_resolved():
    """Ignored (all WASL) or resolved by an edit from the card (split) — the
    item stays in the list with ``resolved: True`` so its labels are reviewable."""
    entries = [
        {
            "ref": "1",
            "segments": [
                _seg("u1", "1:1:1-1:1:4", ignored_categories=["missed_waqf"]),
                _seg("u2", "1:2:1-1:2:2", _resolved_by_edit=["missed_waqf"]),
                _seg("u3", "1:2:3-1:2:4"),
            ],
        }
    ]
    detail = _detail(
        entries, missed_waqf_map={"u1": MISSED_ENTRY, "u2": MISSED_ENTRY, "u3": MISSED_ENTRY}
    )
    by_uid = {it["segment_uid"]: it for it in detail["missed_waqf"]}
    assert by_uid["u1"]["resolved"] is True
    assert by_uid["u2"]["resolved"] is True
    assert "resolved" not in by_uid["u3"]
    SegValidateResponse.model_validate({"missed_waqf": detail["missed_waqf"]})


def test_validate_counts_open_missed_waqf_only_and_gates(monkeypatch, tmp_reciter_dir):
    from services import validation as val
    from services.storage.data_loader import load_detailed

    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas")
    segs = load_detailed(reciter)[0]["segments"]
    first, second = segs[0]["segment_uid"], segs[1]["segment_uid"]
    segs[1]["ignored_categories"] = ["missed_waqf"]
    monkeypatch.setattr(
        val,
        "load_missed_waqf",
        lambda _r: ({first: MISSED_ENTRY, second: MISSED_ENTRY}, {"kind": "missed_waqf"}),
    )

    full = val.validate_reciter_segments(reciter)
    assert full is not None
    assert full["category_counts"]["missed_waqf"] == 1
    assert [it["segment_uid"] for it in full["missed_waqf"]] == [first, second]
    assert full["missed_waqf"][1]["resolved"] is True
    assert full["missed_waqf_meta"] == {"kind": "missed_waqf"}

    gated = val.validate_reciter_segments(reciter, include_boundary_review=False)
    assert gated is not None
    assert "missed_waqf" not in gated
    assert "missed_waqf_meta" not in gated
    assert gated["category_counts"]["missed_waqf"] == 0
