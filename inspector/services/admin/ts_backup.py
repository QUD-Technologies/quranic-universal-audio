"""Pre-publish backup of a delivery's timestamps before a timestamps run writes over them.

:func:`backup` copies the delivery's live ``timestamps/`` and ``timing/`` trees and its
``recitation_profile.json`` server-side to
``backups/timestamps-pre-online-<UTC>/reciters/<slug>/`` (the batch re-time's
``timestamps-pre-neural`` scheme, online variant), skipping what the delivery does not have yet.
On the bucket a tree goes through ``huggingface_hub.copy_files`` with a trailing slash on the
source, which copies its contents instead of nesting the folder; the copy is then listed back.

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
            live = storage_paths.reciter_file(slug, name)
            names = _files(backend, live)
            if not names:
                continue
            _copy_tree(backend, live, f"{root}/{name}")
            missing = names - _files(backend, f"{root}/{name}")
            if missing:
                raise BackupError(f"{name}: {len(missing)} file(s) missing from the backup")
            copied.append(name)
        for name in FILES:
            try:
                backend.copy(storage_paths.reciter_file(slug, name), f"{root}/{name}")
            except StorageNotFound:
                continue
            copied.append(name)
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(f"{type(exc).__name__}: {exc}") from exc
    return root, copied


def _files(backend, path: str) -> set[str]:
    """The file names under ``path``; on the bucket as its API lists them, not the mount."""
    if isinstance(backend, BucketBackend):
        from huggingface_hub import list_bucket_tree

        prefix = f"{path}/"
        items = list_bucket_tree(resolve_bucket_repo(), prefix=prefix, recursive=True)
        return {
            item.path[len(prefix) :]
            for item in items
            if item.type == "file" and item.path.startswith(prefix)
        }
    return set(backend.list_dir_strict(path))


def _copy_tree(backend, src: str, dst: str) -> None:
    if isinstance(backend, BucketBackend):
        from huggingface_hub import copy_files

        bucket = f"hf://buckets/{resolve_bucket_repo()}"
        copy_files(f"{bucket}/{src}/", f"{bucket}/{dst}")
    else:
        backend.copy(src, dst)
