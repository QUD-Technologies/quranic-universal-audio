"""Recitation profile: a Hafs delivery's madd lengths, ghunnah and pauses.

``doc(slug)`` reads ``reciters/<slug>/recitation_profile.json`` (published by the batch
re-time beside the shards) and serves its public projection, ``TsRecitationProfile``:
the madd types with occurrences in display order, a chosen length only where Hafs allows
one, and no counts or shares. A missing, unreadable or non-Hafs profile is ``None``. The
result (``None`` included) is cached per delivery with the file's modified time on the mounted
bucket, so a rewritten profile is read again on the next request; without a mount it is kept
until ``drop(slug)``, which the ``ts-refreshed`` notice calls when new shards land.
"""

from __future__ import annotations

import logging
import threading

from pydantic import ValidationError

from qua_shared.schemas import RecitationProfileDoc, TsMaddRow, TsRecitationProfile
from services.reference.readings import is_hafs
from services.storage import storage_paths
from services.storage.hf_bucket import StorageError, StorageNotFound, get_backend

log = logging.getLogger("inspector")

MADD_ORDER = ("tabii", "munfasil", "muttasil", "lazim", "arid", "leen")
WITH_LENGTH = frozenset({"munfasil", "arid", "leen"})

#: slug -> (the file's version when read, its profile)
_cache: dict[str, tuple[object, TsRecitationProfile | None]] = {}
#: The version of a profile file absent from a mounted bucket.
_ABSENT = "absent"
_lock = threading.Lock()


def project(stored: RecitationProfileDoc) -> TsRecitationProfile:
    """The public projection of a stored profile."""
    rows = []
    for kind in MADD_ORDER:
        stats = getattr(stored.madd, kind)
        if stats.mean_ms is None:
            continue
        length = stats.verdict if kind in WITH_LENGTH else None
        rows.append(TsMaddRow(kind=kind, mean_ms=stats.mean_ms, length=length))
    return TsRecitationProfile(
        madd=rows, ghunnah_ms=stored.ghunnah.mean_ms, pause_ms=stored.silence.mean_ms
    )


def _read(slug: str) -> TsRecitationProfile | None:
    if not is_hafs(slug):
        return None
    try:
        raw = get_backend().read_json(storage_paths.recitation_profile_path(slug))
    except StorageNotFound:
        return None
    try:
        return project(RecitationProfileDoc.model_validate(raw))
    except ValidationError as exc:
        log.warning("recitation profile: %s is invalid: %s", slug, exc)
        return None


def _version(slug: str) -> object:
    """The profile file's modified time on the mounted bucket (:data:`_ABSENT` when it is not
    there), or ``None`` without a mount."""
    local_path = getattr(get_backend(), "local_path", None)
    if local_path is None:
        return None
    try:
        path = local_path(storage_paths.recitation_profile_path(slug))
        if path is not None:
            return path.stat().st_mtime_ns
        return _ABSENT if local_path(storage_paths.detailed_path(slug)) is not None else None
    except OSError:
        return None


def doc(slug: str) -> TsRecitationProfile | None:
    """The delivery's public profile, or ``None`` when it has none."""
    version = _version(slug)
    with _lock:
        cached = _cache.get(slug)
    if cached is not None and (version is None or cached[0] == version):
        return cached[1]
    try:
        profile = _read(slug)
    except StorageError as exc:
        log.warning("recitation profile: %s not read: %s", slug, exc)
        return None
    with _lock:
        _cache[slug] = (version, profile)
    return profile


def drop(slug: str) -> None:
    with _lock:
        _cache.pop(slug, None)


def invalidate() -> None:
    with _lock:
        _cache.clear()
