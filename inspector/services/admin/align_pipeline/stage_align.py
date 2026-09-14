"""Stage 2 — align every chapter on the aligner Space, staging each raw result.

One alignment-only batch, one streamed item per chapter by bucket reference
(``hf://buckets/<repo>/reciters/<slug>/audio/<ch>.mp3``). Chapters already staged
are skipped, so a resumed or retried run only pays for what is left. Transient
transport failures retry the chapter; a ``batch_not_found`` (the Space restarted,
or its in-memory registry expired) recreates the batch; a ZeroGPU quota refusal
flips the rest of the run to the CPU lane.

Chapters use a rolling transport pool. The aligner owns resource admission and
interleaves both single and batch requests fairly by caller; this pool merely
avoids opening 114 simultaneous HTTP streams from the Inspector.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

import requests

from services.storage.hf_bucket import resolve_bucket_repo

from . import params as _params
from . import progress, staging
from .aligner_client import AlignerClient, AlignerError
from .params import AlignParams

log = logging.getLogger("inspector")

CHAPTER_ATTEMPTS = 3
RETRY_SLEEP_S = 30
_RECREATE_CODES = ("batch_not_found",)
_CPU_FALLBACK_CODES = ("gpu_quota_exhausted",)
_TRANSIENT = (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)


class AlignStageError(RuntimeError):
    pass


def audio_ref(slug: str, chapter: int) -> str:
    return f"hf://buckets/{resolve_bucket_repo()}/reciters/{slug}/audio/{chapter}.mp3"


class _Batch:
    """The live batch handle, recreated on demand (owner = Space-side IP hash).

    Shared by every worker thread, so creation and the CPU flip are serialized:
    the first thread to find no batch creates one and the rest reuse it.
    """

    def __init__(self, client: AlignerClient, params: AlignParams):
        self.client = client
        self.params = params
        self.device = params.device
        self.batch_id: str | None = None
        self._lock = threading.Lock()

    def id(self) -> str:
        with self._lock:
            if self.batch_id is None:
                self.batch_id = self.client.create_batch(self.params.batch_body(device=self.device))
            return self.batch_id

    def reset(self, *, cpu: bool = False) -> None:
        with self._lock:
            self.batch_id = None
            if cpu:
                self.device = "CPU"


class _Tracker:
    """Staged-chapter bookkeeping + the in-flight view the status route renders."""

    def __init__(self, run_id: str, done: set[int]):
        self.run_id = run_id
        self.done = done
        self._active: dict[int, str] = {}
        self._lock = threading.Lock()

    def enter(self, chapter: int) -> None:
        with self._lock:
            self._active[chapter] = "queued"
        self.publish()

    def stage(self, chapter: int, stage: str | None) -> None:
        with self._lock:
            if chapter in self._active:
                self._active[chapter] = stage or "running"
        self.publish()

    def finish(self, chapter: int, device: str | None) -> None:
        with self._lock:
            self._active.pop(chapter, None)
            self.done.add(chapter)
        self.publish(device=device)

    def publish(self, *, device: str | None = None) -> None:
        with self._lock:
            active = sorted(self._active)
            head = active[0] if active else None
            fields = {
                "chapters_done": len(self.done),
                "chapter": head,
                "in_flight": len(active),
                "aligner_stage": self._active.get(head) if head is not None else None,
            }
        if device is not None:
            fields["device"] = device
        progress.set_detail(self.run_id, **fields)


def run(slug: str, run_id: str, params: AlignParams, chapters: list[int]) -> None:
    client = AlignerClient()
    batch = _Batch(client, params)
    done = set(staging.staged_chapters(slug, run_id))
    pending = [chapter for chapter in chapters if chapter not in done]
    tracker = _Tracker(run_id, done)
    tracker.publish()
    if not pending:
        return

    try:  # create up front so the first worker does not serialize its siblings
        batch.id()
    except Exception as exc:  # noqa: BLE001 - the per-chapter retry loop owns recovery
        log.warning("align %s: initial batch create failed (%s); workers will retry", run_id, exc)
    workers = _params.align_concurrency(len(pending))
    client.widen_pool(workers)
    log.info(
        "align %s: %d chapter(s) left on %d rolling HTTP worker(s)",
        run_id,
        len(pending),
        workers,
    )
    if workers == 1:
        for chapter in pending:
            progress.check_cancel(run_id)
            _stage_chapter(batch, tracker, slug, run_id, chapter)
        return

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="align") as pool:
        futures = [
            pool.submit(_stage_chapter, batch, tracker, slug, run_id, chapter)
            for chapter in pending
        ]
        remaining = set(futures)
        while remaining:
            finished, remaining = wait(remaining, timeout=5, return_when=FIRST_EXCEPTION)
            failure = next((f.exception() for f in finished if f.exception() is not None), None)
            canceled = progress.cancel_requested(run_id)
            if failure is None and not canceled:
                continue
            for future in remaining:
                future.cancel()
            for future in futures:  # let the running ones settle before we unwind
                if not future.cancelled():
                    future.exception()
            if failure is not None:
                raise failure
            progress.check_cancel(run_id)


def _stage_chapter(batch: _Batch, tracker: _Tracker, slug: str, run_id: str, chapter: int) -> None:
    progress.check_cancel(run_id)
    tracker.enter(chapter)
    result = _align_chapter(batch, tracker, slug, run_id, chapter)
    result["_inspector"] = {
        "auto_split_timing_source": _params.AUTO_SPLIT_TIMING_SOURCE,
    }
    staging.write_json(staging.chapter_path(slug, run_id, chapter), result)
    tracker.finish(chapter, result.get("device"))
    log.info(
        "align %s: chapter %d staged (%d segs, %s)",
        run_id,
        chapter,
        len(result.get("segments") or []),
        result.get("device"),
    )


def _align_chapter(batch: _Batch, tracker: _Tracker, slug: str, run_id: str, chapter: int) -> dict:
    last: Exception | None = None
    for attempt in range(1, CHAPTER_ATTEMPTS + 1):
        tracker.stage(chapter, "queued")

        def on_progress(ev: dict) -> None:
            tracker.stage(chapter, ev.get("stage"))

        try:
            return batch.client.align_item(
                batch.id(), chapter, audio_ref(slug, chapter), on_progress
            )
        except AlignerError as exc:
            last = exc
            if exc.code in _RECREATE_CODES:
                log.warning("align %s: batch vanished (%s); recreating", run_id, exc.code)
                batch.reset()
                continue
            if exc.code in _CPU_FALLBACK_CODES and batch.device != "CPU":
                log.warning("align %s: GPU quota exhausted; rest of run on CPU", run_id)
                batch.reset(cpu=True)
                continue
            if exc.status is not None and exc.status < 500:
                break  # a request the Space refuses will not succeed on retry
            log.warning("align %s: chapter %d attempt %d failed: %s", run_id, chapter, attempt, exc)
        except _TRANSIENT as exc:
            last = exc
            log.warning(
                "align %s: chapter %d attempt %d transport error: %s", run_id, chapter, attempt, exc
            )
            batch.reset()  # the Space may have restarted; a new batch is cheap
        if attempt < CHAPTER_ATTEMPTS:
            time.sleep(RETRY_SLEEP_S)
    raise AlignStageError(f"chapter {chapter}: {last}")
