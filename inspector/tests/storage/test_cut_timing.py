"""``timed_missed_waqf_doc`` — cut-timing silences land on Low Confidence Waqf cuts."""

from __future__ import annotations

from services.storage.cut_timing import timed_missed_waqf_doc

DOC = {
    "_meta": {"kind": "missed_waqf"},
    "by_uid": {
        "u1": {
            "cursors": [1000, 2000],
            "refs": ["2:1:1-2:1:2", "2:1:3-2:1:4", "2:1:5-2:1:9"],
            "cuts": [
                {"cursor_ms": 1000, "evidence": {"lattice": {"after_ref": "2:1:2"}}},
                {"cursor_ms": 2000, "evidence": {"lattice": {"after_ref": "2:1:4"}}},
            ],
        },
        "u2": {"cursors": [500], "cuts": [{"cursor_ms": 500, "evidence": {}}]},
    },
}


def _timing(*joins):
    return {"by_uid": {"u1": {"chapter": 2, "joins": list(joins)}}}


def _join(after_ref, start, cursor, end, source):
    return {
        "kind": "proposal",
        "after_ref": after_ref,
        "cursor_ms": cursor,
        "silence_start_ms": start,
        "silence_end_ms": end,
        "source": source,
    }


def _apply(timing: dict) -> dict:
    out = timed_missed_waqf_doc(DOC, timing)
    assert out is not None
    return out


def test_a_timed_cut_takes_the_silence_and_its_middle():
    out = _apply(_timing(_join("2:1:2", 1000, 1100, 1200, "psil")))
    cut = out["by_uid"]["u1"]["cuts"][0]
    assert (cut["silence_start_ms"], cut["cursor_ms"], cut["silence_end_ms"]) == (1000, 1100, 1200)
    assert cut["timing_source"] == "psil"
    assert out["by_uid"]["u1"]["cursors"] == [1100, 2000]
    assert "silence_start_ms" not in out["by_uid"]["u1"]["cuts"][1]
    assert out["by_uid"]["u2"] is DOC["by_uid"]["u2"]
    assert DOC["by_uid"]["u1"]["cursors"] == [1000, 2000]


def test_untimed_joins_and_disordered_cursors_change_nothing():
    none = _apply(_timing(_join("2:1:2", 1000, 1000, 1000, "none")))
    assert "silence_start_ms" not in none["by_uid"]["u1"]["cuts"][0]
    crossing = _apply(_timing(_join("2:1:2", 2050, 2100, 2150, "energy")))
    assert crossing["by_uid"]["u1"]["cursors"] == [1000, 2000]


def test_without_timing_the_doc_is_returned_as_is():
    assert timed_missed_waqf_doc(DOC, None) is DOC
    assert timed_missed_waqf_doc(None, _timing()) is None
