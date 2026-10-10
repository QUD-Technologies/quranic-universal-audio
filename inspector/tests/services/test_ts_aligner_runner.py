"""Timestamps runs on the aligner: stored times sent and written back, the run record kept."""

from __future__ import annotations

import base64
import json

import pytest

from qua_shared.schemas import TsJobSettings
from services.admin import ts_aligner_runner as runner
from services.timing import aligner_timing


class _Reply:
    def __init__(self, body: dict, status: int = 200):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self) -> dict:
        return self._body


@pytest.fixture
def delivery(state_persistence, monkeypatch):
    backend = state_persistence
    detailed = {
        "_meta": {},
        "entries": [
            {"ref": "112", "segments": [{"segment_uid": "a", "matched_ref": "112:1:1-112:1:4"}]},
            {"ref": "113", "segments": [{"segment_uid": "b", "matched_ref": "113:1:1-113:1:4"}]},
        ],
    }
    backend.write_json_atomic("reciters/r/detailed.json", detailed)
    backend.write_bytes_atomic("reciters/r/timing/112.json.br", b"stored-112")
    monkeypatch.setattr(aligner_timing.aligner_params, "hf_token", lambda: "tok")
    monkeypatch.setattr(aligner_timing.aligner_params, "extraction_secret", lambda: "sec")
    monkeypatch.setattr(aligner_timing.aligner_params, "aligner_url", lambda: "https://aligner")
    monkeypatch.setattr(aligner_timing, "resolve_bucket_repo", lambda: "o/b")
    return backend


PROFILE = {"schema_version": 1, "madd": {"tabii": {"n": 2, "mean_ms": 300}}}


def _serve(
    monkeypatch,
    sent: list,
    *,
    fail: int | None = None,
    failing: tuple[int, ...] = (113,),
    untimed: dict[int, dict] | None = None,
    profile: dict | None = None,
):
    import requests

    def post(url, json, headers, timeout):
        sent.append((url, json, headers))
        if url.endswith("/recitation-profile"):
            if profile is None:
                return _Reply({"code": "shard_not_profilable"}, status=422)
            return _Reply({"profile": profile, "chapters": len(json["shard_refs"])})
        ch = json["chapter"]
        if ch == fail:
            return _Reply({"code": "chapter_timing_failed"}, status=502)
        left = (untimed or {}).get(ch, {})
        return _Reply({
            "model": "head@1",
            "times": base64.b64encode(f"times-{ch}".encode()).decode(),
            "shards": {str(ch): base64.b64encode(f"shard-{ch}".encode()).decode()},
            "timed": 1, "kept": 0, "failed": 0,
            "failed_segments": [{"seg": 0}] if ch in failing else [],
            "lazim_ms": [400.0 + ch],
            "coverage": {"shard_sounds_null": 0, **left} if json["shards"] else None,
            "untimed": left if json["shards"] else None,
            "profile_samples": {"tabii": [ch]} if json["shards"] else None,
        })  # fmt: skip

    monkeypatch.setattr(requests, "post", post)


def _timing_calls(sent: list) -> list:
    return [b for url, b, _ in sent if url.endswith("/extraction/timing")]


def _record(backend, run_id: str) -> dict:
    return json.loads(backend.read_bytes(f"reciters/r/jobs/ts/{run_id}.json"))


def test_a_run_times_each_chapter_and_writes_times_shards_and_record(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent)
    record = runner.TsJobRecord(job_id="run1", slug="r", settings=TsJobSettings())
    runner._run(record, "hafs", False)

    # Times for every chapter, then shards for the chapters without a failed segment.
    assert [(b["chapter"], b["shards"]) for b in _timing_calls(sent)] == [
        (112, False), (113, False), (112, True)
    ]  # fmt: skip
    url, body, headers = sent[0]
    assert url == "https://aligner/api/v1/extraction/timing"
    assert headers == {"Authorization": "Bearer tok", "X-Extraction-Secret": "sec"}
    assert base64.b64decode(body["times"]) == b"stored-112"
    assert sent[1][1]["times"] is None
    assert body["delivery_lazim_ms"] is None
    assert sent[2][1]["delivery_lazim_ms"] == [512.0, 513.0]
    assert base64.b64decode(sent[2][1]["times"]) == b"times-112"
    assert body["audio_refs"] == {"112": "hf://buckets/o/b/reciters/r/audio/112.mp3"}
    assert delivery.read_bytes("reciters/r/timing/113.json.br") == b"times-113"
    assert delivery.read_bytes("reciters/r/timestamps/112.json.br") == b"shard-112"
    # A chapter with a failed segment keeps its previous shards and fails the run.
    assert not delivery.exists("reciters/r/timestamps/113.json.br")
    validation = json.loads(delivery.read_bytes("reciters/r/ts_validation.json"))
    assert validation["failed_segments"] == {"113": [{"seg": 0}]}
    assert validation["_meta"]["aligner_model"] == "head@1"
    stored = _record(delivery, "run1")
    assert stored["status"] == "failed" and "113" in stored["error"]


