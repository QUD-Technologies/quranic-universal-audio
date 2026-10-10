"""Stored segment times: times-alone calls, the post-save queue, and the card read-back."""

from __future__ import annotations

import base64
import json
import threading

import brotli
import pytest

from services.timing import aligner_timing, retime_queue, word_times


class _Reply:
    status_code = 200

    def __init__(self, body: dict):
        self._body, self.text = body, json.dumps(body)

    def json(self) -> dict:
        return self._body


def _doc(segments: dict) -> bytes:
    doc = {"_meta": {"schema_version": 1, "chapter": 112}, "segments": segments}
    return brotli.compress(json.dumps(doc).encode())


SEG = {
    "segment_uid": "a",
    "matched_ref": "112:1:1-112:1:2",
    "confidence": 1.0,
    "time_start": 1000,
    "time_end": 3000,
}


@pytest.fixture
def delivery(state_persistence, monkeypatch):
    backend = state_persistence
    backend.write_json_atomic(
        "reciters/r/detailed.json", {"_meta": {}, "entries": [{"ref": "112", "segments": [SEG]}]}
    )
    monkeypatch.setattr(aligner_timing.aligner_params, "hf_token", lambda: "tok")
    monkeypatch.setattr(aligner_timing.aligner_params, "extraction_secret", lambda: "sec")
    monkeypatch.setattr(aligner_timing.aligner_params, "aligner_url", lambda: "https://aligner")
    monkeypatch.setattr(aligner_timing, "resolve_bucket_repo", lambda: "o/b")
    monkeypatch.setattr("services.reference.delivery_edition.sdk_riwayah_for", lambda slug: "hafs")
    return backend


def _serve(monkeypatch, sent: list):
    import requests

    def post(url, json, headers, timeout):
        sent.append(json)
        return _Reply({
            "model": "head@1", "times": base64.b64encode(b"times").decode(), "shards": {},
            "timed": 1, "kept": 0, "failed": 0, "failed_segments": [],
            "frames": {"112": base64.b64encode(b"index").decode()},
        })  # fmt: skip

    monkeypatch.setattr(requests, "post", post)


def test_retime_stores_times_alone(delivery, monkeypatch):
    sent: list = []
    _serve(monkeypatch, sent)
    out = aligner_timing.retime("r", [112, 7])

    assert list(out) == [112]
    assert sent[0]["shards"] is False and sent[0]["full"] is False
    assert sent[0]["audio_category"] == "by_surah_audio"
    assert delivery.read_bytes("reciters/r/timing/112.json.br") == b"times"
    assert sent[0]["frame_refs"] == {"112": "hf://buckets/o/b/reciters/r/audio_frames/112.bin"}
    assert delivery.read_bytes("reciters/r/audio_frames/112.bin") == b"index"


def test_word_times_show_only_segments_still_timed_as_stored():
    words = [["112:1:1", 0, 700, [], []], ["112:1:2", 700, None, [], []]]
    doc = json.loads(brotli.decompress(_doc({
        "a": {"ref": "112:1:1-112:1:2", "span": [1000, 3000], "status": "ok", "words": words},
        "b": {"ref": "112:2:1-112:2:2", "span": [3000, 5000], "status": "ok", "words": words},
        "c": {"ref": "112:3:1-112:3:2", "span": [5000, 6000], "status": "error"},
    })))  # fmt: skip
    trimmed = {**SEG, "segment_uid": "b", "matched_ref": "112:2:1-112:2:2", "time_end": 4800}
    failed = {**SEG, "segment_uid": "c", "matched_ref": "112:3:1-112:3:2", "time_start": 5000,
              "time_end": 6000}  # fmt: skip
    out = word_times.chapter_word_times([{"ref": "112", "segments": [SEG, trimmed, failed]}], doc)

    assert out == {
        "a": {
            "start_ms": 1000,
            "end_ms": 3000,
            "words": [
                {"location": "112:1:1", "start_ms": 1000, "end_ms": 1700},
                {"location": "112:1:2", "start_ms": 1700, "end_ms": 1700},
            ],
        }
    }


