"""Bucket-resident audio read primitives.

Used by:
- ``audio_source.resolve`` — picks bucket vs CDN path for the audio proxy.
- ``routes/segments/peaks.py`` — slim-envelope peaks reader.

No background worker. No CDN downloads. Bucket audio + slim peaks are written by
the align pipeline's HF jobs (``qua_jobs/acquire_audio.py``,
``qua_jobs/split_audio.py``); the inspector only reads them, and they persist
indefinitely.

No Flask imports — callable from any thread.
"""

from __future__ import annotations

import logging
import os

from services.storage import storage_paths
from services.storage.hf_bucket import get_backend

from . import audio_meta
from .peaks_slim import unpack_slim_envelope

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Per-content read-source gates (granular bucket-read control)
# ----------------------------------------------------------------------
# The bucket isn't the fastest source for every content type when running
# locally (no FUSE mount → hffs reads). These let a caller exclude a content
# type from the bucket so it uses a faster fallback instead. Default ON so
# deployed/hand-run behaviour is unchanged; `launch.py` flips them per mode.


def _audio_from_bucket(slug: str) -> bool:
    """Read chapter audio from the bucket? Off ⇒ ``resolve()`` skips the bucket
    (mount path + hffs full-MP3 read) and streams from the CDN — faster locally.
    The launcher sets this off for local ``dev``/``prod``; deployed defaults on.
    Samples exist only on the bucket, so the gate never applies to them."""
    if storage_paths.is_sample_slug(slug):
        return True
    return os.environ.get("INSPECTOR_AUDIO_FROM_BUCKET", "1") != "0"


def _peaks_from_bucket(slug: str) -> bool:
    """Read slim chapter peaks from the bucket? Off ⇒ every chapter misses and
    the FE falls through to per-segment ffmpeg compute (the manual peaks). On by
    default; the launcher exposes ``--ffmpeg-peaks`` to turn it off. Sample
    peaks exist only on the bucket, so the gate never applies to them."""
    if storage_paths.is_sample_slug(slug):
        return True
    return os.environ.get("INSPECTOR_PEAKS_FROM_BUCKET", "1") != "0"


# ----------------------------------------------------------------------
# Read paths (used by the audio-proxy + peaks routes)
# ----------------------------------------------------------------------


def read_prefetched_audio_bytes(slug: str, url: str) -> bytes | None:
    """Return the chapter MP3 bytes for ``url`` if present in the bucket.

    Resolves URL → chapter key through the catalog sidecar. Returns ``None``
    when either the URL isn't in this delivery's sidecar or the chapter file
    isn't on the bucket — the caller then falls back to a CDN redirect.
    """
    if not _audio_from_bucket(slug):
        return None
    chapter = audio_meta.chapter_for_url(slug, url)
    if chapter is None:
        return None
    path = storage_paths.prefetched_audio_path(slug, chapter)
    backend = get_backend()
    try:
        return backend.read_bytes(path)
    except Exception:  # noqa: BLE001
        return None


def read_prefetched_audio_local_path(slug: str, url: str):
    """Return a real local ``Path`` to the chapter file when the backend can
    expose one (deployed bucket mount or local-disk backend).

    Lets the audio-proxy hand ``send_file`` a path instead of a BytesIO —
    Werkzeug then handles Range/304 via OS sendfile and we avoid pulling the
    whole 4–5 MB MP3 into Flask memory on every surah switch. Returns
    ``None`` for local-dev no-mount (callers fall back to bytes or CDN).
    """
    if not _audio_from_bucket(slug):
        return None
    chapter = audio_meta.chapter_for_url(slug, url)
    if chapter is None:
        return None
    path = storage_paths.prefetched_audio_path(slug, chapter)
    try:
        return get_backend().local_path(path)
    except Exception:  # noqa: BLE001
        return None


def read_prefetched_peaks(slug: str, url: str) -> dict | None:
    """Return the chapter-overview peaks for ``url`` in the slim int8 envelope.

    Reads ``reciters/<slug>/peaks/<chapter>.json.gz`` and returns the
    packed envelope verbatim: ``{schema_version:3, duration_ms, q:'int8',
    bps, n, peaks_b64}``. No dequant, no ``.tolist()`` — the FE-facing route
    sends this through unchanged and the browser inflates b64 → ``Int8Array``
    once on receive (see ``the inspector-audio skill``).

    Returns ``None`` for:
    - Unknown URL (not in the catalog manifest).
    - Missing file (extraction didn't bake peaks for that chapter, or backfill
      not yet run).
    - Pre-v3 file or corrupt blob — caller treats this as a cache miss and
      falls back to ``/segment-peaks`` per-card.

    Companion ``unpack_slim`` (returns dequantized float-list) exists in
    ``peaks_slim.py`` for the offline extraction history-JSONL writer.
    Runtime inspector code uses only this envelope reader.
    """
    if not _peaks_from_bucket(slug):
        return None
    chapter = audio_meta.chapter_for_url(slug, url)
    if chapter is None:
        return None
    path = storage_paths.prefetched_peaks_path(slug, chapter)
    backend = get_backend()
    try:
        blob = backend.read_bytes(path)
    except Exception:  # noqa: BLE001
        return None
    return unpack_slim_envelope(blob)


def read_prefetched_peaks_duration_ms(slug: str, url: str) -> int | None:
    """Return just the chapter ``duration_ms`` from the slim peaks header.

    Fallback length source for the dashboard player when the audio_manifest
    sidecar has a null/missing ``duration_sec`` (e.g. a reciter probed before
    the manifest carried durations). Reads the slim envelope header — the same
    ``duration_ms`` baked alongside the peaks at extraction time — without
    decoding the int8 payload. Returns ``None`` when peaks are also absent,
    the URL is unknown, or the blob is corrupt.
    """
    env = read_prefetched_peaks(slug, url)
    if env is None:
        return None
    dur = env.get("duration_ms")
    return int(dur) if isinstance(dur, (int, float)) and dur > 0 else None
