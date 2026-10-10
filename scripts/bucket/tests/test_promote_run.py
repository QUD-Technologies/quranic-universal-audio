"""Tests for ``promote_run.py`` and its build half, ``_promote_artifacts.py``.

Most run the real CLI end to end against an in-memory bucket, so the refusals,
the write order and the artifact contents are asserted where they actually
happen. Only ffmpeg and the SQLite substrate are stubbed.

The fixture is a hand-built staging dir for chapter 75 whose pre-strip row 0 was
a Basmala and whose waqf-sakt merge sat at pre-strip index 5 — so the merged
row's published position is 4. That one-off is what makes the UID join testable:
the persisted row's UID, the published operation's UID and Inspector's own undo
path all have to agree on it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import _promote_artifacts as pa
import promote_run
import pytest

from qua_shared.schemas.bucket.staged_run import RunManifestDoc

SLUG = "fixture_reciter"
RUN_ID = "run-0001"
CHAPTER = 75
SOURCE_URL = "https://example.invalid/media/075.mp3"
WAQF_INDEX = 4  # pre-strip 5, minus the one stripped Basmala before it

_SEGMENTS = [
    {"time_start": 1000, "time_end": 2000, "matched_ref": "75:1:1-75:1:3", "confidence": 0.9},
    {"time_start": 2000, "time_end": 3000, "matched_ref": "75:2:1-75:2:2", "confidence": 0.9},
    {"time_start": 3000, "time_end": 4000, "matched_ref": "75:3:1-75:3:2", "confidence": 0.88},
    {
        "time_start": 4000,
        "time_end": 5000,
        "matched_ref": "75:26:1-75:26:3",
        "confidence": 0.77,
        "wrap_word_ranges": [["75:26:1", "75:26:2", "75:26:3"]],
    },
    {"time_start": 5000, "time_end": 5900, "matched_ref": "75:27:1-75:27:3", "confidence": 1.0},
    {"time_start": 5900, "time_end": 6800, "matched_ref": "75:28:1-75:28:2", "confidence": 0.8},
]

_EVENTS = [
    {
        "kind": "waqf_sakt",
        "chapter": CHAPTER,
        "targets_before": [
            {
                "index_at_save": 5,
                "time_start": 5000,
                "time_end": 5400,
                "matched_ref": "75:27:1-75:27:2",
                "confidence": 0.71,
            },
            {
                "index_at_save": 6,
                "time_start": 5400,
                "time_end": 5900,
                "matched_ref": "75:27:3-75:27:3",
                "confidence": 0.64,
            },
        ],
        "targets_after": [
            {
                "index_at_save": 5,
                "final_index": WAQF_INDEX,
                "time_start": 5000,
                "time_end": 5900,
                "matched_ref": "75:27:1-75:27:3",
                "confidence": 1.0,
            }
        ],
    },
    {
        "kind": "delete_segment",
        "chapter": CHAPTER,
        "targets_before": [
            {
                "index_at_save": 0,
                "time_start": 0,
                "time_end": 1000,
                "matched_ref": "Basmala",
                "confidence": 0.93,
            }
        ],
        "targets_after": [],
    },
]

_PROFILE = {
    "name": "extraction.v2",
    "segmenter": "segmentation.recitation_v2@v1",
    "segmentation": {
        "min_silence_ms": 300,
        "min_speech_ms": 250,
        "pad_left_ms": 40,
        "pad_right_ms": 60,
        "min_silence_floor_ms": 120,
    },
}


def _staged_files(run_riwayah: str = "hafs") -> dict[str, bytes]:
    """The staged run's payloads, keyed by run-relative path."""
    candidate = {
        "chapter": CHAPTER,
        "entries": [{"ref": str(CHAPTER), "segments": _SEGMENTS}],
        "source_url": SOURCE_URL,
        "source_offset_ms": 0,
        "trim_span_ms": None,
        "riwayah": run_riwayah,
    }
    return {
        f"candidates/{CHAPTER}.json": json.dumps(candidate).encode("utf-8"),
        "events.json": json.dumps(_EVENTS).encode("utf-8"),
        "chapter_sources.json": json.dumps(
            {str(CHAPTER): {"url": SOURCE_URL, "offset_ms": 0}}
        ).encode("utf-8"),
        "coverage_report.json": b'{"missing": []}',
        f"audio/{CHAPTER}.mp3": b"ID3-not-really-an-mp3",
        "sidecars/low_confidence_v2.json": b'{"failures": []}',
    }