def test_a_canceled_run_stops_and_stays_canceled(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent)
    record = runner.TsJobRecord(job_id="run4", slug="r", settings=TsJobSettings())
    runner._write(record.model_copy(update={"status": "canceled"}))
    runner._run(record, "hafs", False)

    assert sent == []
    assert _record(delivery, "run4")["status"] == "canceled"


def test_a_scoped_run_keeps_other_chapters_failures(delivery, monkeypatch):
    delivery.write_json_atomic(
        "reciters/r/ts_validation.json", {"failed_segments": {"113": [{"seg": 9}], "112": [1]}}
    )
    sent: list = []
    _serve(monkeypatch, sent)
    record = runner.TsJobRecord(job_id="run2", slug="r", settings=TsJobSettings(chapters=[112]))
    runner._run(record, "hafs", True)

    assert [(b["chapter"], b["full"], b["shards"]) for b in _timing_calls(sent)] == [
        (112, True, False), (112, False, True)
    ]  # fmt: skip
    validation = json.loads(delivery.read_bytes("reciters/r/ts_validation.json"))
    assert validation["failed_segments"] == {"113": [{"seg": 9}]}


def test_an_aligner_failure_fails_the_run(delivery, monkeypatch):
    _serve(monkeypatch, [], fail=113)
    record = runner.TsJobRecord(job_id="run3", slug="r", settings=TsJobSettings())
    runner._run(record, "hafs", False)

    # Its times are kept; no shard is built without the whole delivery's basis.
    stored = _record(delivery, "run3")
    assert stored["status"] == "failed" and "502" in stored["error"]
    assert delivery.read_bytes("reciters/r/timing/112.json.br") == b"times-112"
    assert not delivery.exists("reciters/r/timestamps/112.json.br")


def test_launch_starts_an_aligner_run_by_default(monkeypatch):
    from services.admin import timestamps_jobs
    from services.state import state as state_service
    from services.storage import cache as _cache
    from services.storage import data_loader

    monkeypatch.delenv("INSPECTOR_TS_ENGINE", raising=False)
    monkeypatch.setattr(state_service, "get_row", lambda slug: object())
    monkeypatch.setattr(state_service, "record_timestamps_job", lambda s, j: None)
    monkeypatch.setattr(_cache, "invalidate_in_flight_jobs_cache", lambda: None)
    monkeypatch.setattr(data_loader, "seg_meta", lambda slug: {})
    started = {}
    monkeypatch.setattr(
        runner, "start_run", lambda slug, **kw: started.update(slug=slug, **kw) or "ts-1"
    )
    out = timestamps_jobs.launch("r", settings=TsJobSettings(chapters=[1]), full=True)

    assert out == {"job_id": "ts-1", "url": None}
    assert started["slug"] == "r" and started["full"] is True and started["riwayah"] == "hafs"


def test_a_run_rebuilds_the_readings_summary_before_closing(delivery, monkeypatch):
    _serve(monkeypatch, [])
    rebuilt = []
    monkeypatch.setattr(runner.readings, "refresh_quietly", rebuilt.append)
    record = runner.TsJobRecord(job_id="run9", slug="r", settings=TsJobSettings())
    runner._run(record, "hafs", False)
    assert rebuilt == ["r"]
    assert _record(delivery, "run9")["ended_at"]


def test_an_untimed_word_sound_or_sakt_writes_no_shard_and_fails_the_run(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent, failing=(), untimed={113: {"sakt_untimed": 1}})
    record = runner.TsJobRecord(job_id="run5", slug="r", settings=TsJobSettings())
    runner._run(record, "hafs", False)

    assert not delivery.exists("reciters/r/timestamps/112.json.br")
    assert not delivery.exists("reciters/r/timestamps/113.json.br")
    stored = _record(delivery, "run5")
    assert stored["status"] == "failed"
    assert "ch113 {'sakt_untimed': 1}" in stored["error"] and "no shards" in stored["error"]
    assert not delivery.exists("reciters/r/recitation_profile.json")


def test_live_timestamps_are_backed_up_before_the_run_writes(delivery, monkeypatch):
    delivery.write_bytes_atomic("reciters/r/timestamps/112.json.br", b"live-112")
    delivery.write_json_atomic("reciters/r/recitation_profile.json", {"schema_version": 1})
    _serve(monkeypatch, [], failing=())
    record = runner.TsJobRecord(job_id="run6", slug="r", settings=TsJobSettings())
    runner._run(record, "fake", False)

    (stamp,) = [d for d in delivery.list_dir("backups") if d.startswith("timestamps-pre-online-")]
    root = f"backups/{stamp}/reciters/r"
    assert delivery.read_bytes(f"{root}/timestamps/112.json.br") == b"live-112"
    assert delivery.read_bytes(f"{root}/timing/112.json.br") == b"stored-112"
    assert delivery.exists(f"{root}/recitation_profile.json")
    assert delivery.read_bytes("reciters/r/timestamps/112.json.br") == b"shard-112"
    assert any(f"backup: {root}" in line for line in _record(delivery, "run6")["logs"])


