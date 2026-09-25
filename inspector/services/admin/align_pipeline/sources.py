"""Which physical files a delivery's chapters come from.

The audio manifest is the pipeline's only input. A chapter's *source* is its
``source_url`` when set (a combined file: one Drive mp3 / YouTube video holding
several chapters), else its ``url``. Chapters sharing a source form one group:

* a group of one is aligned as that chapter (``reciters/<slug>/audio/<ch>.mp3``);
* a combined group is acquired ONCE into a *source slot*
  (``reciters/<slug>/audio/<slot>.mp3``, ``slot = 201 + i``), aligned once, then
  split into per-chapter files by ``stage_split``. Slots sit above 114 so they
  never collide with a chapter; the aligner's bucket-reference pattern admits
  them unchanged, and split deletes them;
* a manifest ``sources`` entry (a playlist file) is a *detect* group: a slot with
  no planned chapters — the aligner's surah detection decides what it holds.
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
    """A chapter already cut out of a combined file (its ``url`` is its own
    bucket mp3) is aligned from that mp3 like any single file."""
    chapters = {
        key: (
            {**entry, "source_url": None}
            if entry.get("url") == bucket_chapter_url(slug, int(key))
            else entry
        )
        for key, entry in audio_meta.manifest_chapters(slug).items()
    }
    return groups_from_manifest(chapters, audio_meta.manifest_sources(slug))


def chapter_sources(slug: str) -> dict[int, str]:
    """``{chapter: source url}`` — the ``chapter_sources.json`` url per chapter,
    from the (post-split) manifest: a cut chapter's original file."""
    return {
        int(key): entry.get("source_url") or entry["url"]
        for key, entry in audio_meta.manifest_chapters(slug).items()
    }
