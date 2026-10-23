"""Unit tests for the bucket-artefact audit engine.

Exercises ``services.storage.bucket_audit.audit`` against a bucket-shaped
``reciters/<slug>/`` directory built under ``tmp_path`` and read through a
real ``FilesystemBackend``. No network — the committed segments fixtures
(``inspector/tests/fixtures/segments/``) supply the artefact bodies.

Two scenarios:
  - a *clean* reciter folder parses with zero errors;
  - a *legacy* reciter folder (the fixture ``edit_history.jsonl`` carries
    pre-migration ``EditHistoryBatch`` / ``EditOperation`` keys) now fails the
    schema under pure ``extra="forbid"`` — the auditor catches the
    ``ValidationError`` and marks the file ``status="error"`` (no silent strip).
"""

from __future__ import annotations

import base64
import gzip
import json

import pytest

from services.storage.bucket_audit import (
    _sample_slugs,
    audit,
    sample_validation,
)
from services.storage.hf_bucket import FilesystemBackend
from tests.conftest import FIXTURES_DIR

SLUG = "fixture_reciter"

# Seg-level keys the DetailedSegment schema no longer accepts (retired in
# Migration #5 or never modelled). The fixture carries some; the clean variant
# drops them so the doc validates under pure extra="forbid".
_SEG_DEAD = {
    "matched_text",
    "phonemes_asr",
    "has_repeated_words",
    "audio_url",
    "chapter",
    "entry_ref",
    "index_at_save",
    "display_text",
}


def _clean_detailed(fixture: dict) -> dict:
    """Strip the fixture-only + retired keys so the doc validates cleanly."""
    doc = json.loads(json.dumps(fixture))  # deep copy
    doc.pop("_fixture_meta", None)
    for entry in doc.get("entries", []):
        entry.pop("audio", None)  # retired per-chapter URL
        for seg in entry.get("segments", []):
            for k in _SEG_DEAD:
                seg.pop(k, None)
    return doc


def _v3_peaks_gz() -> bytes:
    """A minimal valid v3 slim peaks blob (gzip(JSON))."""
    doc = {
        "schema_version": 3,
        "duration_ms": 5000,
        "q": "int8",
        "bps": 8,
        "n": 2,
        "peaks_b64": base64.b64encode(bytes([0, 10, 251, 7])).decode("ascii"),
    }
    return gzip.compress(json.dumps(doc).encode("utf-8"))


def _legacy_edit_history_bytes() -> bytes:
    """Read the committed fixture edit_history.jsonl as raw bytes.

    The fixture carries pre-migration ``EditHistoryBatch`` / ``EditOperation``
    keys that pure ``extra="forbid"`` now rejects — a natural legacy artefact
    the auditor must surface as an error.
    """
    return (FIXTURES_DIR / "112-ikhlas.edit_history.jsonl").read_bytes()


def _build_reciter_dir(backend: FilesystemBackend, detailed: dict, *, edit_history: bytes) -> None:
    """Populate ``reciters/<SLUG>/`` with a full, parity-balanced artefact set."""
    base = f"reciters/{SLUG}"
    backend.write_bytes_atomic(f"{base}/detailed.json", json.dumps(detailed).encode("utf-8"))

    # segments.json — structural shape: verse-keyed lists of int rows.
    segments = {
        "_meta": {"audio_source": "by_surah/fixture"},
        "112:1": [[0, 3, 0, 1500], [3, 6, 1500, 3000]],
    }
    backend.write_bytes_atomic(f"{base}/segments.json", json.dumps(segments).encode("utf-8"))

    backend.write_bytes_atomic(f"{base}/edit_history.jsonl", edit_history)

    pipeline_meta = {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00Z",
    }
    backend.write_bytes_atomic(
        f"{base}/pipeline_meta.json", json.dumps(pipeline_meta).encode("utf-8")
    )

    # One chapter of audio + peaks, kept in parity (1 mp3, 1 json.gz).
    backend.write_bytes_atomic(f"{base}/audio/112.mp3", b"\xff\xfb" + b"\x00" * 4096)
    backend.write_bytes_atomic(f"{base}/peaks/112.json.gz", _v3_peaks_gz())


@pytest.fixture
def backend(tmp_path):
    """A FilesystemBackend rooted at a fresh tmp dir (bucket-shaped layout)."""
    return FilesystemBackend(tmp_path)


def test_missing_reciter_reports_not_found(backend):
    result = audit(backend, "test-bucket", "does_not_exist")

    assert result.found is False
    assert result.n_errors == 0
    assert result.files[0].status == "missing"