def test_a_failed_backup_fails_the_run_before_anything_is_written(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent, failing=())

    def broken(*_a, **_k):
        raise OSError("copy refused")

    monkeypatch.setattr(runner.ts_backup, "_copy_tree", broken)
    record = runner.TsJobRecord(job_id="run7", slug="r", settings=TsJobSettings())
    runner._run(record, "hafs", False)

    assert sent == []
    stored = _record(delivery, "run7")
    assert stored["status"] == "failed" and "copy refused" in stored["error"]
    assert delivery.read_bytes("reciters/r/timing/112.json.br") == b"stored-112"


def test_a_hafs_run_writes_the_profile_and_drops_the_cached_one(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent, failing=(), profile=PROFILE)
    dropped = []
    monkeypatch.setattr(runner.recitation_profile, "drop", dropped.append)
    record = runner.TsJobRecord(job_id="run8", slug="r", settings=TsJobSettings(chapters=[112]))
    delivery.write_bytes_atomic("reciters/r/timestamps/113.json.br", b"live-113")
    runner._run(record, "hafs", False)

    url, body, _ = sent[-1]
    assert url == "https://aligner/api/v1/extraction/recitation-profile"
    # The run's chapter goes as the samples its shard call returned, the rest by shard.
    assert body == {
        "shard_refs": {"113": "hf://buckets/o/b/reciters/r/timestamps/113.json.br"},
        "samples": {"112": {"tabii": [112]}},
    }
    assert delivery.read_json("reciters/r/recitation_profile.json") == PROFILE
    assert dropped == ["r"] and _record(delivery, "run8")["status"] == "succeeded"


def test_no_profile_without_every_chapters_shard_or_outside_hafs(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent, failing=(), profile=PROFILE)
    record = runner.TsJobRecord(job_id="run10", slug="r", settings=TsJobSettings(chapters=[112]))
    runner._run(record, "hafs", False)
    assert not delivery.exists("reciters/r/recitation_profile.json")
    assert any(
        "no shard for chapter(s) [113]" in line for line in _record(delivery, "run10")["logs"]
    )

    record = runner.TsJobRecord(job_id="run11", slug="r", settings=TsJobSettings())
    runner._run(record, "warsh", False)
    assert not any(url.endswith("/recitation-profile") for url, _, _ in sent)
    assert _record(delivery, "run11")["status"] == "succeeded"


def test_a_profile_the_aligner_refuses_leaves_the_run_succeeded(delivery, monkeypatch):
    _serve(monkeypatch, [], failing=(), profile=None)
    record = runner.TsJobRecord(job_id="run12", slug="r", settings=TsJobSettings())
    runner._run(record, "hafs", False)

    stored = _record(delivery, "run12")
    assert stored["status"] == "succeeded"
    assert any("recitation profile not written" in line for line in stored["logs"])
    assert not delivery.exists("reciters/r/recitation_profile.json")


def test_a_cancel_during_the_build_writes_no_shard_or_profile(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent, failing=(), profile=PROFILE)
    record = runner.TsJobRecord(job_id="run13", slug="r", settings=TsJobSettings())
    calls = []

    def canceled(rec):
        calls.append(1)
        return len(calls) > 4  # before each of the four chapter calls: running

    monkeypatch.setattr(runner, "_canceled", canceled)
    runner._run(record, "hafs", False)

    assert not delivery.exists("reciters/r/timestamps/112.json.br")
    assert not any(url.endswith("/recitation-profile") for url, _, _ in sent)
    assert _record(delivery, "run13")["status"] == "canceled"


def test_a_mount_file_newer_than_the_bucket_counts_as_unflushed(tmp_path):
    import datetime
    import os
    from types import SimpleNamespace

    from services.admin import ts_backup

    local = tmp_path / "1.json.br"
    local.write_bytes(b"abc")
    os.utime(local, (1000, 1000))
    at = datetime.datetime.fromtimestamp

    def remote(size=3, when=2000):
        return SimpleNamespace(size=size, mtime=at(when, datetime.UTC), uploaded_at=None)

    assert not ts_backup._unflushed(local, remote())
    assert ts_backup._unflushed(local, remote(when=500))
    assert ts_backup._unflushed(local, remote(size=4))
    assert ts_backup._unflushed(local, None)
    assert not ts_backup._unflushed(None, remote())
