"""``apply_verse_end_verdicts`` — waqf verse ends become a reviewer-shaped split."""

from __future__ import annotations

from itertools import count

from apply_verse_end_verdicts import chapter_save, plan_splits, unlogged_splits


def _chapter(ref: str) -> int:
    return int(ref.split(":")[0])


def _entries() -> list[dict]:
    return [
        {
            "ref": "2",
            "audio": "a.mp3",
            "segments": [
                {
                    "segment_uid": "u0",
                    "time_start": 0,
                    "time_end": 900,
                    "matched_ref": "2:1:1-2:1:1",
                },
                {
                    "segment_uid": "u1",
                    "time_start": 1000,
                    "time_end": 9000,
                    "matched_ref": "2:2:1-2:4:3",
                    "is_wasl": True,
                    "wrap_word_ranges": [["2:2:1", "2:2:3"]],
                },
            ],
        }
    ]


def _applied(**over) -> dict:
    item = {
        "chapter": 2,
        "start_ms": 1000,
        "end_ms": 9000,
        "joins": [
            {"after_ref": "2:3:9", "next_ref": "2:4:1", "verdict": "waqf", "cursor_ms": 6000},
            {"after_ref": "2:2:6", "next_ref": "2:3:1", "verdict": "waqf", "cursor_ms": 3000},
            {"after_ref": "2:2:2", "next_ref": "2:2:3", "verdict": "wasl", "cursor_ms": 2000},
        ],
    }
    return {"u1": {**item, **over}}


def test_waqf_verdicts_plan_one_split_with_a_ref_per_piece():
    splits, skipped = plan_splits(_entries(), _applied(), _chapter)
    assert skipped == {}
    (split,) = splits
    assert split.cursors == (3000, 6000)
    assert split.refs == ("2:2:1-2:2:6", "2:3:1-2:3:9", "2:4:1-2:4:3")


def test_a_moved_segment_is_skipped():
    splits, skipped = plan_splits(_entries(), _applied(end_ms=8000), _chapter)
    assert not splits
    assert skipped == {"moved": 1}


def test_the_save_mirrors_a_reviewer_split():
    splits, _ = plan_splits(_entries(), _applied(), _chapter)
    ids = count()
    payload = chapter_save(_entries(), 2, splits, lambda: f"n{next(ids)}", _chapter)
    segs = payload["segments"]
    assert payload["full_replace"]
    assert [s["segment_uid"] for s in segs] == ["u0", "u1", "n0", "n1"]
    assert [(s["time_start"], s["time_end"]) for s in segs[1:]] == [
        (1000, 3000),
        (3000, 6000),
        (6000, 9000),
    ]
    assert [s["is_wasl"] for s in segs[1:]] == [False, False, True]
    assert all("wrap_word_ranges" not in s for s in segs[1:])
    (op,) = payload["operations"]
    assert (op["op_type"], op["op_context_category"], op["fix_kind"]) == (
        "split_segment",
        "cross_verse",
        "auto_fix",
    )
    assert [t["segment_uid"] for t in op["targets_after"]] == ["u1", "n0", "n1"]
    assert op["patch"]["insertedIds"] == ["n0", "n1"]
    assert [t["index_at_save"] for t in op["targets_after"]] == [1, 2, 3]


def test_an_applied_split_missing_from_history_is_logged():
    splits, _ = plan_splits(_entries(), _applied(), _chapter)
    ids = count()
    payload = chapter_save(_entries(), 2, splits, lambda: f"n{next(ids)}", _chapter)
    live = [{"ref": "2", "audio": "a.mp3", "segments": payload["segments"]}]
    ops = unlogged_splits(live, _applied(), [], _chapter, lambda: "op")
    (op,) = ops[2]
    assert op["targets_before"][0]["matched_ref"] == "2:2:1-2:4:3"
    assert op["targets_before"][0]["time_end"] == 9000
    assert [t["segment_uid"] for t in op["targets_after"]] == ["u1", "n0", "n1"]
    logged = [
        {"operations": [{"op_type": "split_segment", "targets_before": [{"segment_uid": "u1"}]}]}
    ]
    assert unlogged_splits(live, _applied(), logged, _chapter, lambda: "op") == {}
