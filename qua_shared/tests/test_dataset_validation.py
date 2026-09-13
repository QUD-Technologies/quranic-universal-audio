"""intra_segment_gap is time-window based, so reciter lookbacks (repeated word
indices across passes) don't produce phantom gaps — while a real gap inside one
pass is still caught.
"""

from __future__ import annotations

from qua_shared.dataset_validation import (
    check_canonical_uniqueness,
    check_intra_segment_gapless,
    fatal_violations,
)


def test_lookback_repeated_word_index_is_not_a_gap():
    # Reciter recites words 1-2, then loops back and re-recites 2-3. Word index 2
    # appears twice. Each pass is internally gapless; the ~300ms between passes is
    # a between-segment gap (allowed). An index-keyed check would pair pass-1 w1
    # with the pass-2 w2 occurrence and report a phantom gap.
    verse = {
        "words": [[1, 0, 100], [2, 100, 200], [2, 500, 600], [3, 600, 700]],
        "segments": [
            (1, 2, 0, 200),  # pass 1: words 1-2, time [0,200]
            (2, 3, 500, 700),  # pass 2: words 2-3, time [500,700]
        ],
    }
    assert check_intra_segment_gapless("2:35", verse) == []


def test_real_intra_segment_gap_is_still_caught():
    # Single pass, genuine 50ms gap between word 1 (ends 100) and word 2 (starts 150).
    verse = {
        "words": [[1, 0, 100], [2, 150, 200]],
        "segments": [(1, 2, 0, 200)],
    }
    out = check_intra_segment_gapless("1:1", verse)
    assert len(out) == 1
    assert out[0]["violation"] == "intra_segment_gap"
    assert out[0]["gap_ms"] == 50


def test_canonical_uniqueness_flags_zero_and_double_canonical_refs():
    violations = check_canonical_uniqueness(
        [("1:1", True), ("1:1", False), ("1:2", False), ("1:3", True), ("1:3", True)]
    )
    assert [(v["ref"], v["canonical_rows"]) for v in violations] == [("1:2", 0), ("1:3", 2)]
    assert all(v["violation"] == "canonical_uniqueness" for v in violations)
    assert fatal_violations(violations) == violations
