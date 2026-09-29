"""Cross-verse items carry their inner verse ends with the stored answers."""

from services.validation.detail import annotate_cross_verse_joins

WORD_COUNTS = {(2, 1): 4, (2, 2): 3, (2, 3): 5}


def _run(verdicts):
    seg = {"segment_uid": "u", "matched_ref": "2:1:1-2:3:2", "join_verdicts": verdicts}
    item = {"segment_uid": "u"}
    annotate_cross_verse_joins([item], [{"segments": [seg]}], WORD_COUNTS)
    return item


def test_lists_every_inner_verse_end_with_its_answer():
    item = _run([{"at_ms": 900, "after_ref": "2:1:4", "verdict": "wasl"}])
    assert item["verse_joins"] == [
        {"after_ref": "2:1:4", "verdict": "wasl"},
        {"after_ref": "2:2:3", "verdict": None},
    ]
    assert "resolved" not in item


def test_all_answered_resolves():
    item = _run(
        [
            {"at_ms": 900, "after_ref": "2:1:4", "verdict": "wasl"},
            {"at_ms": 1500, "after_ref": "2:2:3", "verdict": "wasl"},
        ]
    )
    assert item["resolved"] is True


def test_cross_surah_span_has_no_joins():
    seg = {"segment_uid": "u", "matched_ref": "1:7:1-2:1:1"}
    item = {"segment_uid": "u"}
    annotate_cross_verse_joins([item], [{"segments": [seg]}], WORD_COUNTS)
    assert item["verse_joins"] == []
