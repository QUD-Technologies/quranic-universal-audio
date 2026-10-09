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


def _serve(monkeypatch, sent: list, *, fail: int | None = None):
    import requests

    def post(url, json, headers, timeout):
        sent.append((url, json, headers))
        ch = json["chapter"]
        if ch == fail:
            return _Reply({"code": "chapter_timing_failed"}, status=502)
        return _Reply({
            "model": "head@1",
            "times": base64.b64encode(f"times-{ch}".encode()).decode(),
            "shards": {str(ch): base64.b64encode(f"shard-{ch}".encode()).decode()},
            "timed": 1, "kept": 0, "failed": 0,
            "failed_segments": [{"seg": 0}] if ch == 113 else [],
        })  # fmt: skip

    monkeypatch.setattr(requests, "post", post)


def _record(backend, run_id: str) -> dict:
    return json.loads(backend.read_bytes(f"reciters/r/jobs/ts/{run_id}.json"))


def test_a_run_times_each_chapter_and_writes_times_shards_and_record(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent)
    record = runner.TsJobRecord(job_id="run1", slug="r", settings=TsJobSettings())
    runner._run(record, "hafs", False)

    assert [body["chapter"] for _, body, _ in sent] == [112, 113]
    url, body, headers = sent[0]
    assert url == "https://aligner/api/v1/extraction/timing"
    assert headers == {"Authorization": "Bearer tok", "X-Extraction-Secret": "sec"}
    assert base64.b64decode(body["times"]) == b"stored-112"
    assert sent[1][1]["times"] is None
    assert body["shards"] is True
    assert body["audio_refs"] == {"112": "hf://buckets/o/b/reciters/r/audio/112.mp3"}
    assert delivery.read_bytes("reciters/r/timing/113.json.br") == b"times-113"
    assert delivery.read_bytes("reciters/r/timestamps/112.json.br") == b"shard-112"
    validation = json.loads(delivery.read_bytes("reciters/r/ts_validation.json"))
    assert validation["failed_segments"] == {"113": [{"seg": 0}]}
    assert validation["_meta"]["aligner_model"] == "head@1"
    assert _record(delivery, "run1")["status"] == "succeeded"


def test_a_scoped_run_keeps_other_chapters_failures(delivery, monkeypatch):
    delivery.write_json_atomic(
        "reciters/r/ts_validation.json", {"failed_segments": {"113": [{"seg": 9}], "112": [1]}}
    )
    sent: list = []
    _serve(monkeypatch, sent)
    record = runner.TsJobRecord(job_id="run2", slug="r", settings=TsJobSettings(chapters=[112]))
    runner._run(record, "hafs", True)

    assert [(b["chapter"], b["full"]) for _, b, _ in sent] == [(112, True)]
    validation = json.loads(delivery.read_bytes("reciters/r/ts_validation.json"))
    assert validation["failed_segments"] == {"113": [{"seg": 9}]}


def test_an_aligner_failure_fails_the_run(delivery, monkeypatch):
    _serve(monkeypatch, [], fail=113)
    record = runner.TsJobRecord(job_id="run3", slug="r", settings=TsJobSettings())
    runner._run(record, "hafs", False)

    stored = _record(delivery, "run3")
    assert stored["status"] == "failed" and "502" in stored["error"]
    assert delivery.read_bytes("reciters/r/timestamps/112.json.br") == b"shard-112"


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
