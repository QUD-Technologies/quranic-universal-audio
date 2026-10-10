"""Pre-publish backup of a delivery's timestamps before a timestamps run writes over them.

:func:`backup` copies the delivery's live ``timestamps/`` and ``timing/`` trees and its
``recitation_profile.json`` to ``backups/timestamps-pre-online-<UTC>/reciters/<slug>/`` (the
batch re-time's ``timestamps-pre-neural`` scheme, online variant), skipping what the delivery
does not have yet. On the bucket a tree is copied server-side with ``huggingface_hub.copy_files``
and a trailing slash on the source, which copies its contents instead of nesting the folder; a
file the mount holds newer than the bucket (written, not flushed yet) is then uploaded from the
mount over its copy, and the backup is listed back against the live tree. The profile is read
and written whole.

Rollback: copy each backed-up tree's contents back over the live one with the same trailing
slash, e.g. ``copy_files("hf://buckets/<bucket>/backups/timestamps-pre-online-<UTC>/reciters/
<slug>/timestamps/", "hf://buckets/<bucket>/reciters/<slug>/timestamps")`` (and ``timing``,
``recitation_profile.json``), then POST ``/api/admin/internal/ts-refreshed`` for the slug so the
Inspector drops its cached profile and rebuilds the readings summary. Shards a run added for a
chapter the backup lacks stay until deleted.
"""

from __future__ import annotations

import datetime

from services.storage import storage_paths
from services.storage.hf_bucket import (
    BucketBackend,
    StorageNotFound,
    get_backend,
    resolve_bucket_repo,
)

PREFIX = "backups/timestamps-pre-online"
TREES = ("timestamps", "timing")
FILES = ("recitation_profile.json",)


class BackupError(RuntimeError):
    """The live timestamps could not be backed up; nothing may be written over them."""


def backup(slug: str) -> tuple[str, list[str]]:
    """Back up ``slug``'s live timestamps; returns the backup folder and what it holds."""
    backend = get_backend()
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    root = f"{PREFIX}-{stamp}/reciters/{slug}"
    copied = []
    try:
        for name in TREES:
            if _copy_tree(backend, storage_paths.reciter_file(slug, name), f"{root}/{name}"):
                copied.append(name)
        for name in FILES:
            try:
                data = backend.read_bytes(storage_paths.reciter_file(slug, name))
            except StorageNotFound:
                continue
            _put(backend, f"{root}/{name}", data)
            copied.append(name)
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(f"{type(exc).__name__}: {exc}") from exc
    return root, copied


def _copy_tree(backend, src: str, dst: str) -> bool:
    """Copy ``src``'s files into ``dst``; False when ``src`` has none."""
    if not isinstance(backend, BucketBackend):
        if not backend.list_dir_strict(src):
            return False
        backend.copy(src, dst)
        return True
    remote = _remote(src)
    local = {n for n in backend.list_dir_strict(src) if ".tmp." not in n}
    if not remote and not local:
        return False
    if remote:
        from huggingface_hub import copy_files

        bucket = f"hf://buckets/{resolve_bucket_repo()}"
        copy_files(f"{bucket}/{src}/", f"{bucket}/{dst}")
    for name in sorted(local):
        if _unflushed(backend.local_path(f"{src}/{name}"), remote.get(name)):
            _put(backend, f"{dst}/{name}", backend.read_bytes(f"{src}/{name}"))
    missing = (set(remote) | local) - set(_remote(dst))
    if missing:
        raise BackupError(f"{src}: {len(missing)} file(s) missing from the backup")
    return True


def unflushed(path: str, names: list[str]) -> list[str]:
    """Which of ``names`` under ``path`` the mount holds newer than the bucket (none off the
    bucket backend, where every write is the bucket's)."""
    backend = get_backend()
    if not isinstance(backend, BucketBackend):
        return []
    remote = _remote(path)
    return [n for n in names if _unflushed(backend.local_path(f"{path}/{n}"), remote.get(n))]


def _remote(path: str) -> dict:
    """``{name: BucketFile}`` of the files under ``path`` as the bucket API lists them."""
    from huggingface_hub import list_bucket_tree

    prefix = f"{path}/"
    items = list_bucket_tree(resolve_bucket_repo(), prefix=prefix, recursive=True)
    return {
        item.path[len(prefix) :]: item
        for item in items
        if item.type == "file" and item.path.startswith(prefix)
    }


def _unflushed(local, remote) -> bool:
    """Whether the mount's copy of a file is newer than the bucket's (or the bucket lacks it)."""
    if local is None:
        return False
    if remote is None:
        return True
    stamp = remote.mtime or remote.uploaded_at
    stat = local.stat()
    return stat.st_size != remote.size or stamp is None or stat.st_mtime > stamp.timestamp()


def _put(backend, path: str, data: bytes) -> None:
    """Write to the bucket itself, past the mount's debounced flush."""
    if isinstance(backend, BucketBackend):
        backend.write_bytes_direct(path, data)
    else:
        backend.write_bytes_atomic(path, data)
