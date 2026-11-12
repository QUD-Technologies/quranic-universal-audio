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


def _flags(slug: str) -> int:
    from services.segments.flags import count_flagged
    from services.storage.data_loader import load_detailed

    return count_flagged(load_detailed(slug))


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
    assert scanned == review_signals.counts_from_validate(result, THRESHOLD, _flags(slug))


def test_cached_validate_payload_is_used(tmp_reciter_dir):
    slug = "fixture_reciter"
    _install_with_sidecar(tmp_reciter_dir, slug, "synthetic-classifier")
    cache.set_seg_validate_cache(
        slug,
        {"low_confidence": [{"confidence": 0.5}, {"confidence": 0.9}], "low_confidence_v2": [{}]},
    )
    assert review_signals.compute(slug) == {
        "low_confidence": 1,
        "low_confidence_v2": 1,
        "flagged": 0,
    }


def test_ignored_segments_do_not_count():
    seg = {"matched_ref": "1:1:1-1:1:2", "confidence": 0.5, "segment_uid": "u1"}
    ignored = {
        **seg,
        "segment_uid": "u2",
        "ignored_categories": ["low_confidence", "low_confidence_v2"],
    }
    flagged = {"matched_ref": "", "confidence": 0.1, "flag": {"comment": "cut short"}}
    entries = [{"segments": [seg, ignored, flagged]}]
    assert review_signals.scan_counts(entries, THRESHOLD, {"u1", "u2"}) == {
        "low_confidence": 1,
        "low_confidence_v2": 1,
        "flagged": 1,
    }


def _signals_file() -> dict | None:
    return data_dir.read_review_signals_doc()


def test_record_writes_only_when_counts_change(tmp_reciter_dir, monkeypatch):
    tmp_reciter_dir.install("fixture_reciter", "synthetic-classifier")
    writes: list[dict] = []
    real_write = data_dir.write_review_signals_doc

    def spy(doc: dict) -> None:
        writes.append(doc)
        real_write(doc)

    monkeypatch.setattr(data_dir, "write_review_signals_doc", spy)
    payload = {"low_confidence": [{"confidence": 0.5}], "low_confidence_v2": [{}, {}]}

    review_signals.record("fixture_reciter", payload)
    review_signals.record("fixture_reciter", payload)  # unchanged: no second write

    assert len(writes) == 1
    doc = _signals_file()
    assert doc is not None
    assert doc["signals"]["fixture_reciter"] == {
        "low_confidence": 1,
        "low_confidence_v2": 2,
        "flagged": 0,
    }


def test_record_drops_a_reciter_with_nothing_open(tmp_reciter_dir):
    tmp_reciter_dir.install("fixture_reciter", "synthetic-classifier")
    review_signals.record("fixture_reciter", {"low_confidence": [{"confidence": 0.5}]})
    review_signals.record("fixture_reciter", {"low_confidence": [{"confidence": 0.95}]})
    doc = _signals_file()
    assert doc is not None
    assert "fixture_reciter" not in doc["signals"]


def test_write_keeps_entries_written_offline_since_the_last_read(tmp_reciter_dir):
    tmp_reciter_dir.install("fixture_reciter", "synthetic-classifier")
    assert review_signals.current() == {}  # loaded: empty, fresh for REFRESH_S
    # An offline backfill writes another reciter behind the Space's back.
    data_dir.write_review_signals_doc(
        {"signals": {"other": {"low_confidence": 4, "low_confidence_v2": 0, "flagged": 0}}}
    )
    review_signals.record("fixture_reciter", {"low_confidence_v2": [{}]})
    doc = _signals_file()
    assert doc is not None
    assert doc["signals"] == {
        "other": {"low_confidence": 4, "low_confidence_v2": 0, "flagged": 0},
        "fixture_reciter": {"low_confidence": 0, "low_confidence_v2": 1, "flagged": 0},
    }


def test_current_rereads_the_file_after_the_refresh_window(tmp_reciter_dir, monkeypatch):
    tmp_reciter_dir.install("fixture_reciter", "synthetic-classifier")
    assert review_signals.current() == {}
    data_dir.write_review_signals_doc(
        {"signals": {"other": {"low_confidence": 2, "low_confidence_v2": 0, "flagged": 0}}}
    )
    assert review_signals.current() == {}  # still inside the window
    monkeypatch.setattr(review_signals, "REFRESH_S", 0.0)
    assert review_signals.current() == {
        "other": {"low_confidence": 2, "low_confidence_v2": 0, "flagged": 0}
    }


def test_validate_route_records_the_counts(flask_client, tmp_reciter_dir):
    slug = "fixture_reciter"
    _install_with_sidecar(tmp_reciter_dir, slug, "synthetic-classifier")
    res = flask_client.get(f"/api/seg/validate/{slug}")
    assert res.status_code == 200
    expected = review_signals.counts_from_validate(res.get_json(), THRESHOLD, _flags(slug))
    assert any(expected.values()), "fixture should have something open"
    doc = _signals_file()
    assert doc is not None
    assert doc["signals"][slug] == expected


def test_route_serves_the_file_without_scanning(flask_client, tmp_reciter_dir, monkeypatch):
    slug = "fixture_reciter"
    tmp_reciter_dir.install(slug, "synthetic-classifier")
    data_dir.write_review_signals_doc(
        {
            "signals": {
                slug: {"low_confidence": 3, "low_confidence_v2": 1, "flagged": 0},
                "not_listed": {"low_confidence": 9},
            }
        }
    )
    monkeypatch.setattr(
        review_signals, "compute", lambda *a, **k: pytest.fail("route must not scan")
    )
    body = flask_client.get("/api/seg/review-signals").get_json()
    assert body == {"signals": {slug: {"low_confidence": 3, "low_confidence_v2": 1, "flagged": 0}}}


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


@pytest.mark.parametrize("fixture", FIXTURES)
def test_offline_compute_matches_the_validator(tmp_reciter_dir, fixture):
    slug = "fixture_reciter"
    _install_with_sidecar(tmp_reciter_dir, slug, fixture)
    offline = review_signals.compute(slug, offline=True)
    result = validate_reciter_segments(slug)
    assert result is not None
    assert offline == review_signals.counts_from_validate(result, THRESHOLD, _flags(slug))
