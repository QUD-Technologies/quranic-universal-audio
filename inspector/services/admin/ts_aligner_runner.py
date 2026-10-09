"""Timestamps runs on the aligner Space: shards built from the stored segment times.

The Inspector's timestamps stage. Each chapter's times live beside its shard
(``reciters/<slug>/timing/<ch>.json.br``, one entry per segment uid), stored at the end of
the align run and kept current after every save (:mod:`services.timing`). A run sends each
chapter to the aligner (:func:`services.timing.aligner_timing.time_chapter`), which times
only what is still stale (nothing, normally; every segment with ``full``) and returns the
chapter's times and shards, written as returned.

``start_run`` writes the ``running`` run record (``jobs/ts/<run_id>.json``, the shape the
batch Space wrote) and works on a daemon thread, stamping the record ``succeeded`` or
``failed`` at the end, so completion, releases and the automations read it unchanged. A
chapter with a failed segment keeps its previous shards and fails the run; a record marked
``canceled`` stops the run before its next chapter and stays canceled.
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import threading
import uuid

from qua_shared.schemas import TsJobRecord, TsJobSettings
from services.storage import storage_paths
from services.storage.hf_bucket import StorageNotFound, get_backend
from services.timing import aligner_timing

log = logging.getLogger("inspector")

_MAX_LOG_LINES = 400


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_run(
    slug: str,
    *,
    settings: TsJobSettings,
    riwayah: str,
    full: bool = False,
) -> str:
    """Write the ``running`` record for a new run of ``slug`` and start it; returns its id."""
    run_id = f"ts-{datetime.datetime.now(datetime.UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    record = TsJobRecord(
        job_id=run_id,
        slug=slug,
        settings=settings,
        status="running",
        chapters_refreshed=settings.chapters,
        started_at=_now(),
    )
    _write(record)
    threading.Thread(
        target=_run, args=(record, riwayah, full), name=f"ts-{slug}", daemon=True
    ).start()
    return run_id


def _write(record: TsJobRecord) -> None:
    path = f"reciters/{record.slug}/jobs/ts/{record.job_id}.json"
    get_backend().write_json_atomic(path, record.model_dump(exclude_none=True))


class _Canceled(Exception):
    """The run's record was marked canceled while it ran."""


def _canceled(record: TsJobRecord) -> bool:
    from services.admin.timestamps_jobs import read_job_record

    return (read_job_record(record.slug, record.job_id) or {}).get("status") == "canceled"


def _run(record: TsJobRecord, riwayah: str, full: bool) -> None:
    def emit(line: str) -> None:
        log.info("[ts %s] %s", record.slug, line)
        record.logs.append(f"{_now()} {line}")
        if len(record.logs) > _MAX_LOG_LINES:
            record.logs = record.logs[-_MAX_LOG_LINES:]
            record.log_truncated = True

    try:
        failed, model = _time_chapters(record, riwayah, full, emit)
        _write_validation(record.slug, failed, record.settings.chapters, model)
        if failed:
            raise RuntimeError(
                f"segments failed in chapter(s) {sorted(map(int, failed))}; their shards were "
                "left as they were (ts_validation.json lists the segments)"
            )
        record.status = "succeeded"
    except _Canceled:
        emit("canceled")
        record.status = "canceled"
    except Exception as exc:  # noqa: BLE001 — the record is the run's only outcome
        log.exception("timestamps run %s failed", record.job_id)
        emit(f"failed: {exc}")
        record.status = "failed"
        record.error = str(exc)[:500]
    if record.status != "canceled" and _canceled(record):
        record.status = "canceled"
    record.ended_at = _now()
    _write(record)


def _time_chapters(record: TsJobRecord, riwayah, full, emit) -> tuple[dict[str, list], str]:
    """Each wanted chapter timed and its shards written, unless a segment failed: that
    chapter's shards stay as they were. Stops before a chapter once the run is canceled."""
    slug, chapters = record.slug, record.settings.chapters
    wanted = [c for c in aligner_timing.chapters_of(slug) if not chapters or c in chapters]
    emit(f"{len(wanted)} chapter(s), {riwayah}, full={full}")
    backend = get_backend()
    failed: dict[str, list] = {}
    model = ""
    for chapter in wanted:
        if _canceled(record):
            raise _Canceled
        reply = aligner_timing.time_chapter(slug, chapter, riwayah=riwayah, full=full, shards=True)
        if reply is None:
            continue
        model = reply["model"]
        emit(
            f"ch{chapter}: timed {reply['timed']}, kept {reply['kept']}, failed {reply['failed']}"
            f" ({reply['model']})"
        )
        if reply["failed_segments"]:
            failed[str(chapter)] = reply["failed_segments"]
            continue
        for shard_chapter, shard in reply["shards"].items():
            backend.write_bytes_atomic(
                storage_paths.timestamps_path_br(slug, shard_chapter), base64.b64decode(shard)
            )
    return failed, model


def _write_validation(
    slug: str, failed: dict[str, list], chapters: list[int] | None, model: str
) -> None:
    """``ts_validation.json``: the segments the run could not time, merged per chapter."""
    backend = get_backend()
    path = storage_paths.reciter_file(slug, "ts_validation.json")
    try:
        prior = (json.loads(backend.read_bytes(path)) or {}).get("failed_segments") or {}
    except (StorageNotFound, ValueError):
        prior = {}
    keep = {ch: rows for ch, rows in prior.items() if chapters and int(ch) not in chapters}
    keep.update(failed)
    doc = {
        "_meta": {
            "reciter": slug,
            "aligner_model": model,
            "method": "neural",
            "created_at": _now(),
        },
        "verses": {},
        "failed_segments": dict(sorted(keep.items(), key=lambda kv: int(kv[0]))),
    }
    backend.write_json_atomic(path, doc)
