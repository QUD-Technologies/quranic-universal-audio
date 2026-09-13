"""``close_absent_word_gaps.close_part_gaps`` — the in-place rule for shards on the bucket."""

from __future__ import annotations

import copy

from close_absent_word_gaps import close_part_gaps


def _shard(readings: list[dict], padding: str | None = "forward") -> dict:
    meta = {"schema_version": 14, "profile": "word", "chapter": 57}
    if padding is not None:
        meta["padding"] = padding
    return {"_meta": meta, "readings": readings}


def _reading(parts: list[list], words: list[list]) -> dict:
    return {"id": "r1", "parts": parts, "words": words, "boundaries": [[1, None]] * len(words)}


def test_an_intra_part_gap_moves_the_earlier_end_up_to_the_later_start():
    shard = _shard(
        [
            _reading(
                [["57:23", 534900, 542420, 0, 3]],
                [
                    ["57:23:8", "x", 537225, 538795],
                    ["57:23:9", "x", 538795, 539565],
                    ["57:23:10", "x", 539765, 540585],
                ],
            )
        ]
    )
    closed = close_part_gaps(shard)
    rows = shard["readings"][0]["words"]
    assert closed == ["57:23:9|57:23:10 200ms"]
    assert rows[1][3] == 539765
    assert rows[0][3] == 538795 and rows[2] == ["57:23:10", "x", 539765, 540585]


def test_a_gap_between_two_parts_is_a_real_pause_and_stays():
    shard = _shard(
        [
            _reading(
                [["112:1", 0, 1000, 0, 2], ["112:1", 1400, 2400, 2, 2]],
                [
                    ["112:1:1", "x", 0, 500],
                    ["112:1:2", "x", 500, 1000],
                    ["112:1:3", "x", 1400, 1900],
                    ["112:1:4", "x", 1900, 2400],
                ],
            )
        ]
    )
    before = copy.deepcopy(shard)
    assert close_part_gaps(shard) == []
    assert shard == before


def test_a_shard_not_forward_padded_is_left_alone():
    reading = _reading(
        [["57:23", 0, 1000, 0, 2]],
        [["57:23:1", "x", 0, 480], ["57:23:2", "x", 500, 1000]],
    )
    for padding in (None, "symmetric", "none"):
        shard = _shard([copy.deepcopy(reading)], padding)
        before = copy.deepcopy(shard)
        assert close_part_gaps(shard) == []
        assert shard == before
