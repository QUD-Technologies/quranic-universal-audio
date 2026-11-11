"""``store_verse_end_verdicts`` — the lab's WASL verse ends become stored answers."""

from __future__ import annotations

from itertools import count

from store_verse_end_verdicts import chapter_save, plan_verdicts

#: Verse 1 has 4 words, verse 2 has 3: a segment 2:1:1-2:3:2 ends verses 1 and 2 inside it.
ENDS = {"2:1:1-2:3:2": ["2:1:4", "2:2:3"], "2:4:1-2:5:2": ["2:4:3"]}


def _chapter(ref: str) -> int:
    return int(ref.split(":")[0])


def _ends(ref: str) -> list[str]:
    return ENDS.get(ref, [])


def _entries(**extra) -> list[dict]:
    return [
        {
            "ref": "2",
            "audio": "a.mp3",
            "segments": [
                {
                    "segment_uid": "A",
                    "time_start": 0,
                    "time_end": 5000,
                    "matched_ref": "2:1:1-2:3:2",
                    "confidence": 0.8,
                    **extra,
                },
                {
                    "segment_uid": "B",
                    "time_start": 5000,
                    "time_end": 8000,
                    "matched_ref": "2:4:1-2:5:2",
                },
            ],
        }
    ]


def _join(after_ref: str, cursor_ms: int, verdict: str = "wasl") -> dict:
    return {"after_ref": after_ref, "cursor_ms": cursor_ms, "verdict": verdict}


def test_every_verse_end_answered_settles_the_segment():
    applied = {"A": {"joins": [_join("2:1:4", 1500), _join("2:2:3", 3200)]}}
    stores, tally = plan_verdicts(_entries(), applied, _ends, _chapter)
    [store] = stores
    assert store.settle and tally == {"stored": 2, "segments_settled": 1}
    ids = count()
    payload = chapter_save(_entries(), 2, stores, lambda: f"op{next(ids)}", _chapter)
    seg = payload["segments"][0]
    assert seg["join_verdicts"] == [
        {"after_ref": "2:1:4", "at_ms": 1500, "verdict": "wasl"},
        {"after_ref": "2:2:3", "at_ms": 3200, "verdict": "wasl"},
    ]
    assert seg["ignored_categories"] == ["cross_verse"] and seg["confidence"] == 1.0
    [op] = payload["operations"]
    assert (op["op_type"], op["op_context_category"], op["fix_kind"]) == (
        "ignore_issue",
        "cross_verse",
        "auto_fix",
    )
    assert op["targets_after"][0]["join_verdicts"] == seg["join_verdicts"]
    assert "join_verdicts" not in op["targets_before"][0]


def test_an_asked_verse_end_left_open_stores_answers_without_settling():
    applied = {"A": {"joins": [_join("2:1:4", 1500)]}}
    [store], tally = plan_verdicts(_entries(), applied, _ends, _chapter)
    assert not store.settle and tally["segments_partial"] == 1
    seg = chapter_save(_entries(), 2, [store], lambda: "op", _chapter)["segments"][0]
    assert "ignored_categories" not in seg and seg["confidence"] == 0.8


def test_a_reviewer_answer_completes_the_settlement():
    reviewed = {"join_verdicts": [{"after_ref": "2:2:3", "at_ms": 3200, "verdict": "waqf"}]}
    applied = {"A": {"joins": [_join("2:1:4", 1500), _join("2:2:3", 3100)]}}
    [store], tally = plan_verdicts(_entries(**reviewed), applied, _ends, _chapter)
    assert store.settle and tally["already_stored"] == 1
    assert [v["after_ref"] for v in store.verdicts] == ["2:1:4"]


def test_stale_joins_are_skipped():
    applied = {
        "A": {"joins": [_join("2:1:3", 1500), _join("2:2:3", 9000), _join("2:1:4", 1500, "waqf")]},
        "gone": {"joins": [_join("2:9:1", 100)]},
    }
    stores, tally = plan_verdicts(_entries(), applied, _ends, _chapter)
    assert stores == []
    assert tally == {"ref_moved": 1, "cursor_outside": 1, "gone": 1}
