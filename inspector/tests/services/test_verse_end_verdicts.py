"""``verse_end_verdicts`` — the align pipeline's verse-end verdicts as reviewer-shaped saves."""

from __future__ import annotations

import itertools
from collections import Counter

import numpy as np

from qua_shared.audio import levels
from services.segments.verse_end_verdicts import chapter_save

ENDS = {"2:1:1-2:3:2": ["2:1:4", "2:2:3"], "2:4:1-2:5:2": ["2:4:5"]}


def _entries():
    return [
        {
            "ref": "2",
            "audio": "a.mp3",
            "segments": [
                {
                    "segment_uid": "cv",
                    "time_start": 0,
                    "time_end": 3000,
                    "matched_ref": "2:1:1-2:3:2",
                },
                {
                    "segment_uid": "w",
                    "time_start": 3000,
                    "time_end": 5000,
                    "matched_ref": "2:4:1-2:5:2",
                },
            ],
        }
    ]


def _save(by_uid):
    ids = (f"id{i}" for i in itertools.count())
    tally: Counter = Counter()
    payload = chapter_save(
        _entries(),
        2,
        by_uid,
        lambda ref: ENDS.get(ref, []),
        lambda _ref: 2,
        lambda: next(ids),
        tally,
    )
    return payload, tally


def _item(start, end, *joins):
    return {"chapter": 2, "start_ms": start, "end_ms": end, "joins": list(joins)}


def _join(after, nxt, verdict, at):
    return {"after_ref": after, "next_ref": nxt, "verdict": verdict, "cursor_ms": at}


def test_waqf_splits_and_wasl_lands_on_its_piece():
    payload, tally = _save(
        {
            "cv": _item(
                0,
                3000,
                _join("2:1:4", "2:2:1", "wasl", 1000),
                _join("2:2:3", "2:3:1", "waqf", 2000),
            )
        }
    )
    segs = payload["segments"]
    assert [(s["time_start"], s["time_end"], s["matched_ref"]) for s in segs[:2]] == [
        (0, 2000, "2:1:1-2:2:3"),
        (2000, 3000, "2:3:1-2:3:2"),
    ]
    assert segs[0]["is_wasl"] is False
    assert segs[0]["join_verdicts"] == [{"after_ref": "2:1:4", "at_ms": 1000, "verdict": "wasl"}]
    assert "join_verdicts" not in segs[1]
    (op,) = payload["operations"]
    assert op["op_type"] == "split_segment" and op["fix_kind"] == "auto_fix"
    assert op["patch"]["insertedIds"] == [segs[1]["segment_uid"]]
    assert tally == {"waqf": 1, "wasl": 1}


def test_all_wasl_stores_answers_and_settles():
    payload, _ = _save({"w": _item(3000, 5000, _join("2:4:5", "2:5:1", "wasl", 4000))})
    seg = payload["segments"][1]
    assert seg["join_verdicts"] == [{"after_ref": "2:4:5", "at_ms": 4000, "verdict": "wasl"}]
    assert seg["ignored_categories"] == ["cross_verse"]
    assert payload["operations"][0]["op_type"] == "ignore_issue"


def test_moved_or_answered_segments_are_skipped():
    payload, tally = _save({"w": _item(3000, 4900, _join("2:4:5", "2:5:1", "wasl", 4000))})
    assert payload["operations"] == [] and tally == {"moved": 1}
    answered, _ = _save({"w": _item(3000, 5000, _join("2:4:5", "2:5:1", "wasl", 4000))})
    entries = _entries()
    entries[0]["segments"][1] = answered["segments"][1]
    ids = iter(["x"])
    tally = Counter()
    again = chapter_save(
        entries,
        2,
        {"w": _item(3000, 5000, _join("2:4:5", "2:5:1", "wasl", 4000))},
        lambda ref: ENDS.get(ref, []),
        lambda _r: 2,
        lambda: next(ids),
        tally,
    )
    assert again["operations"] == [] and tally == {"already_answered": 1}


def test_levels_blob_round_trips():
    lv = np.array([-70, -20, -5, 0], dtype=np.int8)
    assert levels.unpack(levels.pack(lv)).tolist() == [-70, -20, -5, 0]
