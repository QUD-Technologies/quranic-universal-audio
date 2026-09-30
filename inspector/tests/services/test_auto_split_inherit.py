"""Pieces an edit made or reshaped inherit their ancestor's cross-verse cuts by word."""

from qua_shared.join_verdicts import contained_verdicts
from services.segments.auto_split import _cursor_by_word, _fits, _inherited

WORD_COUNTS = {(20, 29): 5, (20, 30): 2, (20, 31): 3}
PARENT = {
    "cursors": [228965, 231685],
    "refs": ["20:29:1-20:29:5", "20:30:1-20:30:2", "20:31:1-20:31:3"],
    "kind": "cross_verse",
}
PIECE = {
    "segment_uid": "p",
    "time_start": 222975,
    "time_end": 231155,
    "matched_ref": "20:29:1-20:30:2",
}


def test_a_piece_keeping_its_parents_uid_no_longer_fits_the_parent_entry():
    assert not _fits(PARENT, PIECE)
    assert _fits({**PARENT, "kind": "missed_waqf"}, PIECE)


def test_a_piece_takes_the_cut_at_each_of_its_verse_ends():
    hit = _inherited(PIECE, _cursor_by_word({"parent": PARENT}), WORD_COUNTS)
    assert hit == {
        "cursors": [228965],
        "refs": ["20:29:1-20:29:5", "20:30:1-20:30:2"],
        "kind": "cross_verse",
    }
    assert _fits(hit, PIECE)


def test_no_entry_when_a_verse_end_has_no_cut_inside_the_piece():
    moved = {**PIECE, "time_start": 229000}
    assert _inherited(moved, _cursor_by_word({"parent": PARENT}), WORD_COUNTS) is None


def test_answers_stay_with_their_words():
    seg = {"time_start": 350, "time_end": 1000, "matched_ref": "2:1:1-2:1:9"}
    answers = [
        {"at_ms": 300, "after_ref": "2:1:2", "verdict": "wasl"},
        {"at_ms": 700, "after_ref": "2:1:9", "verdict": "waqf"},
        {"at_ms": 600, "after_ref": "2:1:5", "verdict": "waqf"},
        {"at_ms": 900, "after_ref": "2:2:1", "verdict": "wasl"},
    ]
    assert contained_verdicts(seg, answers) == [
        {"at_ms": 351, "after_ref": "2:1:2", "verdict": "wasl"},
        {"at_ms": 1000, "after_ref": "2:1:9", "verdict": "waqf"},
    ]
