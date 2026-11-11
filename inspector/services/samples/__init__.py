"""Maintainer-uploaded alignment samples.

A sample is one audio file plus one aligner-contract JSON. Ingest converts the
JSON to the bucket segment schema (``convert.py``), normalises the audio to MP3
and bakes peaks (``audio_ingest.py``), and lays everything out under
``samples/<id>/`` with the per-reciter file names so the Segments view and
every ``/api/seg/*`` route work on the slug ``sample--<id>`` unchanged. The
``samples`` table indexes the folder: owner, name, ingest status, and the
save/export timestamps behind the "changed since export" badge.

Peaks are computed on a daemon thread after the request returns; the row sits
in ``processing`` until they land. Flask-free; writes open their own
``durable_transaction``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import orjson

from adapters.segments_json import build_segments_doc
from services.auth import permissions
from services.db import repo_access, repo_samples
from services.db import sync as _sync
from services.storage import cache, data_dir, storage_paths
from services.storage.data_loader import load_detailed
from services.storage.hf_bucket import get_backend
from utils.uuid7 import uuid7

from . import audio_ingest
from .convert import (
    SampleConvertError,
    alignment_to_detailed,
    detailed_to_alignment,
    resolve_pseudo_chapter,
    sniff_envelope,
)

logger = logging.getLogger(__name__)

NAME_MAX = 120
SOURCE_JSON_MAX_BYTES = 50 * 1024 * 1024


class SampleError(ValueError):
    """Invalid sample input (bad JSON, bad audio, bad name)."""


class SampleNotFound(LookupError):
    pass


class SampleForbidden(PermissionError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sample_url(sample_id: str, chapter: int) -> str:
    """Synthetic chapter URL: not ``/api/``-prefixed, so the FE routes playback
    through the audio proxy, which resolves it to the bucket MP3."""
    return f"qua-sample://{sample_id}/{chapter}"


def _audio_manifest(sample_id: str, chapter: int, *, size_bytes: int, probe: dict) -> dict:
    return {
        "schema_version": 1,
        "slug": storage_paths.sample_slug(sample_id),
        "_meta": {"chapter_count": 1, "category": "by_surah", "created_at": _now_iso()},
        "chapters": {
            str(chapter): {
                "url": _sample_url(sample_id, chapter),
                "size_bytes": size_bytes,
                "duration_sec": probe["duration_ms"] / 1000,
                "bitrate_kbps": probe.get("bitrate_kbps"),
                "bitrate_mode": "cbr",
            }
        },
    }


def _parse_source(json_bytes: bytes) -> tuple[str, dict, dict]:
    if len(json_bytes) > SOURCE_JSON_MAX_BYTES:
        raise SampleError("JSON is too large")
    try:
        doc = orjson.loads(json_bytes)
    except orjson.JSONDecodeError as exc:
        raise SampleError(f"JSON could not be parsed: {exc}") from exc
    try:
        kind, alignment = sniff_envelope(doc)
    except SampleConvertError as exc:
        raise SampleError(str(exc)) from exc
    return kind, doc, alignment


def create_sample(
    *, user: Any, name: str, audio_path: Path, audio_filename: str, json_bytes: bytes
) -> dict:
    """Ingest one upload. ``audio_path`` is the caller's temp copy of the upload."""
    name = (name or "").strip()
    if not name:
        raise SampleError("name is required")
    if len(name) > NAME_MAX:
        raise SampleError(f"name must be at most {NAME_MAX} characters")

    kind, source_doc, alignment = _parse_source(json_bytes)
    chapter = resolve_pseudo_chapter(alignment)
    try:
        detailed, sidecar = alignment_to_detailed(alignment, pseudo_chapter=chapter)
    except SampleConvertError as exc:
        raise SampleError(str(exc)) from exc

    sample_id = uuid7()
    slug = storage_paths.sample_slug(sample_id)

    mp3_fd, mp3_name = tempfile.mkstemp(suffix=".mp3")
    os.close(mp3_fd)
    mp3_path = Path(mp3_name)
    try:
        audio_ingest.normalize_to_mp3(audio_path, mp3_path)
        probe = audio_ingest.probe(mp3_path)
    except audio_ingest.AudioIngestError as exc:
        mp3_path.unlink(missing_ok=True)
        raise SampleError(str(exc)) from exc

    backend = get_backend()
    mp3_bytes = mp3_path.read_bytes()
    backend.write_bytes_atomic(storage_paths.sample_source_path(sample_id), json_bytes)
    backend.write_json_atomic(storage_paths.sample_sidecar_path(sample_id), sidecar)
    data_dir.write_detailed_doc(slug, detailed)
    data_dir.write_segments_doc(
        slug, build_segments_doc(detailed["entries"], detailed["_meta"], with_repeated=True)
    )
    backend.write_json_atomic(
        storage_paths.audio_manifest_path(slug),
        _audio_manifest(sample_id, chapter, size_bytes=len(mp3_bytes), probe=probe),
    )
    backend.write_bytes_atomic(storage_paths.prefetched_audio_path(slug, chapter), mp3_bytes)

    with _sync.durable_transaction():
        repo_access.ensure_user(user.hf_user_id, login=user.login)
        repo_samples.create(
            sample_id=sample_id,
            owner_hf_user_id=user.hf_user_id,
            name=name,
            audio_filename=audio_filename,
            audio_duration_ms=probe["duration_ms"],
            source_schema=kind,
            pseudo_chapter=chapter,
        )

    _spawn(_finish_ingest, (sample_id, slug, chapter, mp3_path))
    return get_sample(sample_id, user)


