"""Whether files the Inspector wrote through the bucket mount have reached the bucket yet.

The deployed Inspector writes through a mount that flushes to the bucket after a delay; a
reader on another host (the aligner) sees the bucket. :func:`unflushed` names the files the
mount holds newer than the bucket, so a caller can wait instead of handing out stale paths.
"""

from __future__ import annotations

from services.storage.hf_bucket import BucketBackend, get_backend, resolve_bucket_repo


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
