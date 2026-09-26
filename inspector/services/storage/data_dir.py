"""Per-reciter data directory resolver.

Wraps the storage backend with semantic helpers — callers pass a slug
and get back the bytes/dict they need. Routes/services use this instead
of touching ``RECITATION_SEGMENTS_PATH`` (legacy filesystem constant, kept
only for tests on ``FilesystemBackend``).

All per-reciter content lives under a single ``reciters/<slug>/`` prefix;
lifecycle state is a DB attribute, not a folder choice, so these helpers
need no notion of ``wip`` vs ``published``.

"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator

import brotli

from . import storage_paths
from .hf_bucket import StorageNotFound, get_backend

# ---------------------------------------------------------------------------
# Read-only TS-shard bucket override (dev convenience)
# ---------------------------------------------------------------------------
#
# The dev bucket carries state/catalog but not the heavy per-chapter timestamp
# shards (those live in prod). For local prototyping against real recitations,
# ``INSPECTOR_TS_READ_BUCKET`` redirects *timestamp-shard reads only* to a named
# bucket, read through the HF API + token — leaving the default backend (state,
# DB, all writes) on its own bucket so prod state is never touched.
#
# READ-ONLY by construction: this backend is only ever handed to ``read_bytes``.
# It deliberately bypasses ``resolve_bucket_repo``'s prod refusal (which guards
# the DB full-file sync, irrelevant to a pure read). Never route writes here.
_ts_read_backend = None
_ts_read_lock = threading.Lock()


def _ts_read_backend_or_default():
    repo = (os.environ.get("INSPECTOR_TS_READ_BUCKET") or "").strip()
    if not repo:
        return get_backend()
    global _ts_read_backend
    if _ts_read_backend is None:
        with _ts_read_lock:
            if _ts_read_backend is None:
                from .hf_bucket import BucketBackend

                _ts_read_backend = BucketBackend(bucket_id=repo)
    return _ts_read_backend


# ---- path helpers (no I/O) ----


def list_slugs() -> list[str]:
    """Return all reciter slug subdirectories under ``reciters/``."""
    return get_backend().list_dir(storage_paths.RECITERS_PREFIX)


def reciter_file_path(slug: str, name: str) -> str:
    return storage_paths.reciter_file(slug, name)


def segments_path(slug: str) -> str:
    return storage_paths.segments_path(slug)


def detailed_path(slug: str) -> str:
    return storage_paths.detailed_path(slug)


def edit_history_path(slug: str) -> str:
    return storage_paths.edit_history_path(slug)


def edit_history_peaks_path(slug: str) -> str:
    return storage_paths.edit_history_peaks_path(slug)


def low_confidence_path(slug: str) -> str:
    return storage_paths.low_confidence_path(slug)


def ts_validation_path(slug: str) -> str:
    return storage_paths.ts_validation_path(slug)


def auto_split_path(slug: str) -> str:
    return storage_paths.auto_split_path(slug)


def hidden_pause_path(slug: str) -> str:
    return storage_paths.hidden_pause_path(slug)


def missed_waqf_path(slug: str) -> str:
    return storage_paths.missed_waqf_path(slug)


def false_split_path(slug: str) -> str:
    return storage_paths.false_split_path(slug)


def unmarked_wasl_path(slug: str) -> str:
    return storage_paths.unmarked_wasl_path(slug)


def wasl_recheck_path(slug: str) -> str:
    return storage_paths.wasl_recheck_path(slug)


def pipeline_meta_path(slug: str) -> str:
    return storage_paths.pipeline_meta_path(slug)


def read_pipeline_meta_doc(slug: str) -> dict | None:
    """Return the parsed ``pipeline_meta.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(pipeline_meta_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def write_pipeline_meta_doc(slug: str, doc: dict) -> None:
    get_backend().write_json_atomic(pipeline_meta_path(slug), doc)


def has_reciter(slug: str) -> bool:
    """True if the reciter directory exists under ``reciters/``."""
    return get_backend().exists(storage_paths.reciter_dir(slug))


# ---- high-level read/write helpers (these hide the backend entirely) ----


def read_segments_doc(slug: str) -> dict | None:
    """Return the parsed ``segments.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(segments_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def read_detailed_bytes(slug: str) -> bytes | None:
    """Return raw ``detailed.json`` bytes, or ``None`` if absent."""
    try:
        return get_backend().read_bytes(detailed_path(slug))
    except StorageNotFound:
        return None


def read_low_confidence_doc(slug: str) -> dict | None:
    try:
        return get_backend().read_json(low_confidence_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def read_ts_validation_doc(slug: str) -> dict | None:
    """Return the parsed ``ts_validation.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(ts_validation_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def read_auto_split_doc(slug: str) -> dict | None:
    """Return the parsed ``auto_split_v1.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(auto_split_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def read_hidden_pause_doc(slug: str) -> dict | None:
    """Return the parsed ``hidden_pause_v1.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(hidden_pause_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def read_missed_waqf_doc(slug: str) -> dict | None:
    """Return the parsed ``missed_waqf_v1.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(missed_waqf_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def read_false_split_doc(slug: str) -> dict | None:
    """Return the parsed ``false_split_v1.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(false_split_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def read_unmarked_wasl_doc(slug: str) -> dict | None:
    """Return the parsed ``unmarked_wasl_v1.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(unmarked_wasl_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def read_wasl_recheck_doc(slug: str) -> dict | None:
    """Return the parsed ``wasl_recheck_v1.json`` doc, or ``None`` if absent."""
    try:
        return get_backend().read_json(wasl_recheck_path(slug))  # type: ignore[return-value]
    except StorageNotFound:
        return None


def write_detailed_doc(slug: str, doc: dict) -> None:
    get_backend().write_json_atomic(detailed_path(slug), doc)


def write_segments_doc(slug: str, doc: dict) -> None:
    get_backend().write_json_atomic(segments_path(slug), doc)


def append_edit_history(slug: str, batch: dict) -> None:
    get_backend().append_jsonl(edit_history_path(slug), batch)


def append_peaks_history(slug: str, record: dict) -> None:
    get_backend().append_jsonl(edit_history_peaks_path(slug), record)


def iter_edit_history(slug: str) -> Iterator[dict]:
    yield from get_backend().iter_jsonl(edit_history_path(slug))


def iter_peaks_history(slug: str) -> Iterator[dict]:
    yield from get_backend().iter_jsonl(edit_history_peaks_path(slug))


# ---- timestamps (released reciters only — others have no TS yet) ----


def read_timestamps_chapter_br(slug: str, chapter: int) -> bytes | None:
    """Return the raw Brotli timestamps-shard bytes for a chapter, or ``None``.

    Reads ``timestamps/<chapter>.json.br`` and returns it uninflated for the
    shard route's byte pass-through response.
    """
    backend = _ts_read_backend_or_default()
    try:
        return backend.read_bytes(storage_paths.timestamps_path_br(slug, chapter))
    except StorageNotFound:
        return None


def read_timestamps_chapter(slug: str, chapter: int) -> bytes | None:
    """Return decompressed timestamps-shard JSON bytes for a chapter, or ``None``.

    Reads the Brotli shard ``timestamps/<chapter>.json.br`` and inflates it;
    the caller receives raw JSON bytes.
    """
    compressed = read_timestamps_chapter_br(slug, chapter)
    return brotli.decompress(compressed) if compressed is not None else None