def test_word_times_route_reads_the_stored_doc(delivery, flask_client, monkeypatch):
    from routes.segments import data as route

    words = [["112:1:1", 0, 900, [], []], ["112:1:2", 900, 1900, [], []]]
    delivery.write_bytes_atomic(
        "reciters/r/timing/112.json.br",
        _doc({"a": {"ref": "112:1:1-112:1:2", "span": [1000, 3000], "status": "ok",
                    "words": words}}),
    )  # fmt: skip
    monkeypatch.setattr(route.state_service, "has_content_access", lambda slug: True)
    monkeypatch.setattr(route, "load_detailed", lambda slug: [{"ref": "112", "segments": [SEG]}])
    resp = flask_client.get("/api/seg/word-times/r/112")

    assert resp.status_code == 200
    assert resp.get_json()["segments"]["a"]["words"][1] == {
        "location": "112:1:2",
        "start_ms": 1900,
        "end_ms": 2900,
    }
    assert flask_client.get("/api/seg/word-times/r/113").get_json() == {"segments": {}}


def test_a_save_burst_is_timed_once_more_after_it_settles(monkeypatch):
    monkeypatch.setattr(retime_queue, "SETTLE_S", 0.05)
    monkeypatch.setattr(aligner_timing, "enabled", lambda: True)
    started, release, calls = threading.Event(), threading.Event(), []

    def retime(slug, chapters):
        calls.append((slug, chapters))
        started.set()
        release.wait(2)

    monkeypatch.setattr(aligner_timing, "retime", retime)
    retime_queue.schedule("r", [112])
    assert started.wait(2)
    retime_queue.schedule("r", [112])
    retime_queue.schedule("r", [112])
    release.set()
    for _ in range(100):
        with retime_queue._lock:
            if ("r", 112) not in retime_queue._running:
                break
        threading.Event().wait(0.02)
    assert calls == [("r", [112]), ("r", [112])]


def test_schedule_is_a_no_op_when_timing_is_off(monkeypatch):
    monkeypatch.setenv("INSPECTOR_TS_ENGINE", "space")
    monkeypatch.setattr(aligner_timing, "retime", lambda *a, **k: pytest.fail("timed while off"))
    retime_queue.schedule("r", [112])
    assert ("r", 112) not in retime_queue._running


def test_align_times_every_chapter_then_fails_on_any_failure(monkeypatch):
    from services.admin.align_pipeline import stage_assemble

    tried = []

    def time_chapter(slug, chapter, **kw):
        tried.append(chapter)
        if chapter == 1:
            raise aligner_timing.TimingCallError("ch1: aligner 502")

    monkeypatch.setattr(aligner_timing, "enabled", lambda: True)
    monkeypatch.setattr(aligner_timing, "chapters_of", lambda slug: [1, 2])
    monkeypatch.setattr(aligner_timing, "time_chapter", time_chapter)
    with pytest.raises(stage_assemble.AssembleError, match=r"chapters \[1\]"):
        stage_assemble._store_times("r", "run", "hafs")
    assert tried == [1, 2]


def test_segments_without_a_stored_uid_are_sent_with_the_read_path_uid(delivery, monkeypatch):
    from domain.identity import derive_uid

    bare = {k: v for k, v in SEG.items() if k != "segment_uid"}
    delivery.write_json_atomic(
        "reciters/r/detailed.json", {"_meta": {}, "entries": [{"ref": "112", "segments": [bare]}]}
    )
    sent: list = []
    _serve(monkeypatch, sent)
    aligner_timing.retime("r", [112])
    uid = sent[0]["entries"][0]["segments"][0]["segment_uid"]
    assert uid == derive_uid(chapter=112, original_index=0, start_ms=1000)


def test_a_projected_segment_is_relabelled_word_for_word():
    words = [["112:1:1", 0, 500, [], []], ["112:1:2", 500, 900, [], []]]
    doc = {"_meta": {}, "segments": {
        "a": {"ref": "112:1:1-112:1:2", "span": [0, 1000], "status": "ok", "words": words},
        "b": {"ref": "112:1:1-112:1:2", "span": [1000, 2000], "status": "ok", "words": words},
    }}  # fmt: skip
    same = {"segment_uid": "a", "matched_ref": "112:2:1-112:2:2", "source_ref": "112:1:1-112:1:2",
            "confidence": 1.0, "time_start": 0, "time_end": 1000}  # fmt: skip
    longer = {**same, "segment_uid": "b", "matched_ref": "112:2:1-112:2:3", "time_start": 1000,
              "time_end": 2000}  # fmt: skip
    out = word_times.chapter_word_times(
        [{"ref": "112", "segments": [same, longer]}], doc, {(112, 2): 3}
    )
    assert [w["location"] for w in out["a"]["words"]] == ["112:2:1", "112:2:2"]
    assert "b" not in out
