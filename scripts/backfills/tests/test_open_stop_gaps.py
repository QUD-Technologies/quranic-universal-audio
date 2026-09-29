"""``open_stop_gaps`` — abutting stops are trimmed to their measured silence."""

from __future__ import annotations

from itertools import count

from open_stop_gaps import chapter_save, plan_gaps


def _chapter(ref: str) -> int:
    return int(ref.split(":")[0])


def _entries() -> list[dict]:
    return [
        {
            "ref": "2",
            "audio": "a.mp3",
            "segments": [
                {
                    "segment_uid": "L",
                    "time_start": 0,
                    "time_end": 1000,
                    "matched_ref": "2:1:1-2:1:4",
                    "join_verdicts": [{"at_ms": 1000, "after_ref": "2:1:4", "verdict": "waqf"}],
                },
                {
                    "segment_uid": "R",
                    "time_start": 1000,
                    "time_end": 2000,
                    "matched_ref": "2:1:5-2:1:9",
                },
            ],
        }
    ]


def _timing(**join) -> dict:
    base = {
        "kind": "stop",
        "after_ref": "2:1:4",
        "cursor_ms": 1000,
        "silence_start_ms": 900,
        "silence_end_ms": 1100,
        "source": "psil",
    }
    return {"L": {"chapter": 2, "joins": [{**base, **join}]}}


def _uuid():
    n = count()
    return lambda: f"id{next(n)}"


def test_a_timed_stop_is_trimmed_to_its_silence_with_the_verdict_on_the_edge():
    entries = _entries()
    gaps, tally = plan_gaps(entries, _timing(), _chapter)
    assert tally == {"ok": 1}
    payload = chapter_save(entries, 2, gaps, _uuid(), _chapter)
    left, right = payload["segments"]
    assert (left["time_end"], right["time_start"]) == (900, 1100)
    assert left["join_verdicts"] == [{"at_ms": 900, "after_ref": "2:1:4", "verdict": "waqf"}]
    assert [op["op_type"] for op in payload["operations"]] == ["trim_segment", "trim_segment"]
    assert payload["operations"][0]["targets_after"][0]["time_end"] == 900
    assert entries[0]["segments"][0]["time_end"] == 1000


def test_untimed_moved_and_cramped_stops_are_skipped():
    assert plan_gaps(_entries(), _timing(source="none"), _chapter) == ([], {"untimed": 1})
    assert plan_gaps(_entries(), _timing(after_ref="2:1:3"), _chapter) == ([], {"word_moved": 1})
    cramped = _timing(silence_start_ms=20)
    assert plan_gaps(_entries(), cramped, _chapter) == ([], {"too_short": 1})
    apart = _entries()
    apart[0]["segments"][1]["time_start"] = 1050
    assert plan_gaps(apart, _timing(), _chapter) == ([], {"not_abutting": 1})


def test_the_gap_spans_the_boundary_and_a_nearby_silence():
    later = _timing(silence_start_ms=1150, silence_end_ms=1250)
    gaps, tally = plan_gaps(_entries(), later, _chapter)
    assert tally == {"ok": 1}
    left, right = chapter_save(_entries(), 2, gaps, _uuid(), _chapter)["segments"]
    assert (left["time_end"], right["time_start"]) == (1000, 1250)
    earlier = _timing(silence_start_ms=750, silence_end_ms=820)
    left, right = chapter_save(
        _entries(), 2, plan_gaps(_entries(), earlier, _chapter)[0], _uuid(), _chapter
    )["segments"]
    assert (left["time_end"], right["time_start"]) == (750, 1000)
    far = _timing(silence_start_ms=1400, silence_end_ms=1500)
    assert plan_gaps(_entries(), far, _chapter) == ([], {"silence_far_from_boundary": 1})