def _manifest_doc(files: dict[str, bytes], run_riwayah: str = "hafs") -> dict:
    return {
        "run_id": RUN_ID,
        "slug": SLUG,
        "source_commit": "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c",
        "created_at": "2026-08-07T09:15:00Z",
        "profile": _PROFILE,
        "profile_sha256": "f" * 64,
        "model_revisions": {
            "asr": "example-org/whisper-quran-large",
            "vad": "example-org/recitation-segmenter-v2",
        },
        "inputs": {
            "slug": SLUG,
            "chapters": [CHAPTER],
            "audio_source": "https://example.invalid/playlist",
            "riwayah": run_riwayah,
        },
        "artifacts": [
            {"path": p, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
            for p, b in files.items()
        ],
        "required": sorted(files),
        "deleted_basmala_chapters": [CHAPTER],
    }


class FakeBackend:
    """In-memory bucket exposing the read/write surface promote uses."""

    def __init__(self, files: dict[str, bytes], events: list[str]) -> None:
        self.files = dict(files)
        self.events = events
        self.reads: list[str] = []
        self.deleted: list[str] = []

    def read_bytes(self, path: str) -> bytes:
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def exists(self, path: str) -> bool:
        return path in self.files

    def write_bytes_atomic(self, path: str, data: bytes) -> None:
        self.events.append(f"write:{path}")
        self.files[path] = data

    def iter_jsonl(self, path: str):
        for line in self.read_bytes(path).decode("utf-8").splitlines():
            if line.strip():
                yield json.loads(line)

    def delete(self, path: str) -> None:
        self.files.pop(path, None)
        self.deleted.append(path)


def _hd_peaks(_source: str) -> dict:
    """A synthetic ``compute_audio_peaks`` result — no ffmpeg in a unit test."""
    duration_ms = 7000
    return {
        "schema_version": 2,
        "duration_ms": duration_ms,
        "peaks": [[-0.5, 0.5]] * int(duration_ms / 1000 * 30),
    }


class Harness:
    """One promote run: the fake bucket, the recorded batch, the event order."""

    def __init__(
        self,
        staged: dict[str, bytes],
        published: dict[str, bytes] | None = None,
        run_riwayah: str = "hafs",
    ):
        self.events: list[str] = []
        prefix = f"staging/{SLUG}/{RUN_ID}"
        bucket = {f"{prefix}/{p}": b for p, b in staged.items()}
        bucket[f"{prefix}/manifest.json"] = json.dumps(
            _manifest_doc(staged, run_riwayah)
        ).encode("utf-8")
        bucket.update(published or {})
        self.backend = FakeBackend(bucket, self.events)
        self.written: dict[str, object] = {}

    def record_batch(self, _bucket_id: str, files: dict) -> None:
        self.events.append("batch_write")
        self.written = dict(files)


class _Row:
    def __init__(self, value: str) -> None:
        self.state = type("S", (), {"value": value})()


@pytest.fixture
def harness(monkeypatch):
    def build(staged=None, published=None, row=None, riwayah=None, run_riwayah="hafs"):
        import services.db as db
        from services.audio import peaks as peaks_mod
        from services.db import sync as db_sync
        from services.state import catalog as catalog_service
        from services.state import state as state_svc
        from services.storage import bucket_audit, hf_bucket

        h = Harness(
            staged if staged is not None else _staged_files(run_riwayah),
            published,
            run_riwayah,
        )
        monkeypatch.setattr(hf_bucket, "get_backend", lambda: h.backend)
        monkeypatch.setattr(peaks_mod, "compute_audio_peaks", _hd_peaks)
        monkeypatch.setattr(promote_run.bs, "batch_write", h.record_batch)
        monkeypatch.setattr(db_sync, "pull", lambda *a, **k: True)
        monkeypatch.setattr(db, "init_db", lambda: 1)
        monkeypatch.setattr(state_svc, "get_row", lambda slug: row)
        # The catalog delivery row is what promote checks the run's riwayah
        # against; ``None`` is the no-row case, which is Hafs by construction.
        delivery = None if riwayah is None else type("D", (), {"riwayah": riwayah})()
        monkeypatch.setattr(catalog_service, "find_delivery", lambda slug: delivery)
        monkeypatch.setattr(
            bucket_audit,
            "audit",
            lambda *a: type("R", (), {"files": [], "n_errors": 0, "n_missing": 0})(),
        )
        return h

    return build


def _run(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["promote_run.py", f"{SLUG}/{RUN_ID}", *argv])
    return promote_run.main()


def _published(h: Harness, name: str) -> dict:
    return json.loads(h.written[f"reciters/{SLUG}/{name}"])


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_promote_publishes_every_artifact(monkeypatch, harness):
    h = harness()
    assert _run(monkeypatch) == 0

    base = f"reciters/{SLUG}"
    assert set(h.written) >= {
        f"{base}/detailed.json",
        f"{base}/segments.json",
        f"{base}/pipeline_meta.json",
        f"{base}/chapter_sources.json",
        f"{base}/coverage_report.json",
        f"{base}/low_confidence_v2.json",
        f"{base}/edit_history.jsonl",
        f"{base}/edit_history_peaks.jsonl",
        f"{base}/audio/{CHAPTER}.mp3",
        f"{base}/peaks/{CHAPTER}.json.gz",
    }
    assert _published(h, "pipeline_meta.json")["deleted_basmala_chapters"] == [CHAPTER]


def test_stamping_is_unconditional(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    segs = _published(h, "detailed.json")["entries"][0]["segments"]
    assert len(segs) == len(_SEGMENTS)
    # 75:27:3 ends on qaf; the stamp is the only writer of ``qalqala_letter``.
    assert segs[4]["qalqala_letter"] == "ق"


def test_done_sentinel_is_the_last_write(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    assert h.events == ["batch_write", f"write:reciters/{SLUG}/audio/_done.json"]
    sentinel = json.loads(h.backend.files[f"reciters/{SLUG}/audio/_done.json"])
    assert sentinel["total_chapters"] == 1


def test_chapter_audio_is_published_by_path(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    for name in (f"audio/{CHAPTER}.mp3", f"peaks/{CHAPTER}.json.gz"):
        assert isinstance(h.written[f"reciters/{SLUG}/{name}"], Path)
    assert not any(isinstance(v, bytes) and len(v) > 1 << 20 for v in h.written.values())


def test_staging_is_cleared_after_a_verified_promote(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    assert f"staging/{SLUG}/{RUN_ID}/manifest.json" in h.backend.deleted
    assert f"staging/{SLUG}/{RUN_ID}/events.json" in h.backend.deleted


# ---------------------------------------------------------------------------
# Refusals — nothing is written
# ---------------------------------------------------------------------------


def test_sha256_mismatch_aborts_before_any_write(monkeypatch, harness):
    h = harness()
    path = f"staging/{SLUG}/{RUN_ID}/events.json"
    h.backend.files[path] = h.backend.files[path].replace(b'"kind"', b'"KIND"')
    with pytest.raises(SystemExit):
        _run(monkeypatch)
    assert h.events == []


def test_missing_required_path_aborts(monkeypatch, harness):
    staged = _staged_files()
    manifest = _manifest_doc(staged)
    manifest["required"].append("sidecars/auto_split_v1.json")
    h = harness()
    prefix = f"staging/{SLUG}/{RUN_ID}"
    h.backend.files[f"{prefix}/manifest.json"] = json.dumps(manifest).encode("utf-8")
    with pytest.raises(SystemExit):
        _run(monkeypatch)
    assert h.events == []


def test_reviewed_slug_is_refused_without_force(monkeypatch, harness):
    h = harness(row=_Row("under_review"))
    with pytest.raises(SystemExit):
        _run(monkeypatch)
    assert h.events == []


def test_force_is_refused_when_a_human_edited_the_reciter(monkeypatch, harness):
    human = json.dumps({"batch_id": "b1", "actor": {"hf_user_id": "u42"}, "operations": []}).encode(
        "utf-8"
    )
    h = harness(
        published={f"reciters/{SLUG}/edit_history.jsonl": human},
        row=_Row("under_review"),
    )
    with pytest.raises(SystemExit):
        _run(monkeypatch, "--force")
    assert h.events == []


# ---------------------------------------------------------------------------
# The waqf UID — one derivation, two consumers, checked through undo
# ---------------------------------------------------------------------------


def test_waqf_uid_joins_the_row_to_the_operation_and_undo_reverses_it(monkeypatch, harness):
    from domain.identity import derive_uid
    from services.segments import undo

    h = harness()
    _run(monkeypatch)

    entries = _published(h, "detailed.json")["entries"]
    row = entries[0]["segments"][WAQF_INDEX]
    expected = derive_uid(CHAPTER, WAQF_INDEX, row["time_start"])
    assert row["segment_uid"] == expected

    batches = [json.loads(x) for x in h.written[f"reciters/{SLUG}/edit_history.jsonl"].splitlines()]
    waqf = next(op for b in batches for op in b["operations"] if op["op_type"] == "waqf_sakt")
    assert waqf["targets_after"][0]["segment_uid"] == expected
    assert "final_index" not in waqf["targets_after"][0]
    assert [s.get("segment_uid") for s in waqf["targets_before"]] == [None, None]

    undo._reverse_merge(entries, waqf, {CHAPTER})
    assert len(entries[0]["segments"]) == len(_SEGMENTS) + 1


def test_a_wrong_final_index_stops_the_promote(monkeypatch, harness):
    events = json.loads(json.dumps(_EVENTS))
    events[0]["targets_after"][0]["final_index"] = WAQF_INDEX + 1
    h = harness(staged={**_staged_files(), "events.json": json.dumps(events).encode("utf-8")})
    with pytest.raises(SystemExit):
        _run(monkeypatch)
    assert h.events == []


# ---------------------------------------------------------------------------
# The published operations and their peaks
# ---------------------------------------------------------------------------


def test_snapshots_carry_chapter_and_audio_url(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    batches = [json.loads(x) for x in h.written[f"reciters/{SLUG}/edit_history.jsonl"].splitlines()]
    strip = next(b for b in batches if b.get("batch_type") == "strip_specials")
    assert strip["chapters"] == [CHAPTER]
    assert "chapter" not in strip
    for batch in batches:
        for op in batch["operations"]:
            for snap in op["targets_before"] + op["targets_after"]:
                assert snap["chapter"] == CHAPTER
                assert snap["audio_url"] == SOURCE_URL


def test_peaks_records_come_from_the_in_memory_envelopes(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    records = [
        json.loads(x) for x in h.written[f"reciters/{SLUG}/edit_history_peaks.jsonl"].splitlines()
    ]
    assert len(records) == len(_EVENTS)
    assert {r["url"] for r in records} == {SOURCE_URL}
    assert all(r["peaks_b64"] for r in records)
    # Everything read came from staging — the baked peaks the default provider
    # would have gone looking for do not exist yet.
    assert all(p.startswith(f"staging/{SLUG}/") for p in h.backend.reads)


def test_segments_json_keeps_the_repeated_fifth_element(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    doc = _published(h, "segments.json")
    wrapped = doc["75:26"][0]
    assert len(wrapped) == 5
    assert wrapped[4] == {"repeated": [[1, 2], [1, 3]]}
    assert len(doc["75:1"][0]) == 4


# ---------------------------------------------------------------------------
# --dry-run --compare, the parity-replay surface
# ---------------------------------------------------------------------------


def _publish_once(monkeypatch, harness) -> dict[str, bytes]:
    """Promote once and return the JSON artifacts as a published reciter."""
    h = harness()
    _run(monkeypatch)
    return {k: v for k, v in h.written.items() if isinstance(v, bytes)}


def test_compare_against_an_identical_reciter_writes_nothing_and_passes(monkeypatch, harness):
    published = _publish_once(monkeypatch, harness)
    h = harness(published=published)
    assert _run(monkeypatch, "--dry-run", "--compare", SLUG) == 0
    assert h.events == []


def test_compare_reports_a_differing_published_reciter(monkeypatch, harness, capsys):
    published = _publish_once(monkeypatch, harness)
    key = f"reciters/{SLUG}/detailed.json"
    doc = json.loads(published[key])
    doc["entries"][0]["segments"][0]["time_end"] += 7
    published[key] = json.dumps(doc).encode("utf-8")

    harness(published=published)
    assert _run(monkeypatch, "--dry-run", "--compare", SLUG) == 1
    assert "time_end" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# build_meta
# ---------------------------------------------------------------------------


def _manifest_model(profile=_PROFILE, run_riwayah="hafs") -> RunManifestDoc:
    doc = _manifest_doc(_staged_files(run_riwayah), run_riwayah)
    doc["profile"] = profile
    return RunManifestDoc.model_validate(doc)


def test_build_meta_maps_every_declared_field():
    meta = pa.build_meta(_manifest_model())
    assert meta == {
        "created_at": "2026-08-07T09:15:00Z",
        "asr_model": "example-org/whisper-quran-large",
        "vad_model": "example-org/recitation-segmenter-v2",
        "min_silence_ms": 300,
        "min_speech_ms": 250,
        "pad_left_ms": 40,
        "pad_right_ms": 60,
        "min_silence_floor_ms": 120,
        "audio_source": "https://example.invalid/playlist",
    }


def test_build_meta_omits_a_knob_the_boundary_arm_has_no_field_for():
    bnd = {**_PROFILE, "segmentation": {"threshold": 0.5, "pad_left_ms": 40, "pad_right_ms": 60}}
    meta = pa.build_meta(_manifest_model(bnd))
    assert "min_silence_ms" not in meta
    assert meta["pad_left_ms"] == 40


def test_both_artifacts_carry_the_same_meta_value(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    assert _published(h, "detailed.json")["_meta"] == _published(h, "segments.json")["_meta"]


# ---------------------------------------------------------------------------
# Which edition the run was made against
# ---------------------------------------------------------------------------


def test_a_hafs_run_publishes_no_riwayah_key_at_all():
    """A key that says nothing is a key every pre-multi-riwayah reciter lacks."""
    assert "riwayah" not in pa.build_meta(_manifest_model())


def test_a_projected_run_publishes_the_inspector_vocabulary_slug():
    """The producer speaks ``warsh``; the catalog and every reader speak
    ``warsh_an_nafi``, so the boundary is crossed once, here."""
    assert pa.build_meta(_manifest_model(run_riwayah="warsh"))["riwayah"] == "warsh_an_nafi"


def test_a_projected_run_stamps_its_edition_on_both_artifacts_and_the_sidecar(
    monkeypatch, harness
):
    h = harness(riwayah="warsh_an_nafi", run_riwayah="warsh")
    assert _run(monkeypatch) == 0

    assert _published(h, "detailed.json")["_meta"]["riwayah"] == "warsh_an_nafi"
    assert _published(h, "segments.json")["_meta"]["riwayah"] == "warsh_an_nafi"
    assert _published(h, "pipeline_meta.json")["riwayah"] == "warsh_an_nafi"


def test_a_hafs_run_leaves_the_pipeline_sidecar_bytes_alone(monkeypatch, harness):
    h = harness()
    _run(monkeypatch)
    assert "riwayah" not in _published(h, "pipeline_meta.json")


def test_a_run_whose_edition_is_not_the_catalogs_is_refused(monkeypatch, harness, capsys):
    """The alignment and the catalog row disagree — no --force covers that."""
    harness(riwayah=None, run_riwayah="warsh")
    with pytest.raises(SystemExit):
        _run(monkeypatch, "--force")
    assert "warsh" in capsys.readouterr().err


def test_a_candidate_from_another_edition_stops_the_build(monkeypatch, harness):
    """One candidate file copied in from a different run is otherwise invisible."""
    staged = _staged_files("warsh")
    doc = json.loads(staged[f"candidates/{CHAPTER}.json"])
    doc["riwayah"] = "qalun"
    staged[f"candidates/{CHAPTER}.json"] = json.dumps(doc).encode("utf-8")
    harness(staged=staged, riwayah="warsh_an_nafi", run_riwayah="warsh")

    with pytest.raises(SystemExit):
        _run(monkeypatch)