def _spawn(target, args: tuple) -> None:
    """Run the peaks bake off the request thread (tests swap this for inline)."""
    threading.Thread(target=target, args=args, daemon=True).start()


def _finish_ingest(sample_id: str, slug: str, chapter: int, mp3_path: Path) -> None:
    try:
        audio_ingest.bake_peaks(mp3_path, slug, chapter)
        status, error = "ready", None
    except Exception as exc:  # noqa: BLE001 — surfaced on the row, never lost
        logger.exception("sample %s: peaks ingest failed", sample_id)
        status, error = "failed", str(exc)[:500]
    finally:
        mp3_path.unlink(missing_ok=True)
    with _sync.durable_transaction():
        repo_samples.set_status(sample_id, status, error=error)
    cache.pop_reciter_peaks_response_cache(slug)


def _can_manage(row: dict, user: Any) -> bool:
    return row["owner_hf_user_id"] == user.hf_user_id or permissions.is_owner(user)


def _last_edit_at(slug: str) -> str | None:
    last = None
    for batch in data_dir.iter_edit_history(slug):
        last = batch.get("saved_at_utc") or last
    return last


def _changed_since_export(row: dict) -> bool:
    last_save = row.get("last_save_at") or _last_edit_at(storage_paths.sample_slug(row["id"]))
    if not last_save:
        return False
    last_export = row.get("last_export_at")
    return last_export is None or last_save > last_export


def _row_view(row: dict, user: Any) -> dict:
    return {
        **row,
        "slug": storage_paths.sample_slug(row["id"]),
        "changed_since_export": _changed_since_export(row),
        "can_manage": _can_manage(row, user),
    }


def list_samples(user: Any) -> list[dict]:
    return [_row_view(row, user) for row in repo_samples.list_all()]


def get_sample(sample_id: str, user: Any) -> dict:
    row = repo_samples.get(sample_id)
    if row is None:
        raise SampleNotFound(sample_id)
    return _row_view(row, user)


def _require_manage(sample_id: str, user: Any) -> dict:
    row = repo_samples.get(sample_id)
    if row is None:
        raise SampleNotFound(sample_id)
    if not _can_manage(row, user):
        raise SampleForbidden(sample_id)
    return row


def rename_sample(sample_id: str, name: str, *, user: Any) -> dict:
    name = (name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise SampleError(f"name must be 1-{NAME_MAX} characters")
    _require_manage(sample_id, user)
    with _sync.durable_transaction():
        repo_samples.rename(sample_id, name)
    return get_sample(sample_id, user)


def delete_sample(sample_id: str, *, user: Any) -> None:
    _require_manage(sample_id, user)
    slug = storage_paths.sample_slug(sample_id)
    get_backend().delete(storage_paths.sample_dir(sample_id))
    with _sync.durable_transaction():
        repo_samples.delete(sample_id)
    cache.invalidate_seg_caches(slug)
    cache.pop_audio_manifest_cache(slug)
    cache.pop_reciter_peaks_response_cache(slug)


def export_sample(sample_id: str, *, user: Any) -> tuple[str, bytes]:
    """Return ``(filename, json_bytes)`` in the schema the sample was uploaded in."""
    row = repo_samples.get(sample_id)
    if row is None:
        raise SampleNotFound(sample_id)
    slug = storage_paths.sample_slug(sample_id)
    backend = get_backend()
    source_doc = json.loads(backend.read_bytes(storage_paths.sample_source_path(sample_id)))
    sidecar = backend.read_json(storage_paths.sample_sidecar_path(sample_id))
    if not isinstance(sidecar, dict):
        raise SampleError("sample sidecar is malformed")
    entries = load_detailed(slug)
    doc = detailed_to_alignment(entries, sidecar, source_doc)
    with _sync.durable_transaction():
        repo_samples.touch_last_export(sample_id)
    safe = "".join(ch for ch in row["name"] if ch.isalnum() or ch in "-_ ").strip() or "sample"
    return f"{safe}.alignment.json", orjson.dumps(doc, option=orjson.OPT_INDENT_2)
