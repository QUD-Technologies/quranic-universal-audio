"""Which physical files a delivery's chapters come from.

The audio manifest is the pipeline's only input. A chapter's *source* is its
``source_url`` when set (a combined file: one Drive mp3 / YouTube video holding
several chapters), else its ``url``. Chapters sharing a source form one group:

* a group of one is aligned as that chapter (``reciters/<slug>/audio/<ch>.mp3``);
* a combined group is acquired ONCE into a *source slot*
  (``reciters/<slug>/audio/<slot>.mp3``, ``slot = 901 + i``), aligned once, then
  split into per-chapter files by ``stage_split``. Slots sit above 114 so they
  never collide with a chapter; the aligner's bucket-reference pattern admits
  them unchanged, and split deletes them.
"""

from __future__ import annotations

from qua_shared.audio.sources import (
    SLOT_BASE,
    SourceGroup,
    groups_from_manifest,
)
from services.audio import audio_meta
from services.storage.hf_bucket import resolve_bucket_repo

__all__ = ["SLOT_BASE", "SourceGroup", "bucket_chapter_url", "chapter_sources", "groups_for"]


def bucket_chapter_url(slug: str, chapter: int) -> str:
    """Browser URL of a persisted chapter mp3 — the unique manifest ``url`` a
    combined file's chapters get (their ``source_url`` keeps the original)."""
    return (
        f"https://huggingface.co/buckets/{resolve_bucket_repo()}/resolve/"
        f"reciters/{slug}/audio/{chapter}.mp3"
    )


def groups_for(slug: str) -> list[SourceGroup]:
    manifest = audio_meta.manifest_chapters(slug)
    if not manifest:
        return []
    return groups_from_manifest(manifest)


def chapter_sources(groups: list[SourceGroup]) -> dict[int, str]:
    """``{chapter: source url}`` — the ``chapter_sources.json`` url per chapter."""
    return {ch: g.url for g in groups for ch in g.chapters}
