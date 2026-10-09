"""Timestamps runs on the aligner Space: stored segment times brought up to date, shards rebuilt.

The Inspector's timestamps stage. Each chapter's times live beside its shard
(``reciters/<slug>/timing/<ch>.json.br``, one entry per segment uid). A run sends a
chapter's detailed.json entries, their bucket audio and those stored times to the
aligner's ``POST /api/v1/extraction/timing``; the aligner keeps every time whose segment
is unchanged, times the rest with the neural timing head and returns the chapter's times
and shards, which are written as returned. A regeneration after edits therefore times
only the edited segments.

``start_run`` writes the ``running`` run record (``jobs/ts/<run_id>.json``, the shape the
batch Space wrote) and works on a daemon thread, stamping the record ``succeeded`` or
``failed`` at the end, so completion, releases and the automations read it unchanged.
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import threading
import uuid
from collections import defaultdict

from qua_shared.schemas import TsJobRecord, TsJobSettings
from services.admin.align_pipeline import params as aligner_params
from services.storage import storage_paths
from services.storage.hf_bucket import StorageNotFound, get_backend, resolve_bucket_repo

log = logging.getLogger("inspector")

_ROUTE = "/api/v1/extraction/timing"
#: One chapter of a slow reciter decodes and times in about a minute on the GPU; the CPU
#: fallback takes several times that.
_READ_TIMEOUT_S = 3600
_MAX_LOG_LINES = 400


class TsAlignerError(RuntimeError):
    """The aligner refused or failed one chapter's timing."""


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


def _run(record: TsJobRecord, riwayah: str, full: bool) -> None:
    def emit(line: str) -> None:
        log.info("[ts %s] %s", record.slug, line)
        record.logs.append(f"{_now()} {line}")
        if len(record.logs) > _MAX_LOG_LINES:
            record.logs = record.logs[-_MAX_LOG_LINES:]
            record.log_truncated = True

    try:
        failed, model = _time_chapters(record.slug, record.settings.chapters, riwayah, full, emit)
        _write_validation(record.slug, failed, record.settings.chapters, model)
        record.status = "succeeded"
    except Exception as exc:  # noqa: BLE001 — the record is the run's only outcome
        log.exception("timestamps run %s failed", record.job_id)
        emit(f"failed: {exc}")
        record.status = "failed"
        record.error = str(exc)[:500]
    record.ended_at = _now()
    _write(record)


def _time_chapters(slug, chapters, riwayah, full, emit) -> tuple[dict[str, list], str]:
    backend = get_backend()
    detailed = json.loads(backend.read_bytes(storage_paths.detailed_path(slug)))
    by_chapter: dict[int, list[dict]] = defaultdict(list)
    for entry in detailed.get("entries", []):
        by_chapter[int(str(entry["ref"]).split(":")[0])].append(entry)
    wanted = (
        sorted(by_chapter) if not chapters else [c for c in sorted(by_chapter) if c in chapters]
    )
    category = _audio_category(detailed, by_chapter)
    emit(f"{len(wanted)} chapter(s), {riwayah}, {category}, full={full}")
    failed: dict[str, list] = {}
    model = ""
    for chapter in wanted:
        reply = _time_chapter(slug, chapter, by_chapter[chapter], riwayah, category, full)
        backend.write_bytes_atomic(_timing_path(slug, chapter), base64.b64decode(reply["times"]))
        for shard_chapter, shard in reply["shards"].items():
            backend.write_bytes_atomic(
                storage_paths.timestamps_path_br(slug, shard_chapter), base64.b64decode(shard)
            )
        if reply["failed_segments"]:
            failed[str(chapter)] = reply["failed_segments"]
        model = reply["model"]
        emit(
            f"ch{chapter}: timed {reply['timed']}, kept {reply['kept']}, failed {reply['failed']}"
            f" ({reply['model']})"
        )
    return failed, model


def _audio_category(detailed: dict, by_chapter: dict[int, list[dict]]) -> str:
    source = str((detailed.get("_meta") or {}).get("audio_source", ""))
    by_ayah = source.startswith("by_ayah") or any(
        ":" in str(e["ref"]) for entries in by_chapter.values() for e in entries
    )
    return "by_ayah_audio" if by_ayah else "by_surah_audio"


def _timing_path(slug: str, chapter: int) -> str:
    return storage_paths.reciter_file(slug, f"timing/{chapter}.json.br")


def _time_chapter(slug, chapter, entries, riwayah, category, full) -> dict:
    import requests

    try:
        times = base64.b64encode(get_backend().read_bytes(_timing_path(slug, chapter))).decode()
    except StorageNotFound:
        times = None
    repo = resolve_bucket_repo()
    body = {
        "slug": slug,
        "chapter": chapter,
        "riwayah": riwayah,
        "audio_category": category,
        "entries": entries,
        "audio_refs": {
            str(e["ref"]): f"hf://buckets/{repo}/reciters/{slug}/audio/{e['ref']}.mp3"
            for e in entries
        },
        "times": times,
        "full": full,
    }
    headers = {
        "Authorization": f"Bearer {aligner_params.hf_token()}",
        "X-Extraction-Secret": aligner_params.extraction_secret(),
    }
    resp = requests.post(
        aligner_params.aligner_url() + _ROUTE,
        json=body,
        headers=headers,
        timeout=(60, _READ_TIMEOUT_S),
    )
    if resp.status_code // 100 != 2:
        raise TsAlignerError(f"ch{chapter}: aligner {resp.status_code}: {resp.text[:300]}")
    return resp.json()


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
