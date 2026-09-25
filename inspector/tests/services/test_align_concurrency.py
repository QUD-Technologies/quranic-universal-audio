"""The align stage uses a bounded rolling HTTP pool.

The aligner Space serializes the GPU half of an item internally, so the only
thing these cover is the client side: how many chapter items the Inspector keeps
in flight, that every chapter still lands exactly once, and that a chapter
failure still fails the stage instead of being swallowed by a worker thread.
"""

from __future__ import annotations

import threading
from typing import cast

import pytest

from qua_shared.audio.sources import SourceGroup
from services.admin.align_pipeline import params as align_params
from services.admin.align_pipeline import progress, stage_align
from services.admin.align_pipeline.params import AlignParams

SLUG = "rec_conc"
RUN = "run-conc"


class _StubClient:
    """Counts overlapping ``align_item`` calls and records the peak."""

    def __init__(self, *, expect: int, fail_on: set[int] | None = None):
        self.expect = expect  # flights this stub waits for before releasing
        self.fail_on = fail_on or set()
        self.peak = 0
        self.seen: list[int] = []
        self.batches = 0
        self._active = 0
        self._lock = threading.Lock()
        self._gate = threading.Event()
        self.pool_widened_to: int | None = None

    def create_batch(self, body: dict) -> str:
        with self._lock:
            self.batches += 1
        return "batch-1"

    def widen_pool(self, connections: int) -> None:
        self.pool_widened_to = connections

    def align_item(self, batch_id, chapter, audio_ref, on_progress=None):
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
            self.seen.append(chapter)
            saturated = self._active >= self.expect
        if saturated:
            self._gate.set()  # the pool is full: release everyone
        # Wait until the pool saturates (or give up) so the peak is observable.
        self._gate.wait(timeout=2)
        if on_progress is not None:
            on_progress({"stage": "matching"})
        with self._lock:
            self._active -= 1
        if chapter in self.fail_on:
            raise stage_align.AlignStageError(f"chapter {chapter}: boom")
        return {"segments": [], "device": "GPU"}


@pytest.fixture
def staged(monkeypatch):
    """Capture staged chapter results instead of writing to a bucket."""
    written: dict[int, dict] = {}
    lock = threading.Lock()

    monkeypatch.setattr(stage_align.staging, "staged_chapters", lambda slug, run: [])
    monkeypatch.setattr(stage_align.staging, "staged_sources", lambda slug, run: [])
    monkeypatch.setattr(stage_align.staging, "chapter_path", lambda slug, run, ch: ch)

    def write_json(chapter, doc):
        with lock:
            written[chapter] = doc

    monkeypatch.setattr(stage_align.staging, "write_json", write_json)
    monkeypatch.setattr(stage_align, "audio_ref", lambda slug, ch: f"hf://{slug}/{ch}")
    monkeypatch.setattr(stage_align, "RETRY_SLEEP_S", 0)
    yield written
    progress.clear_detail(RUN)


def _run(monkeypatch, client: _StubClient, chapters: list[int]) -> None:
    monkeypatch.setattr(stage_align, "AlignerClient", lambda: client)
    groups = [SourceGroup(url=f"https://cdn/{ch}.mp3", chapters=(ch,)) for ch in chapters]
    stage_align.run(SLUG, RUN, AlignParams(), groups)


def test_default_pool_rolls_sixteen_chapter_streams(monkeypatch, staged):
    chapters = list(range(1, 21))
    client = _StubClient(expect=16)
    _run(monkeypatch, client, chapters)

    assert sorted(staged) == chapters
    assert client.peak == 16
    assert client.pool_widened_to == 16
    assert client.batches == 1, "every worker shares the one batch"
    assert progress.get_detail(RUN)["chapters_done"] == len(chapters)


def test_env_override_narrows_the_pool(monkeypatch, staged):
    monkeypatch.setenv("INSPECTOR_ALIGN_CONCURRENCY", "1")
    client = _StubClient(expect=1)
    _run(monkeypatch, client, [1, 2, 3])

    assert client.peak == 1
    assert client.seen == [1, 2, 3], "a single worker keeps manifest order"
    assert sorted(staged) == [1, 2, 3]


def test_one_chapter_failure_fails_the_stage(monkeypatch, staged):
    monkeypatch.setenv("INSPECTOR_ALIGN_CONCURRENCY", "2")
    client = _StubClient(expect=2, fail_on={2})
    with pytest.raises(stage_align.AlignStageError, match="chapter 2"):
        _run(monkeypatch, client, [1, 2, 3, 4])
    assert 2 not in staged


def test_cancel_stops_the_pool(monkeypatch, staged):
    client = _StubClient(expect=2)
    progress.request_cancel(RUN)
    try:
        with pytest.raises(progress.Canceled):
            _run(monkeypatch, client, [1, 2, 3, 4])
    finally:
        progress.clear_cancel(RUN)
    assert not staged


class _FakeResponse:
    """Just enough of ``requests.Response`` for ``create_batch``."""

    status_code = 200
    headers: dict[str, str] = {}
    text = ""

    def __init__(self, doc: dict):
        self._doc = doc

    def json(self) -> dict:
        return self._doc


class _FakeSession:
    def __init__(self, doc: dict):
        self._doc = doc
        self.headers: dict[str, str] = {}

    def post(self, *_args, **_kwargs) -> _FakeResponse:
        return _FakeResponse(self._doc)


def _created_batch(doc: dict) -> str:
    import requests

    from services.admin.align_pipeline.aligner_client import AlignerClient

    session = cast(requests.Session, _FakeSession(doc))
    client = AlignerClient(base_url="https://aligner.test", session=session)
    return client.create_batch({})


def test_create_batch_only_needs_the_opaque_id():
    assert _created_batch({"batch_id": "b"}) == "b"


def test_default_transport_pool_does_not_fan_out_all_chapters():
    assert align_params.align_concurrency(114) == 16
