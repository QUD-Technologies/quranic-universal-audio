"""Native align pipeline — adaptation, run lifecycle, assemble, auto_detect gate.

The aligner Space and the HF Job are never touched: ``adapt`` is pure, the
run lifecycle is exercised with the worker thread stubbed out, and assemble is
driven from hand-staged aligner results against an in-memory bucket.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

import pytest

from qua_shared.schemas import (
    Actor,
    AudioCategory,
    Channel,
    Delivery,
    ReciterEntry,
    ReciterState,
    Riwayah,
    Role,
    Source,
    Style,
    Vocab,
)
from qua_shared.schemas.bucket.staged_run import ChapterCandidateDoc, PipelineAuditEvent
from qua_shared.schemas.config.pending_requests import ProposedEdits
from services.state import state as state_service

SLUG = "rec_align"
OWNER = Actor(hf_user_id="u-owner", login_at_time="owner", role=Role.OWNER)


def _state(slug: str) -> ReciterState:
    row = state_service.get_row(slug)
    assert row is not None
    return row.state


def _row(seg, t0, t1, ref_from, ref_to, conf=0.9, **extra):
    return {
        "segment": seg,
        "time_from": t0,
        "time_to": t1,
        "ref_from": ref_from,
        "ref_to": ref_to,
        "matched_text": "",
        "confidence": conf,
        "has_missing_words": False,
        "has_repeated_words": False,
        "kind": "quran",
        "special_type": None,
        "error": None,
        **extra,
    }


def _special(seg, t0, t1, name="Basmala"):
    return _row(seg, t0, t1, "", "", 1.0, kind="special", special_type=name)


#: Chapter 112 as the aligner returns it: a Basmala, then a waqf-merged row at
#: pre-strip index 2 (post-strip 1), then two plain rows.
CH112 = {
    "audio_id": "x",
    "device": "GPU",
    "_meta": {"schema_version": 2},
    "segments": [
        _special(1, 0.82, 6.68),
        _row(2, 7.38, 10.94, "112:1:1", "112:1:4", 1.0),
        _row(
            3,
            11.06,
            13.94,
            "112:2:1",
            "112:2:2",
            1.0,
            merge_group_id="am_1",
            merge_members=[
                {
                    "time_from": 11.06,
                    "time_to": 12.2,
                    "ref_from": "112:2:1",
                    "ref_to": "112:2:1",
                    "confidence": 0.71,
                },
                {
                    "time_from": 12.2,
                    "time_to": 13.94,
                    "ref_from": "112:2:2",
                    "ref_to": "112:2:2",
                    "confidence": 0.64,
                },
            ],
        ),
        _row(4, 14.44, 22.82, "112:3:1", "112:4:5", 0.9734),
    ],
}


# ---------------------------------------------------------------------------
# adapt
# ---------------------------------------------------------------------------


def test_adapt_chapter_shapes_candidate_and_events():
    from services.admin.align_pipeline import adapt

    candidate, events, basmala = adapt.adapt_chapter(
        112, CH112, source_url="https://cdn/112.mp3", riwayah="hafs"
    )
    doc = ChapterCandidateDoc.model_validate(candidate)
    segs = doc.entries[0].segments
    assert doc.entries[0].ref == "112"
    assert [s["matched_ref"] for s in segs] == [
        "112:1:1-112:1:4",
        "112:2:1-112:2:2",
        "112:3:1-112:4:5",
    ]
    assert segs[0]["time_start"] == 7380 and segs[0]["time_end"] == 10940
    assert segs[2]["confidence"] == 0.97  # two decimals
    assert basmala is True

    parsed = [PipelineAuditEvent.model_validate(e) for e in events]
    assert [e.kind for e in parsed] == ["waqf_sakt", "delete_segment"]
    waqf = parsed[0]
    assert [s.index_at_save for s in waqf.targets_before] == [2, 3]
    assert waqf.targets_before[0].matched_ref == "112:2:1-112:2:1"
    assert waqf.targets_after[0].index_at_save == 2
    assert waqf.targets_after[0].final_index == 1  # one stripped Basmala before it
    assert waqf.targets_after[0].confidence == 1.0
    delete = parsed[1]
    assert delete.targets_before[0].index_at_save == 0
    assert delete.targets_before[0].matched_ref == "Basmala"


def test_adapt_writes_a_one_word_ref_as_a_degenerate_span():
    """``a-a``, never a bare ``a``: the text resolvers need two endpoints.

    A single coordinate makes ``dk_text_for_ref`` (and its frontend mirror) return
    no text at all, so the segment renders empty and every text-derived field goes
    with it. Muqattaat openers and repeated single words are the common case.
    """
    from services.admin.align_pipeline import adapt
    from services.reference.quran_refs import dk_text_for_ref

    result = {"segments": [_row(1, 0, 1, "", ""), _row(2, 1, 2, "68:1:1", "68:1:1")]}
    candidate, events, basmala = adapt.adapt_chapter(68, result, source_url="u", riwayah="hafs")
    refs = [s["matched_ref"] for s in candidate["entries"][0]["segments"]]
    assert refs == ["", "68:1:1-68:1:1"]
    assert dk_text_for_ref(refs[1]), "the ref the pipeline writes must resolve to text"
    assert events == [] and basmala is False


def test_adapt_spans_a_missing_ref_to_onto_ref_from():
    """The aligner may send ``ref_to`` empty on a one-word row; still ``a-a``."""
    from services.admin.align_pipeline import adapt

    result = {"segments": [_row(1, 0, 1, "7:1:1", "")]}
    candidate, _, _ = adapt.adapt_chapter(7, result, source_url="u", riwayah="hafs")
    assert candidate["entries"][0]["segments"][0]["matched_ref"] == "7:1:1-7:1:1"


def test_align_batch_requests_only_auto_split_candidate_timings():
    from services.admin.align_pipeline.params import AlignParams

    body = AlignParams().batch_body()
    assert body["include_word_timestamps"] is False
    assert body["include_auto_split_timings"] is True


# ---------------------------------------------------------------------------
# run lifecycle (worker stubbed)
# ---------------------------------------------------------------------------


@pytest.fixture
def align_env(tmp_path, monkeypatch):
    from services import hf_bucket as _hf_bucket
    from services.admin.align_pipeline import progress, runner
    from services.audio import audio_meta
    from services.segments import auto_detect
    from tests.conftest import _seed_catalog, _seed_state

    monkeypatch.setenv("INSPECTOR_FILESYSTEM_ROOT", str(tmp_path))
    monkeypatch.setenv("INSPECTOR_EXTRACTION_SECRET", "s" * 64)
    monkeypatch.setenv("INSPECTOR_HF_TOKEN", "hf_test")
    backend = _hf_bucket.FilesystemBackend(tmp_path)
    _hf_bucket.set_backend(backend)
    _seed_catalog(
        vocab=Vocab(
            riwayat=[Riwayah(slug="hafs", short="H", name="Hafs")],
            styles=[Style(slug="murattal", short="M", name="Murattal")],
            sources=[Source(slug="src1", name="Source One")],
            channels=[Channel(slug="ch1", short="c1", name="Channel One")],
        ),
        reciters=[ReciterEntry(reciter_id="rec_align", name_en="Reciter")],
        deliveries=[
            Delivery(
                slug=SLUG,
                reciter_id="rec_align",
                riwayah="hafs",
                style="murattal",
                source="src1",
                channel="ch1",
                audio_category=AudioCategory.BY_SURAH,
                chapter_count=1,
                channels=2,
                added_at=datetime.now(UTC),
                added_by_hf_id="seed",
            ),
        ],
    )
    _seed_state(SLUG, state="awaiting_alignment", reciter_id="rec_align")
    audio_meta._stage_for_test(
        SLUG,
        {
            "schema_version": 1,
            "slug": SLUG,
            "_meta": {"checksum": "x", "chapter_count": 1, "category": "by_surah"},
            "chapters": {"112": {"url": "https://cdn/112.mp3"}},
        },
    )
    started: list[str] = []
    monkeypatch.setattr(runner, "ensure_worker", lambda run: started.append(run["run_id"]))
    progress._reset_for_tests()
    auto_detect._reset_seen_for_tests()

    yield backend, started

    progress._reset_for_tests()
    auto_detect._reset_seen_for_tests()
    audio_meta._clear_for_test()
    _hf_bucket.reset_backend()


def test_start_retry_cancel_lifecycle(align_env):
    from services.admin.align_pipeline import runs
    from services.db import repo_align_runs

    _backend, started = align_env
    status = runs.start(SLUG, OWNER)
    assert status.stage == "acquire" and status.status == "pending"
    assert status.chapters_total == 1 and status.model_name == "Large"
    assert started == [status.run_id]

    with pytest.raises(runs.AlignRunError) as exc:
        runs.start(SLUG, OWNER)
    assert exc.value.status == 409

    with pytest.raises(runs.AlignRunError):
        runs.retry(SLUG, OWNER)  # only a failed run retries

    from services.db.sync import durable_transaction

    with durable_transaction():
        repo_align_runs.update(status.run_id, status="failed", last_error="boom")
    retried = runs.retry(SLUG, OWNER)
    assert retried.status == "pending" and retried.attempt == 2 and retried.last_error == "boom"

    with durable_transaction():
        repo_align_runs.update(status.run_id, status="failed")
    canceled = runs.cancel(SLUG, OWNER)
    assert canceled.status == "canceled" and canceled.ended_at
    assert repo_align_runs.active_for_slug(SLUG) is None
    # A canceled run no longer blocks a fresh start.
    assert runs.start(SLUG, OWNER).run_id != status.run_id


def test_start_refuses_wrong_state_and_missing_config(align_env, monkeypatch):
    from services.admin.align_pipeline import runs
    from services.state import state as state_service

    state_service.transition(SLUG, "reciter.alignment_completed", actor=OWNER)
    with pytest.raises(runs.AlignRunError) as exc:
        runs.start(SLUG, OWNER)
    assert exc.value.status == 409

    monkeypatch.delenv("INSPECTOR_EXTRACTION_SECRET")
    with pytest.raises(runs.AlignRunError) as exc:
        runs.start(SLUG, OWNER)
    assert exc.value.status == 503


def test_requests_overlay_carries_active_run(align_env):
    from services import pending_requests as pending_requests_service
    from services.admin import requests as admin_requests
    from services.admin.align_pipeline import runs

    pending_requests_service.submit(
        SLUG,
        requester=Actor(hf_user_id="u-c", login_at_time="c", role=Role.CONTRIBUTOR),
        edits=ProposedEdits(),
        comments=None,
    )
    runs.start(SLUG, OWNER)
    payload = admin_requests.list_requests(
        status="open", caller_is_owner=True, caller_hf_id="u-owner"
    )
    row = next(r for r in payload["rows"] if r["slug"] == SLUG)
    assert row["align"]["stage"] == "acquire" and row["align"]["status"] == "pending"


# ---------------------------------------------------------------------------
# assemble + auto_detect gate
# ---------------------------------------------------------------------------


def _slim_blob(duration_ms: int) -> bytes:
    from qua_shared.audio.peaks import pack_slim

    n = max(100, duration_ms * 30 // 1000)
    return pack_slim({"schema_version": 3, "duration_ms": duration_ms, "peaks": [[-0.5, 0.5]] * n})


def test_assemble_publishes_reciter_and_auto_detect_fires(align_env):
    from services.admin.align_pipeline import runs, stage_assemble, staging
    from services.admin.align_pipeline.params import AlignParams
    from services.segments import auto_detect
    from services.state import state as state_service

    backend, _started = align_env
    run = runs.start(SLUG, OWNER)
    staging.write_json(staging.chapter_path(SLUG, run.run_id, 112), CH112)
    staging.write_json(staging.sidecar_path(SLUG, run.run_id, "low_confidence_v2.json"), {"v": 2})
    staging.write_json(staging.sidecar_path(SLUG, run.run_id, "auto_split_v1.json"), {"v": 1})
    backend.write_bytes_atomic(f"reciters/{SLUG}/peaks/112.json.gz", _slim_blob(23000))

    # An audio-only folder must NOT trip auto_detect before assemble.
    assert auto_detect.reconcile_once() == 0
    assert _state(SLUG) == ReciterState.AWAITING_ALIGNMENT

    stage_assemble.run(
        SLUG,
        run.run_id,
        AlignParams(),
        [112],
        {112: "https://cdn/112.mp3"},
        started_at="2026-09-12T00:00:00Z",
    )

    detailed = json.loads(backend.read_bytes(f"reciters/{SLUG}/detailed.json"))
    assert (
        detailed["_meta"]["pad_left_ms"] == 100 and detailed["_meta"]["asr_model"] == "hetchyy/r7"
    )
    segs = detailed["entries"][0]["segments"]
    assert [s["matched_ref"] for s in segs] == [
        "112:1:1-112:1:4",
        "112:2:1-112:2:2",
        "112:3:1-112:4:5",
    ]
    assert segs[1]["segment_uid"]  # the waqf row got its uid
    assert "qalqala_letter" in segs[0] or "is_boundary_adj" in segs[0]  # stamped

    history = [
        json.loads(line)
        for line in backend.read_bytes(f"reciters/{SLUG}/edit_history.jsonl").decode().splitlines()
    ]
    kinds = sorted(op["op_type"] for b in history for op in b["operations"])
    assert kinds == ["delete_segment", "waqf_sakt"]
    waqf = next(op for b in history for op in b["operations"] if op["op_type"] == "waqf_sakt")
    assert waqf["targets_after"][0]["segment_uid"] == segs[1]["segment_uid"]
    assert "final_index" not in waqf["targets_after"][0]

    meta = json.loads(backend.read_bytes(f"reciters/{SLUG}/pipeline_meta.json"))
    assert meta["deleted_basmala_chapters"] == [112]
    assert json.loads(backend.read_bytes(f"reciters/{SLUG}/low_confidence_v2.json")) == {"v": 2}
    assert backend.exists(f"reciters/{SLUG}/edit_history_peaks.jsonl")
    assert backend.exists(f"reciters/{SLUG}/chapter_sources.json")
    assert not backend.exists(staging.chapter_path(SLUG, run.run_id, 112))  # staging torn down

    assert auto_detect.reconcile_once() == 1
    assert _state(SLUG) == ReciterState.AWAITING_REVIEW

    with pytest.raises(stage_assemble.AssembleError):
        stage_assemble.guard(SLUG)  # never overwrite published content


def test_slim_blob_roundtrip_matches_inspector_reader():
    from services.audio.peaks_slim import unpack_slim_envelope

    env = unpack_slim_envelope(_slim_blob(5000))
    assert env and env["schema_version"] == 3 and env["bps"] == 10 and env["duration_ms"] == 5000
    assert gzip.decompress(_slim_blob(5000))[:1] == b"{"


def test_adapt_reverse_maps_projected_rows_to_hafs_source():
    """A Warsh delivery's rows arrive in Warsh coordinates; the Hafs span the
    sidecars + timestamps engine align against is re-derived here, not shipped
    by the aligner. Warsh 57:23 IS Hafs 57:24, and Hafs 57:24:10 is a word
    Warsh does not write, so the support is partial."""
    from services.admin.align_pipeline import adapt
    from services.reference import editions

    if not editions.available():
        pytest.skip("qua-domain not installed (Hafs-only runtime)")
    result = {"segments": [_row(1, 0, 1, "57:23:1", "57:23:11")]}
    candidate, _events, _b = adapt.adapt_chapter(57, result, source_url="u", riwayah="warsh")
    seg = candidate["entries"][0]["segments"][0]
    assert seg["source_ref"] == "57:24:1-57:24:12"
    assert seg["projection_support"] == "partial"
    hafs, _e, _b = adapt.adapt_chapter(57, result, source_url="u", riwayah="hafs")
    assert "source_ref" not in hafs["entries"][0]["segments"][0]


def test_job_command_uses_stock_image_with_system_and_pip_deps():
    from services.admin.jobs import base

    cmd = base.job_command("python /aux/code/qua_jobs/acquire_audio.py", "numpy")
    assert cmd[:2] == ["bash", "-lc"]
    assert "apt-get install -y -qq --no-install-recommends ffmpeg" in cmd[2]
    # brotli + pydantic are BASE deps: qua_shared.verse_layout imports brotli at
    # module scope and every qua_shared.schemas model is pydantic.
    assert (
        "pip install -q --root-user-action=ignore huggingface_hub brotli pydantic numpy" in cmd[2]
    )
    # The staged qua_domain wheel installs after the pip deps, glob-guarded so a
    # Hafs-only image (nothing staged) still launches.
    assert "/aux/code/wheels/qua_domain-*.whl" in cmd[2]
    assert cmd[2].endswith("&& python /aux/code/qua_jobs/acquire_audio.py")
    assert "hf.co/spaces" not in base.JOB_IMAGE


# ---------------------------------------------------------------------------
# D12 — no low-confidence probe off Hafs
# ---------------------------------------------------------------------------


def test_sidecars_stage_skips_a_null_low_confidence(align_env, monkeypatch):
    """The Space answers ``low_confidence_v2: null`` for a non-Hafs delivery;
    the stage must not stage a ``null`` file for assemble to trip over."""
    from services.admin.align_pipeline import runs, stage_sidecars, staging
    from services.admin.align_pipeline.params import AlignParams

    _backend, _started = align_env
    run = runs.start(SLUG, OWNER)
    staging.write_json(staging.chapter_path(SLUG, run.run_id, 112), CH112)
    monkeypatch.setattr(
        stage_sidecars,
        "_call",
        lambda _run_id, _body: {"low_confidence_v2": None, "auto_split_v1": {"by_uid": {}}},
    )

    stage_sidecars.run(
        SLUG, run.run_id, AlignParams(riwayah="warsh"), [112], {112: "https://cdn/112.mp3"}
    )

    assert staging.read_json(staging.sidecar_path(SLUG, run.run_id, "auto_split_v1.json")) == {
        "by_uid": {}
    }
    assert (
        staging.read_json(staging.sidecar_path(SLUG, run.run_id, "low_confidence_v2.json")) is None
    )


def test_sidecars_stage_sends_interactive_timings_in_published_segment_order(
    align_env, monkeypatch
):
    from copy import deepcopy

    from services.admin.align_pipeline import runs, stage_sidecars, staging
    from services.admin.align_pipeline.params import AlignParams

    _backend, _started = align_env
    run = runs.start(SLUG, OWNER)
    chapter = deepcopy(CH112)
    chapter["_inspector"] = {"auto_split_timing_source": "align_stage_interactive_v1"}
    chapter["segments"][3]["words"] = [
        {"location": "112:3:1", "start": 0.1, "end": 0.4},
        {"location": "112:3:2", "start": 0.5, "end": 0.9},
        {"location": "112:4:1", "start": 1.5, "end": 1.9},
        {"location": "112:4:2", "start": 2.0, "end": 2.4},
        {"location": "112:4:3", "start": 2.5, "end": 2.9},
        {"location": "112:4:4", "start": 3.0, "end": 3.4},
        {"location": "112:4:5", "start": 3.5, "end": 3.9},
    ]
    staging.write_json(staging.chapter_path(SLUG, run.run_id, 112), chapter)
    captured = {}

    def fake_call(_run_id, body):
        captured.update(body)
        return {"low_confidence_v2": {"failures": []}, "auto_split_v1": {"by_uid": {}}}

    monkeypatch.setattr(stage_sidecars, "_call", fake_call)

    stage_sidecars.run(
        SLUG, run.run_id, AlignParams(), [112], {112: "https://cdn/112.mp3"}
    )

    assert captured["auto_split_timings"]["112"] == [None, None, chapter["segments"][3]["words"]]


def test_assemble_accepts_a_missing_low_confidence_off_hafs(align_env):
    from services.admin.align_pipeline import runs, stage_assemble, staging
    from services.admin.align_pipeline.params import AlignParams

    backend, _started = align_env
    run = runs.start(SLUG, OWNER)
    staging.write_json(staging.chapter_path(SLUG, run.run_id, 112), CH112)
    staging.write_json(staging.sidecar_path(SLUG, run.run_id, "auto_split_v1.json"), {"v": 1})
    backend.write_bytes_atomic(f"reciters/{SLUG}/peaks/112.json.gz", _slim_blob(23000))

    stage_assemble.run(
        SLUG,
        run.run_id,
        AlignParams(riwayah="warsh"),
        [112],
        {112: "https://cdn/112.mp3"},
        started_at="2026-09-13T00:00:00Z",
    )

    assert backend.exists(f"reciters/{SLUG}/auto_split_v1.json")
    assert not backend.exists(f"reciters/{SLUG}/low_confidence_v2.json")


def test_assemble_still_requires_low_confidence_on_hafs(align_env):
    from services.admin.align_pipeline import runs, stage_assemble, staging
    from services.admin.align_pipeline.params import AlignParams

    backend, _started = align_env
    run = runs.start(SLUG, OWNER)
    staging.write_json(staging.chapter_path(SLUG, run.run_id, 112), CH112)
    staging.write_json(staging.sidecar_path(SLUG, run.run_id, "auto_split_v1.json"), {"v": 1})
    backend.write_bytes_atomic(f"reciters/{SLUG}/peaks/112.json.gz", _slim_blob(23000))

    with pytest.raises(stage_assemble.AssembleError, match="low_confidence_v2"):
        stage_assemble.run(
            SLUG,
            run.run_id,
            AlignParams(),
            [112],
            {112: "https://cdn/112.mp3"},
            started_at="2026-09-13T00:00:00Z",
        )
