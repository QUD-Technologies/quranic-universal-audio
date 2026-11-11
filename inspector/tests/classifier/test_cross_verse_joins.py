"""Cross-verse items carry their inner verse ends with their answers by word."""

from services.validation.detail import annotate_cross_verse_joins

WORD_COUNTS = {(2, 1): 4, (2, 2): 3, (2, 3): 5}


def _run(segs, ref="2:1:1-2:3:2", recheck=()) -> dict:
    item: dict = {"segment_uid": "u", "ref": ref}
    annotate_cross_verse_joins([item], [{"segments": segs}], WORD_COUNTS, recheck)
    return item


def _seg(verdicts=(), **kw):
    return {
        "segment_uid": "u",
        "time_start": 0,
        "time_end": 2000,
        "matched_ref": "2:1:1-2:3:2",
        "join_verdicts": list(verdicts),
        **kw,
    }


def test_lists_every_inner_verse_end_with_its_answer():
    item = _run([_seg([{"at_ms": 900, "after_ref": "2:1:4", "verdict": "wasl"}])])
    assert item["verse_joins"] == [
        {"after_ref": "2:1:4", "verdict": "wasl"},
        {"after_ref": "2:2:3", "verdict": None},
    ]
    assert "resolved" not in item


def test_all_answered_resolves():
    item = _run(
        [
            _seg(
                [
                    {"at_ms": 900, "after_ref": "2:1:4", "verdict": "wasl"},
                    {"at_ms": 1500, "after_ref": "2:2:3", "verdict": "wasl"},
                ]
            )
        ]
    )
    assert item["resolved"] is True


def test_a_split_answers_its_verse_end_by_the_piece_ending_there():
    left = {
        "segment_uid": "u",
        "time_start": 0,
        "time_end": 900,
        "matched_ref": "2:1:1-2:2:3",
        "is_wasl": False,
    }
    right = {"segment_uid": "r", "time_start": 900, "time_end": 1200, "matched_ref": "2:3:1-2:3:2"}
    item = _run([left, right], ref="2:1:1-2:2:3")
    assert item["verse_joins"] == [{"after_ref": "2:1:4", "verdict": None}]
    left["join_verdicts"] = [{"at_ms": 500, "after_ref": "2:1:4", "verdict": "wasl"}]
    assert _run([left, right], ref="2:1:1-2:2:3")["resolved"] is True


def test_an_ignored_segment_reads_wasl():
    item = _run([_seg(ignored_categories=["cross_verse"])])
    assert [j["verdict"] for j in item["verse_joins"]] == ["wasl", "wasl"]


def test_cross_surah_span_has_no_joins():
    item = _run([{"segment_uid": "u", "matched_ref": "1:7:1-2:1:1"}], ref="1:7:1-2:1:1")
    assert item["verse_joins"] == []


def test_an_earlier_rendition_ending_on_the_verse_end_is_not_an_answer():
    earlier = {"segment_uid": "e", "time_start": 0, "time_end": 500, "matched_ref": "2:1:1-2:1:4"}
    item = _run([earlier, _seg(time_start=800, time_end=2800)])
    assert [j["verdict"] for j in item["verse_joins"]] == [None, None]
