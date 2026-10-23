"""Unit tests for the whole-bucket drift validator CLI.

Exercises ``scripts.diagnostics.validate_bucket.validate_bucket`` against a
bucket-shaped layout built under ``tmp_path`` and read through a real
``FilesystemBackend`` (installed as the process singleton so
``data_dir.list_slugs`` and the per-reciter audit both read it). No network —
the committed segments fixtures supply the artefact bodies and the sidecars are
synthesised inline.

The DB-catalog pass runs against the empty migrated DB from the autouse
``_substrate_db`` fixture (a valid zero-reciter catalog), so ``check_catalog``
stays exercised without seeding rows.

Scenarios:
  - a clean bucket (two reciters + one valid sidecar) -> exit 0;
  - a seeded bad sidecar (missing required ``_meta``) -> exit non-zero;
  - a seeded bad reciter artefact (corrupt detailed.json) -> exit non-zero;
  - an unknown field in a reciter artefact -> hard error under pure
    ``extra="forbid"`` (fails both strict and non-strict).
"""

from __future__ import annotations

import base64
import gzip
import json

import pytest

# The script lives under scripts/diagnostics; the inspector test bootstrap puts
# the repo root on sys.path, so this imports cleanly without a path shim.
from scripts.diagnostics.validate_bucket import validate_bucket, validate_catalog
from services.storage import hf_bucket as _hf_bucket
from services.storage.hf_bucket import FilesystemBackend

SLUG_A = "fixture_reciter_a"
SLUG_B = "fixture_reciter_b"

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
    """Strip the fixture-only + legacy keys so the doc parses cleanly."""
    doc = json.loads(json.dumps(fixture))  # deep copy
    doc.pop("_fixture_meta", None)
    for entry in doc.get("entries", []):
        entry.pop("audio", None)
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


def _build_reciter_dir(backend: FilesystemBackend, slug: str, detailed: dict) -> None:
    """Populate ``reciters/<slug>/`` with a full, parity-balanced artefact set."""
    base = f"reciters/{slug}"
    backend.write_bytes_atomic(f"{base}/detailed.json", json.dumps(detailed).encode("utf-8"))

    segments = {
        "_meta": {"audio_source": "by_surah/fixture"},
        "112:1": [[0, 3, 0, 1500], [3, 6, 1500, 3000]],
    }
    backend.write_bytes_atomic(f"{base}/segments.json", json.dumps(segments).encode("utf-8"))
    backend.write_bytes_atomic(f"{base}/edit_history.jsonl", b"")

    pipeline_meta = {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00Z",
    }
    backend.write_bytes_atomic(
        f"{base}/pipeline_meta.json", json.dumps(pipeline_meta).encode("utf-8")
    )

    backend.write_bytes_atomic(f"{base}/audio/112.mp3", b"\xff\xfb" + b"\x00" * 4096)
    backend.write_bytes_atomic(f"{base}/peaks/112.json.gz", _v3_peaks_gz())


def _valid_sidecar(slug: str) -> dict:
    return {
        "schema_version": 1,
        "slug": slug,
        "_meta": {
            "checksum": "deadbeef",
            "chapter_count": 1,
            "category": "by_surah",
        },
        "chapters": {
            "112": {"url": "https://cdn.example/112.mp3", "size_bytes": 4098},
        },
    }


def _write_sidecar(backend: FilesystemBackend, slug: str, doc: dict) -> None:
    backend.write_bytes_atomic(
        f"catalog/audio_manifest/{slug}.json", json.dumps(doc).encode("utf-8")
    )


@pytest.fixture
def bucket_backend(tmp_path):
    """Install a FilesystemBackend as the process singleton (bucket-shaped).

    Both ``data_dir.list_slugs`` and the per-reciter audit resolve through the
    singleton, so this is what the validator under test reads. Restores the
    singleton on teardown.
    """
    backend = FilesystemBackend(tmp_path)
    _hf_bucket.set_backend(backend)
    yield backend
    _hf_bucket.reset_backend()


def _seed_clean_bucket(backend: FilesystemBackend, load_fixture) -> None:
    clean = _clean_detailed(load_fixture("112-ikhlas"))
    _build_reciter_dir(backend, SLUG_A, clean)
    _build_reciter_dir(backend, SLUG_B, clean)
    _write_sidecar(backend, SLUG_A, _valid_sidecar(SLUG_A))


def test_clean_bucket_validates_and_exits_zero(bucket_backend, load_fixture):
    _seed_clean_bucket(bucket_backend, load_fixture)

    report = validate_bucket(bucket_backend, "test-bucket", check_catalog=True)

    assert report.n_hard_errors == 0, [
        (r.slug, r.error_details) for r in report.reciters if r.n_errors
    ]
    assert report.n_reciter_errors == 0
    assert report.n_sidecar_errors == 0
    assert report.catalog_ok is True
    assert report.catalog_checked is True
    assert report.exit_code(strict=False) == 0
    assert report.exit_code(strict=True) == 0

    # Both reciters were discovered and audited.
    assert {r.slug for r in report.reciters} == {SLUG_A, SLUG_B}
    assert all(r.found for r in report.reciters)
    # The one sidecar present parsed ok.
    assert [s.slug for s in report.sidecars] == [SLUG_A]
    assert report.sidecars[0].ok is True


