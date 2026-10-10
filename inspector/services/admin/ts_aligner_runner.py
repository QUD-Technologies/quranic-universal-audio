"""Timestamps runs on the aligner Space: shards built from the stored segment times.

The Inspector's timestamps stage. Each chapter's times live beside its shard
(``reciters/<slug>/timing/<ch>.json.br``, one entry per segment uid), stored at the end of
the align run and kept current after every save (:mod:`services.timing`). A run sends each
chapter to the aligner (:func:`services.timing.aligner_timing.time_chapter`) twice: first
for its times alone, timing only what is still stale (nothing, normally; every segment with
``full``), then for its shards, with the madd lāzim lengths of the whole delivery
(:func:`~services.timing.aligner_timing.delivery_lazim`) as the basis of their reading-variant
picks. Times and shards are written as returned.

Shards are written only when no built
chapter leaves a word, sound or rendered sakt untimed (the aligner's ``untimed`` counts, the
batch re-time's publish gate); otherwise none are and the run fails naming the counts. A Hafs
run that succeeds ends by writing ``recitation_profile.json`` and dropping the cached profile:
the aligner summarizes every chapter, the run's own from the ``profile_samples`` their shard
calls returned and the rest from their shards in the bucket (none missing). A profile that
cannot be built is logged and leaves the run succeeded. A cancel seen after the shards are
built writes neither shards nor profile.

``start_run`` writes the ``running`` run record (``jobs/ts/<run_id>.json``, the shape the
batch Space wrote) and works on a daemon thread, stamping the record ``succeeded`` or
``failed`` at the end, so completion, releases and the automations read it unchanged. A
chapter with a failed segment keeps its previous shards and fails the run; a record marked
``canceled`` stops the run before its next chapter and stays canceled. Whatever the outcome,
the delivery's readings summary (:mod:`services.reference.readings`) is rebuilt from the shards
now in the bucket before the record is closed.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import json
import logging
import threading
import uuid

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from qua_shared.schemas import RecitationProfileDoc, TsJobRecord, TsJobSettings
from services.admin import bucket_flush
from services.reference import readings, recitation_profile
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
        out = _time_chapters(record, riwayah, full, emit)
        _write_validation(record.slug, out.failed, record.settings.chapters, out.model)
        if out.untimed:
            raise RuntimeError(
                f"untimed words, sounds or sakt: {_counts(out.untimed)}; no shards were written"
            )
        if out.failed:
            raise RuntimeError(
                f"segments failed in chapter(s) {sorted(map(int, out.failed))}; their shards "
                "were left as they were (ts_validation.json lists the segments)"
            )
        record.status = "succeeded"
        if riwayah == DEFAULT_SDK_RIWAYAH and not _canceled(record):
            _write_profile(record, out.samples, emit)
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
    readings.refresh_quietly(record.slug)
    record.ended_at = _now()
    _write(record)


@dataclasses.dataclass
class _Outcome:
    failed: dict[str, list]  # chapter -> its failed segments
    model: str
    untimed: dict[str, dict]  # chapter -> its untimed counts
    samples: dict[int, dict]  # chapter -> its recitation-profile samples


def _time_chapters(record: TsJobRecord, riwayah, full, emit) -> _Outcome:
    """Each wanted chapter's times brought current, then its shards built and written, unless
    a segment failed: that chapter's shards stay as they were. No shard is written when a built
    chapter leaves anything untimed. Stops before a chapter, and before the writes, once the
    run is canceled."""
    slug, chapters = record.slug, record.settings.chapters
    wanted = [c for c in aligner_timing.chapters_of(slug) if not chapters or c in chapters]
    emit(f"{len(wanted)} chapter(s), {riwayah}, full={full}")
    timed = _pass(record, wanted, emit, riwayah=riwayah, full=full)
    failed = {str(c): r["failed_segments"] for c, r in timed.items() if r["failed_segments"]}
    lazim = aligner_timing.delivery_lazim(slug, timed)
    emit(f"variant basis: {len(lazim)} madd lazim")
    ready = [c for c in timed if str(c) not in failed]
    built = _pass(record, ready, emit, riwayah=riwayah, shards=True, delivery_lazim_ms=lazim)
    for chapter, reply in built.items():
        if reply["failed_segments"]:
            failed[str(chapter)] = reply["failed_segments"]
    publish = {c: r for c, r in built.items() if str(c) not in failed}
    untimed = _untimed(publish, emit)
    if _canceled(record):
        raise _Canceled
    if not untimed:
        backend = get_backend()
        for reply in publish.values():
            for shard_chapter, shard in reply["shards"].items():
                backend.write_bytes_atomic(
                    storage_paths.timestamps_path_br(slug, shard_chapter), base64.b64decode(shard)
                )
    model = next((r["model"] for r in (*built.values(), *timed.values())), "")
    samples = {c: r["profile_samples"] for c, r in publish.items() if r.get("profile_samples")}
    return _Outcome(failed, model, untimed, samples)


def _untimed(replies: dict[int, dict], emit) -> dict[str, dict]:
    """``{chapter: untimed counts}`` of the built chapters that leave something untimed. A reply
    without coverage comes from an aligner that predates the gate: logged, not counted."""
    out: dict[str, dict] = {}
    totals: dict[str, int] = {}
    unreported = []
    for chapter, reply in replies.items():
        if reply.get("coverage") is None:
            unreported.append(chapter)
            continue
        for key, n in reply["coverage"].items():
            totals[key] = totals.get(key, 0) + n
        if reply.get("untimed"):
            out[str(chapter)] = reply["untimed"]
    if unreported:
        emit(f"coverage not reported by the aligner for chapter(s) {sorted(unreported)}: ungated")
        log.warning("[ts] the aligner reported no coverage for chapter(s) %s", sorted(unreported))
    if totals:
        emit(f"coverage: {totals}")
    if out:
        emit(f"untimed: {_counts(out)}; no shards written")
    return out


def _counts(untimed: dict[str, dict]) -> str:
    return "; ".join(f"ch{ch} {untimed[ch]}" for ch in sorted(untimed, key=int))


def _write_profile(record: TsJobRecord, samples: dict[int, dict], emit) -> None:
    """``recitation_profile.json`` from every chapter: ``samples`` for the chapters this run
    built, the bucket shard for the rest (each must have one, flushed from the mount). A
    failure is logged and the previous profile stays; a cancel during the call writes none."""
    slug = record.slug
    try:
        chapters = aligner_timing.chapters_of(slug)
        shards_dir = storage_paths.reciter_file(slug, "timestamps")
        present = set(get_backend().list_dir_strict(shards_dir))
        stored = [c for c in chapters if c not in samples]
        missing = [c for c in stored if f"{c}.json.br" not in present]
        if missing:
            emit(f"recitation profile not written: no shard for chapter(s) {missing}")
            return
        pending = bucket_flush.unflushed(shards_dir, [f"{c}.json.br" for c in stored])
        if pending:
            emit(f"recitation profile not written: {pending} not flushed to the bucket yet")
            return
        inline = {c: samples[c] for c in chapters if c in samples}
        doc = aligner_timing.delivery_profile(slug, stored, inline)
        RecitationProfileDoc.model_validate(doc)
        if _canceled(record):
            emit("recitation profile not written: the run was canceled")
            return
        get_backend().write_json_atomic(storage_paths.recitation_profile_path(slug), doc)
    except Exception as exc:  # noqa: BLE001 — the run's shards are live either way
        log.exception("[ts %s] recitation profile not written", slug)
        emit(f"recitation profile not written: {type(exc).__name__}: {str(exc)[:300]}")
        return
    finally:
        recitation_profile.drop(slug)
    emit(f"recitation profile written from {len(chapters)} chapter shard(s)")


def _pass(record: TsJobRecord, chapters: list[int], emit, **kwargs) -> dict[int, dict]:
    """One aligner call per chapter; ``{chapter: reply}`` for the chapters that still exist."""
    step = "shards" if kwargs.get("shards") else "times"
    out = {}
    for chapter in chapters:
        if _canceled(record):
            raise _Canceled
        reply = aligner_timing.time_chapter(record.slug, chapter, **kwargs)
        if reply is None:
            continue
        out[chapter] = reply
        untimed = f", untimed {reply['untimed']}" if reply.get("untimed") else ""
        emit(
            f"ch{chapter} {step}: timed {reply['timed']}, kept {reply['kept']}, "
            f"failed {reply['failed']}{untimed} ({reply['model']})"
        )
    return out


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
