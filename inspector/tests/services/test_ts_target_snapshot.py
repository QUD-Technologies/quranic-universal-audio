"""Exact native report target resolution and staleness."""

from __future__ import annotations

import copy

from services.ts_reports import ts_target_snapshot as snapshots


def _doc() -> dict:
    return {
        "_meta": {"schema_version": 15},
        "readings": [
            {
                "id": "r1",
                "parts": [{"ref": "2:45", "t": [100, 500], "word_ids": [1, 2]}],
                "analysis": {
                    "schema_version": 2,
                    "result": {
                        "words": [
                            {"id": 1, "ref": "2:45:1", "text": "a", "sound_ids": [20]},
                            {"id": 2, "ref": "2:45:2", "text": "b", "sound_ids": [21]},
                        ],
                        "sounds": [
                            {"id": 20, "token": "a", "word_id": 1},
                            {"id": 21, "token": "b", "word_id": 2},
                        ],
                        "boundaries": [{"id": 5, "before": 1, "after": 2, "state": "join"}],
                    },
                },
                "source": {"schema_version": 2, "source": {}},
                "cells": {
                    "schema_version": 2,
                    "cell_view": {
                        "words": [
                            {
                                "word_id": 1,
                                "columns": [
                                    {
                                        "id": 10,
                                        "source_unit_ids": [100],
                                        "owned_sound_ids": [20],
                                        "presented_sound_ids": [],
                                    },
                                    {
                                        "id": 11,
                                        "source_unit_ids": [],
                                        "owned_sound_ids": [],
                                        "presented_sound_ids": [],
                                    },
                                ],
                                "groups": [{"column_ids": [10, 11], "sound_ids": [20]}],
                                "bridges": [{"merger_id": 30, "sound": {"sound_id": 20}}],
                            },
                            {"word_id": 2, "columns": [], "groups": [], "bridges": []},
                        ],
                        "boundaries": [
                            {
                                "boundary_id": 5,
                                "columns": [],
                                "groups": [],
                                "bridges": [],
                            }
                        ],
                    },
                },
                "timing": {
                    "words": [
                        {"word_id": 1, "start_ms": 100, "end_ms": 300},
                        {"word_id": 2, "start_ms": 350, "end_ms": 500},
                    ],
                    "sounds": [
                        {"sound_id": 20, "start_ms": 200, "end_ms": 300},
                        {"sound_id": 21, "start_ms": 350, "end_ms": 500},
                    ],
                    "animation_tokens": [
                        {
                            "id": 0,
                            "source_unit_ids": [100],
                            "start_ms": 100,
                            "end_ms": 180,
                        }
                    ],
                    "boundaries": [{"boundary_id": 5, "start_ms": 300, "end_ms": 350}],
                },
            }
        ],
    }


def _target(kind: str, target_id: str) -> dict:
    return {"reading_id": "r1", "kind": kind, "target_id": target_id}


def test_resolves_every_native_target_kind_exactly():
    doc = _doc()
    expected = {
        ("verse", "2:45"): (100, 500),
        ("word", "1"): (100, 300),
        ("sound", "20"): (200, 300),
        ("column", "10"): (100, 300),
        ("group", "10:11"): (200, 300),
        ("bridge", "30"): (200, 300),
        ("boundary", "5"): (300, 350),
    }
    for (kind, target_id), span in expected.items():
        snapshot = snapshots.resolve_target(doc, "2:45", _target(kind, target_id))
        assert snapshot is not None, (kind, target_id)
        assert snapshot["timing"] == {"start_ms": span[0], "end_ms": span[1]}
        assert snapshot["native_schema_version"] == 2
        assert snapshot["shard_schema_version"] == 15


def test_group_identity_is_ordered_native_column_ids():
    doc = _doc()
    assert snapshots.resolve_target(doc, "2:45", _target("group", "10:11"))
    assert snapshots.resolve_target(doc, "2:45", _target("group", "11:10")) is None


def test_reading_and_verse_ownership_are_strict():
    doc = _doc()
    assert snapshots.resolve_target(doc, "2:45", _target("word", "1"))
    assert (
        snapshots.resolve_target(
            doc, "2:45", {"reading_id": "missing", "kind": "word", "target_id": "1"}
        )
        is None
    )
    assert snapshots.resolve_target(doc, "2:46", _target("word", "1")) is None
    assert snapshots.resolve_target(doc, "2:45", _target("column", "999")) is None


def test_non_timing_reports_stale_on_native_change():
    doc = _doc()
    target = _target("sound", "20")
    old = snapshots.resolve_target(doc, "2:45", target)
    changed = copy.deepcopy(doc)
    changed["readings"][0]["analysis"]["result"]["sounds"][0]["token"] = "changed"
    report = {
        "verse_key": "2:45",
        "category": "phonemes",
        "target": target,
        "snapshot": old,
    }
    assert snapshots.is_stale_after_restamp(report, changed)


def test_timing_reports_only_compare_the_selected_axes():
    doc = _doc()
    target = _target("word", "1")
    old = snapshots.resolve_target(doc, "2:45", target)
    changed = copy.deepcopy(doc)
    timing = changed["readings"][0]["timing"]["words"][0]
    timing["start_ms"] += 10_000
    onset = {
        "verse_key": "2:45",
        "category": "timing",
        "target": target,
        "snapshot": old,
        "onset": "late",
        "offset": None,
    }
    offset = {**onset, "onset": None, "offset": "early"}
    assert snapshots.is_stale_after_restamp(onset, changed)
    assert not snapshots.is_stale_after_restamp(offset, changed)


def test_v11_documents_never_resolve():
    doc = _doc()
    doc["_meta"]["schema_version"] = 11
    assert snapshots.resolve_target(doc, "2:45", _target("word", "1")) is None
