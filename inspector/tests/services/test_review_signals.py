"""Reciter-picker review signals: the light scan agrees with the validator, the cache drops on
save, and the route serves what is cached while the sweep fills the rest."""

from __future__ import annotations

import json

import pytest

from services.storage import cache, data_dir, storage_paths
from services.validation import review_signals, validate_reciter_segments

FIXTURES = ("112-ikhlas", "113-falaq", "synthetic-classifier", "synthetic-structural")
THRESHOLD = 0.85


def _uids(slug: str) -> list[str]:
    raw = data_dir.read_detailed_bytes(slug)
    assert raw is not None
    doc = json.loads(raw)
    return [s["segment_uid"] for e in doc["entries"] for s in e["segments"] if s.get("segment_uid")]


def _install_with_sidecar(tmp_reciter_dir, slug: str, fixture: str) -> None:
    """The fixture plus a Low Confidence v2 sidecar failing every other segment."""
    tmp_reciter_dir.install(slug, fixture)
    failures = _uids(slug)[::2]
    doc = {"_meta": {"method": "neural_checks"}, "failures": failures, "by_uid": {}}
    from services.storage.hf_bucket import get_backend

    get_backend().write_json_atomic(storage_paths.low_confidence_path(slug), doc)
    cache.invalidate_seg_caches(slug)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_scan_matches_the_validator(tmp_reciter_dir, fixture):
    slug = "fixture_reciter"
    _install_with_sidecar(tmp_reciter_dir, slug, fixture)
    scanned = review_signals.compute(slug)
    assert cache.get_seg_validate_cache(slug) is None  # the scan path, not the cached payload
    result = validate_reciter_segments(slug)
    assert result is not None
    assert scanned == review_signals.counts_from_validate(result, THRESHOLD)


def test_cached_validate_payload_is_used(tmp_reciter_dir):
    slug = "fixture_reciter"
    _install_with_sidecar(tmp_reciter_dir, slug, "synthetic-classifier")
    cache.set_seg_validate_cache(
        slug,
        {"low_confidence": [{"confidence": 0.5}, {"confidence": 0.9}], "low_confidence_v2": [{}]},
    )
    assert review_signals.compute(slug) == {"low_confidence": 1, "low_confidence_v2": 1}


def test_ignored_segments_do_not_count():
    seg = {"matched_ref": "1:1:1-1:1:2", "confidence": 0.5, "segment_uid": "u1"}
    ignored = {
        **seg,
        "segment_uid": "u2",
        "ignored_categories": ["low_confidence", "low_confidence_v2"],
    }
    entries = [{"segments": [seg, ignored, {"matched_ref": "", "confidence": 0.1}]}]
    assert review_signals.scan_counts(entries, THRESHOLD, {"u1", "u2"}) == {
        "low_confidence": 1,
        "low_confidence_v2": 1,
    }


def test_save_invalidation_drops_the_signal():
    cache.set_review_signals("r", {"low_confidence": 2, "low_confidence_v2": 0})
    cache.pop_seg_caches_affected_by_segment_edit("r")
    assert cache.get_review_signals("r") is None
    cache.set_review_signals("r", {"low_confidence": 2, "low_confidence_v2": 0})
    cache.invalidate_seg_caches("r")
    assert cache.get_review_signals("r") is None


def test_route_serves_cache_and_reports_pending(flask_client, tmp_reciter_dir, monkeypatch):
    slug = "fixture_reciter"
    _install_with_sidecar(tmp_reciter_dir, slug, "synthetic-classifier")
    started: list[list[str]] = []
    monkeypatch.setattr(review_signals, "warm", lambda slugs: started.append(slugs))

    first = flask_client.get("/api/seg/review-signals")
    assert first.status_code == 200
    body = first.get_json()
    assert body["pending"] >= 1 and slug not in body["signals"]
    assert started and slug in started[0]

    review_signals._sweep([slug])
    second = flask_client.get("/api/seg/review-signals").get_json()
    assert second["signals"][slug] == review_signals.compute(slug)


def test_resolved_by_edit_hides_a_low_confidence_v2_item(tmp_reciter_dir, monkeypatch):
    slug = "fixture_reciter"
    _install_with_sidecar(tmp_reciter_dir, slug, "synthetic-classifier")
    before = review_signals.compute(slug)
    assert before is not None and before["low_confidence_v2"] >= 1
    edited = _uids(slug)[0]  # failed in the sidecar (every other uid from the first)
    monkeypatch.setattr(
        review_signals,
        "build_resolved_by_edit_index",
        lambda _slug: {edited: {"low_confidence_v2"}},
    )
    after = review_signals.compute(slug)
    assert after is not None
    assert after["low_confidence_v2"] == before["low_confidence_v2"] - 1


def test_scan_leaves_no_parsed_history_in_the_caches(tmp_reciter_dir):
    slug = "fixture_reciter"
    _install_with_sidecar(tmp_reciter_dir, slug, "112-ikhlas")  # ships an edit history
    assert review_signals.compute(slug) is not None
    assert cache.get_seg_edit_history(slug) is None
    assert cache.get_seg_history_batches(slug) is None
    assert cache.get_seg_cache(slug) is None
