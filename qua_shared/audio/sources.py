"""Which physical files a delivery's chapters come from — shared by the align
pipeline (Inspector) and its acquire/split HF Jobs.

A chapter's *source* is its manifest ``source_url`` when set (a combined file:
one Drive mp3 / YouTube video holding several chapters), else its ``url``.
Chapters sharing a source form one group. A manifest ``sources`` entry (a
playlist file whose surahs are not known yet) is a *detect* group: no chapters,
the aligner decides what it holds.

Combined and detect groups are acquired once into a *source slot* —
``reciters/<slug>/audio/<slot>.mp3`` with ``slot = 201 + i`` — above any chapter
number, so it never collides with one and the aligner's bucket-reference pattern
(1–3 digits) admits it unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

DIRECT_AUDIO_EXTS = frozenset({".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac"})
_DRIVE_FILE_RE = re.compile(r"drive\.google\.com.*?(?:/file/d/|[?&]id=)([A-Za-z0-9_\-]{10,})")

SLOT_BASE = 201
MAX_SLOT = 999


@dataclass(frozen=True)
class SourceGroup:
    url: str
    #: Planned chapters; empty for a detect group.
    chapters: tuple[int, ...]
    #: ``None`` for a single-chapter group; the bucket slot for a combined one.
    slot: int | None = None

    @property
    def combined(self) -> bool:
        """Acquired into a slot and split after aligning (combined or detect)."""
        return self.slot is not None

    @property
    def weight(self) -> int:
        """Progress units: its chapters, or 1 for a detect group."""
        return max(1, len(self.chapters))

    @property
    def detect(self) -> bool:
        return self.slot is not None and not self.chapters

    @property
    def item(self) -> int:
        """The aligner item / audio file number this group is aligned under."""
        return self.slot if self.slot is not None else self.chapters[0]


def groups_from_manifest(
    chapters: dict[str, dict], sources: list[dict] | None = None
) -> list[SourceGroup]:
    """Group manifest entries (``{"<ch>": {url, source_url, …}}``) by source, then
    one detect group per ``sources`` entry not already a chapter's source."""
    by_source: dict[str, list[int]] = {}
    for key, entry in chapters.items():
        if ":" in str(key):
            raise ValueError("by_ayah manifests are not supported by the align pipeline")
        source = entry.get("source_url") or entry["url"]
        by_source.setdefault(source, []).append(int(key))
    groups: list[SourceGroup] = []
    slot = SLOT_BASE
    for url, chs in sorted(by_source.items(), key=lambda kv: min(kv[1])):
        chs.sort()
        if len(chs) == 1:
            groups.append(SourceGroup(url=url, chapters=(chs[0],)))
            continue
        if slot > MAX_SLOT:
            raise ValueError("too many combined source files")
        groups.append(SourceGroup(url=url, chapters=tuple(chs), slot=slot))
        slot += 1
    seen = set(by_source)
    for src in sources or []:
        url = src["url"]
        if url in seen:
            continue
        seen.add(url)
        if slot > MAX_SLOT:
            raise ValueError(f"too many source files (at most {MAX_SLOT - SLOT_BASE + 1})")
        groups.append(SourceGroup(url=url, chapters=(), slot=slot))
        slot += 1
    return groups


def drive_file_id(url: str) -> str | None:
    """The file id of a Google Drive file link (``/file/d/<id>`` or ``?id=``)."""
    m = _DRIVE_FILE_RE.search(url)
    return m.group(1) if m else None


def needs_ytdlp(url: str) -> bool:
    """A source only yt-dlp can fetch (a watch page, a SoundCloud track, …) —
    neither a Drive file nor a direct media URL."""
    if drive_file_id(url):
        return False
    return PurePosixPath(url.split("?")[0]).suffix.lower() not in DIRECT_AUDIO_EXTS