def test_clean_reciter_audits_ok_with_no_strips(backend, load_fixture):
    clean = _clean_detailed(load_fixture("112-ikhlas"))
    # A clean edit_history.jsonl: an empty file is valid (0/0 batches, ok).
    _build_reciter_dir(backend, clean, edit_history=b"")

    result = audit(backend, "test-bucket", SLUG)

    assert result.found is True
    assert result.n_errors == 0, [
        f"{f.path}: {f.detail}" for f in result.files if f.status == "error"
    ]
    assert result.n_legacy == 0
    assert result.n_warnings == 0

    by_path = {f.path: f for f in result.files}
    detailed = by_path[f"reciters/{SLUG}/detailed.json"]
    assert detailed.status == "ok"
    assert detailed.n_info == 0
    assert detailed.n_warn == 0
    assert detailed.items_total == 4  # the fixture has 4 segments

    # audio/peaks parity row present and ok.
    parity = by_path[f"reciters/{SLUG}/(audio|peaks)/"]
    assert parity.status == "ok"
    assert parity.items_ok == 1


def test_missing_pipeline_meta_is_optional(backend, load_fixture):
    """Nothing in Inspector reads ``pipeline_meta.json`` any more, so a reciter
    without one is reported as an optional absence, not an error."""
    clean = _clean_detailed(load_fixture("112-ikhlas"))
    _build_reciter_dir(backend, clean, edit_history=b"")
    backend.delete(f"reciters/{SLUG}/pipeline_meta.json")

    result = audit(backend, "test-bucket", SLUG)

    meta = {f.path: f for f in result.files}[f"reciters/{SLUG}/pipeline_meta.json"]
    assert meta.status == "missing"
    assert meta.detail.startswith("(optional")
    assert result.n_errors == 0


def test_legacy_edit_history_surfaces_as_error(backend, load_fixture):
    clean = _clean_detailed(load_fixture("112-ikhlas"))
    # The fixture edit_history carries pre-migration EditHistoryBatch /
    # EditOperation keys (file_hash_after, save_mode, type, started_at_utc, ...)
    # that pure extra="forbid" rejects. The auditor catches the ValidationError
    # and marks the file as an error — no silent strip.
    _build_reciter_dir(backend, clean, edit_history=_legacy_edit_history_bytes())

    result = audit(backend, "test-bucket", SLUG)

    assert result.found is True

    by_path = {f.path: f for f in result.files}
    eh = by_path[f"reciters/{SLUG}/edit_history.jsonl"]
    assert eh.status == "error"
    assert eh.items_ok == 0  # the single legacy batch failed to validate
    assert "first error" in eh.detail
    # The error propagates to the audit-level error count.
    assert result.n_errors >= 1


# ---------------------------------------------------------------------------
# sample_validation — the cheap /healthz?deep=1 drift probe
# ---------------------------------------------------------------------------


def test_sample_slugs_spreads_across_sorted_list():
    # 3-of-10 must hit first + last, not just the first three alphabetically.
    slugs = [f"r{i:02d}" for i in range(10)]
    assert _sample_slugs(slugs, 3) == ["r00", "r04", "r09"]
    # Fewer slugs than the cap returns them all (sorted).
    assert _sample_slugs(["b", "a"], 3) == ["a", "b"]
    # A zero cap walks nothing.
    assert _sample_slugs(slugs, 0) == []


def test_sample_validation_clean_reciter_is_ok(backend, load_fixture):
    clean = _clean_detailed(load_fixture("112-ikhlas"))
    _build_reciter_dir(backend, clean, edit_history=b"")

    out = sample_validation(backend=backend, bucket_id="test-bucket", slugs=[SLUG])

    assert out["ok"] is True
    assert out["catalog_ok"] is True  # autouse DB has a clean reciter-less catalog
    assert out["sampled"] == [SLUG]
    assert out["n_reciters"] == 1
    assert out["errors"] == []


def test_sample_validation_surfaces_drifted_reciter(backend, load_fixture):
    clean = _clean_detailed(load_fixture("112-ikhlas"))
    _build_reciter_dir(backend, clean, edit_history=b"")
    # Corrupt detailed.json so the schema audit fails → the probe must report it.
    backend.write_bytes_atomic(f"reciters/{SLUG}/detailed.json", b"{not json")

    out = sample_validation(backend=backend, bucket_id="test-bucket", slugs=[SLUG])

    assert out["ok"] is False
    assert out["sampled"] == [SLUG]
    assert any("detailed.json" in e for e in out["errors"])


def test_sample_validation_caps_the_walk(backend, load_fixture):
    clean = _clean_detailed(load_fixture("112-ikhlas"))
    # Build five reciter folders; the default cap walks only three of them.
    slugs = [f"r{i}" for i in range(5)]
    for slug in slugs:
        base = f"reciters/{slug}"
        backend.write_bytes_atomic(f"{base}/detailed.json", json.dumps(clean).encode("utf-8"))

    out = sample_validation(backend=backend, bucket_id="test-bucket", slugs=slugs, limit=3)

    assert out["n_reciters"] == 5
    assert len(out["sampled"]) == 3
    assert out["sampled"] == ["r0", "r2", "r4"]
