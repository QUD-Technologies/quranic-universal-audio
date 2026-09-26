"""Bucket file access for HF Jobs that read AND write the bucket (split_audio).

Not an entrypoint. With ``BUCKET_REPO`` set, files move over the Hub HTTP API
(``download_bucket_files`` / ``batch_bucket_files``): a job reading large files
off its bucket-volume mount while writing to it saw reads hang and fail
mid-file (ffmpeg then wrote 2.7 s "chapters" with exit 0, 2026-09-26).
Without it — tests, a local run — paths resolve under ``INSPECTOR_BUCKET_MOUNT``.
Paths are bucket-relative (``reciters/<slug>/audio/1.mp3``).
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

ATTEMPTS = 4
RETRY_SLEEP_S = 5


class MissingFile(FileNotFoundError):
    """The bucket has no file at that path."""


def _repo() -> str:
    return (os.environ.get("BUCKET_REPO") or "").strip()


def _root() -> Path:
    return Path(os.environ.get("INSPECTOR_BUCKET_MOUNT", "/data"))


def _retry(what: str, fn):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fn()
        except MissingFile:
            raise
        except Exception:  # noqa: BLE001 — Hub/network errors are retried, then raised
            if attempt == ATTEMPTS:
                raise
            time.sleep(RETRY_SLEEP_S * attempt)
    raise AssertionError(what)


def fetch(rel: str, dest: Path) -> Path:
    """Copy bucket file ``rel`` to local ``dest``; raises ``MissingFile``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    repo = _repo()
    if not repo:
        src = _root() / rel
        if not src.is_file():
            raise MissingFile(rel)
        shutil.copyfile(src, dest)
        return dest

    def get() -> Path:
        from huggingface_hub import download_bucket_files, get_bucket_paths_info

        info = list(get_bucket_paths_info(repo, [rel]))
        if not info:
            raise MissingFile(rel)
        download_bucket_files(repo, files=[(rel, str(dest))], raise_on_missing_files=True)
        size = getattr(info[0], "size", None)
        if size is not None and dest.stat().st_size != size:
            raise OSError(f"{rel}: got {dest.stat().st_size} of {size} bytes")
        return dest

    return _retry(f"fetch {rel}", get)


def put(local: Path, rel: str) -> None:
    repo = _repo()
    if not repo:
        dest = _root() / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(local, tmp)
        os.replace(tmp, dest)
        return

    def up() -> None:
        from huggingface_hub import batch_bucket_files

        batch_bucket_files(repo, add=[(str(local), rel)])

    _retry(f"put {rel}", up)


def put_bytes(data: bytes, rel: str, scratch: Path) -> None:
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_bytes(data)
    put(scratch, rel)


def delete(rels: list[str]) -> None:
    if not rels:
        return
    repo = _repo()
    if not repo:
        for rel in rels:
            (_root() / rel).unlink(missing_ok=True)
        return

    def rm() -> None:
        from huggingface_hub import batch_bucket_files

        batch_bucket_files(repo, delete=rels)

    _retry("delete", rm)
