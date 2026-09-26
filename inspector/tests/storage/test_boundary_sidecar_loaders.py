"""``load_hidden_pause`` / ``load_missed_waqf`` / ``load_false_split`` /
``load_unmarked_wasl`` — sidecar readers + cache."""

from __future__ import annotations

import json

import pytest

from services.storage import cache
from services.storage.data_loader import (
    load_false_split,
    load_hidden_pause,
    load_missed_waqf,
    load_unmarked_wasl,
)

SLUG = "loader_reciter"


@pytest.fixture
def reciter_dir(tmp_path, tmp_reciter_dir):
    d = tmp_path / "reciters" / SLUG
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(d, name: str, doc: dict) -> None:
    (d / name).write_text(json.dumps(doc), encoding="utf-8")


def test_absent_sidecars_yield_empty_and_cache(reciter_dir):
    assert load_hidden_pause(SLUG) == ({}, None)
    assert load_false_split(SLUG) == ({}, None)
    assert load_unmarked_wasl(SLUG) == ({}, None)
    assert load_missed_waqf(SLUG) == ({}, None)
    assert cache.get_seg_hidden_pause(SLUG) == ({}, None)
    assert cache.get_seg_missed_waqf(SLUG) == ({}, None)
    assert cache.get_seg_false_split(SLUG) == ({}, None)
    assert cache.get_seg_unmarked_wasl(SLUG) == ({}, None)


def test_sidecars_parse_by_uid_and_meta(reciter_dir):
    _write(
        reciter_dir,
        "hidden_pause_v1.json",
        {"_meta": {"kind": "hidden_pause", "segments": 1}, "by_uid": {"u1": {"cursors": [10]}}},
    )
    _write(
        reciter_dir,
        "false_split_v1.json",
        {"_meta": {"kind": "false_split"}, "by_uid": {"u9": {"next_uid": "u10"}}},
    )
    assert load_hidden_pause(SLUG) == (
        {"u1": {"cursors": [10]}},
        {"kind": "hidden_pause", "segments": 1},
    )
    assert load_false_split(SLUG) == ({"u9": {"next_uid": "u10"}}, {"kind": "false_split"})


def test_unmarked_wasl_sidecar_parses_and_invalidates(reciter_dir):
    _write(
        reciter_dir,
        "unmarked_wasl_v1.json",
        {"_meta": {"kind": "unmarked_wasl"}, "by_uid": {"u3": {"next_uid": "u4", "gap_ms": 0}}},
    )
    assert load_unmarked_wasl(SLUG) == (
        {"u3": {"next_uid": "u4", "gap_ms": 0}},
        {"kind": "unmarked_wasl"},
    )
    cache.invalidate_seg_caches(SLUG)
    assert cache.get_seg_unmarked_wasl(SLUG) is None


def test_malformed_by_uid_degrades_to_empty(reciter_dir):
    _write(reciter_dir, "hidden_pause_v1.json", {"_meta": "nope", "by_uid": ["not", "a", "dict"]})
    assert load_hidden_pause(SLUG) == ({}, None)


def test_cache_hit_skips_disk(reciter_dir):
    _write(reciter_dir, "false_split_v1.json", {"by_uid": {"a": {}}})
    assert load_false_split(SLUG)[0] == {"a": {}}
    _write(reciter_dir, "false_split_v1.json", {"by_uid": {"b": {}}})
    assert load_false_split(SLUG)[0] == {"a": {}}
    cache.invalidate_seg_caches(SLUG)
    assert load_false_split(SLUG)[0] == {"b": {}}


def test_missed_waqf_sidecar_parses_and_invalidates(reciter_dir):
    _write(
        reciter_dir,
        "missed_waqf_v1.json",
        {"_meta": {"kind": "missed_waqf"}, "by_uid": {"u5": {"cursors": [1200], "score": 1300}}},
    )
    assert load_missed_waqf(SLUG) == (
        {"u5": {"cursors": [1200], "score": 1300}},
        {"kind": "missed_waqf"},
    )
    cache.invalidate_seg_caches(SLUG)
    assert cache.get_seg_missed_waqf(SLUG) is None