def test_bad_sidecar_fails_with_nonzero_exit(bucket_backend, load_fixture):
    _seed_clean_bucket(bucket_backend, load_fixture)
    # Seed a structurally-invalid sidecar: drop the required ``_meta`` block.
    bad = _valid_sidecar(SLUG_B)
    del bad["_meta"]
    _write_sidecar(bucket_backend, SLUG_B, bad)

    report = validate_bucket(bucket_backend, "test-bucket", check_catalog=True)

    assert report.n_sidecar_errors == 1
    assert report.n_hard_errors == 1
    assert report.exit_code(strict=False) == 1

    failed = [s for s in report.sidecars if not s.ok]
    assert [s.slug for s in failed] == [SLUG_B]
    assert "schema fail" in failed[0].detail


def test_corrupt_reciter_artefact_fails_with_nonzero_exit(bucket_backend, load_fixture):
    _seed_clean_bucket(bucket_backend, load_fixture)
    # Corrupt one reciter's detailed.json so its per-file auditor hard-errors.
    bucket_backend.write_bytes_atomic(f"reciters/{SLUG_B}/detailed.json", b"{ not valid json")

    report = validate_bucket(bucket_backend, "test-bucket", check_catalog=True)

    assert report.n_reciter_errors >= 1
    assert report.n_hard_errors >= 1
    assert report.exit_code(strict=False) == 1

    bad = next(r for r in report.reciters if r.slug == SLUG_B)
    assert bad.n_errors >= 1
    assert bad.error_details


def test_unknown_field_is_a_hard_error(bucket_backend, load_fixture):
    # Inject an unrecognised top-level key into a reciter's detailed.json. Under
    # pure extra="forbid" the schema raises ValidationError, so the auditor marks
    # the file as an error — a hard error that fails both strict and non-strict.
    clean = _clean_detailed(load_fixture("112-ikhlas"))
    clean["totally_unknown_top_level_key"] = {"x": 1}
    _build_reciter_dir(bucket_backend, SLUG_A, clean)
    _write_sidecar(bucket_backend, SLUG_A, _valid_sidecar(SLUG_A))

    report = validate_bucket(bucket_backend, "test-bucket", check_catalog=True)

    assert report.n_hard_errors >= 1
    assert report.exit_code(strict=False) == 1
    assert report.exit_code(strict=True) == 1


def test_one_failing_reciter_does_not_abort_the_sweep(bucket_backend, load_fixture):
    """A reciter whose ``audit()`` raises (e.g. a non-UTF8 ``edit_history.jsonl``
    whose unguarded ``raw.decode('utf-8')`` throws) is recorded as a hard error
    for that slug only — the whole-bucket sweep keeps going and still reports
    every other reciter instead of aborting mid-loop."""
    _seed_clean_bucket(bucket_backend, load_fixture)
    bucket_backend.write_bytes_atomic(
        f"reciters/{SLUG_B}/edit_history.jsonl", b"\xff\xfe not valid utf-8 \xff"
    )

    report = validate_bucket(bucket_backend, "test-bucket", check_catalog=False)

    # Both reciters still reported — the bad folder did not kill the sweep.
    assert {r.slug for r in report.reciters} == {SLUG_A, SLUG_B}
    bad = next(r for r in report.reciters if r.slug == SLUG_B)
    assert bad.n_errors == 1
    assert any("UnicodeDecodeError" in d for d in bad.error_details)
    good = next(r for r in report.reciters if r.slug == SLUG_A)
    assert good.n_errors == 0
    assert report.exit_code(strict=False) == 1


def test_catalog_pull_missing_db_is_hard_error(monkeypatch):
    """With ``pull=True`` (the nightly CLI path) a bucket that has no
    ``db/inspector.db`` is a hard catalog error, not a silently-empty pass."""
    from services.db import sync as db_sync

    monkeypatch.setattr(db_sync, "pull", lambda *a, **k: False)
    ok, detail = validate_catalog(pull=True)
    assert ok is False
    assert "no db/inspector.db" in detail


def test_catalog_pull_present_db_snapshots(monkeypatch):
    """With ``pull=True`` and a DB present, the catalog pass migrates +
    snapshots the local DB. The autouse migrated substrate DB yields a valid
    zero-reciter catalog (``ok=True``)."""
    import services.db as db_pkg
    from services.db import sync as db_sync

    monkeypatch.setattr(db_sync, "pull", lambda *a, **k: True)
    monkeypatch.setattr(db_pkg, "init_db", lambda *a, **k: 0)  # DB already migrated by fixture
    ok, detail = validate_catalog(pull=True)
    assert ok is True
    assert "reciters" in detail
