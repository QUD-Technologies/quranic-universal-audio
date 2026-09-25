"""Which file each surah is taken from — pure, fed by ``stage_split``.

Every slot file (a combined file the plan labelled, or a playlist file nobody
labelled) has been aligned and cut by ``partition.cut_file``. Surahs are then
assigned:

1. A single-chapter file keeps its chapter (``fixed``).
2. A combined file keeps the chapters the manifest planned for it, when the
   aligner found them there.
3. Every other surah goes to the file holding most of its recitation
   (``matched_ms``). A further file that adds new ayahs of the same surah — a
   long surah uploaded in parts — is stitched on as another *piece*, in ayah
   order; one that only repeats covered ayahs (a re-upload) is ignored.

A planned chapter nobody provides is dropped; a surah nobody planned is adopted.
A cut holding a lot of unmatched recitation next to a surah missing from the
middle of the delivery is flagged: the aligner likely failed to place that surah
(a short surah just before another) and its audio sits inside this cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .partition import ChapterCut, ayahs, matched_ms, unmatched_ms

#: A further file joins a surah as a piece only if this share of its ayahs is new.
PIECE_MIN_NEW_SHARE = 0.8
SUSPECT_MIN_MS = 3000


@dataclass
class FileCuts:
    item: int
    url: str
    planned: tuple[int, ...]
    cuts: dict[int, ChapterCut]


@dataclass
class Piece:
    file: FileCuts
    cut: ChapterCut


@dataclass
class Resolution:
    #: Chapter → its pieces in playback order (one piece in the usual case).
    chapters: dict[int, list[Piece]] = field(default_factory=dict)
    dropped: list[int] = field(default_factory=list)
    adopted: list[int] = field(default_factory=list)
    #: Slot → surahs found there but taken from another file.
    ignored: dict[int, list[int]] = field(default_factory=dict)
    #: URLs of files the aligner found no recitation in.
    empty: list[str] = field(default_factory=list)
    suspect: dict[int, str] = field(default_factory=dict)


def resolve(files: list[FileCuts], fixed: set[int]) -> Resolution:
    out = Resolution()
    planned = {ch for f in files for ch in f.planned}
    for f in files:
        for ch in f.planned:
            if ch in f.cuts and ch not in fixed:
                out.chapters[ch] = [Piece(f, f.cuts[ch])]
    candidates: dict[int, list[Piece]] = {}
    for f in files:
        for ch, cut in f.cuts.items():
            if ch not in fixed and ch not in out.chapters:
                candidates.setdefault(ch, []).append(Piece(f, cut))
    for ch, pieces in candidates.items():
        out.chapters[ch] = _stitch(pieces)
    out.chapters = dict(sorted(out.chapters.items()))
    out.dropped = sorted(ch for ch in planned if ch not in out.chapters)
    out.adopted = sorted(ch for ch in out.chapters if ch not in planned)
    for f in files:
        if not f.cuts:
            out.empty.append(f.url)
            continue
        used = {ch for ch, ps in out.chapters.items() if any(p.file is f for p in ps)}
        unused = sorted(set(f.cuts) - used)
        if unused:
            out.ignored[f.item] = unused
    out.suspect = _suspects(out.chapters, fixed)
    return out


def _stitch(pieces: list[Piece]) -> list[Piece]:
    ranked = sorted(pieces, key=lambda p: matched_ms(p.cut.rows), reverse=True)
    chosen = [ranked[0]]
    covered = ayahs(ranked[0].cut.rows)
    for piece in ranked[1:]:
        own = ayahs(piece.cut.rows)
        new = own - covered
        if own and len(new) >= PIECE_MIN_NEW_SHARE * len(own):
            chosen.append(piece)
            covered |= own
    return sorted(chosen, key=lambda p: min(ayahs(p.cut.rows), default=0))


def _suspects(chapters: dict[int, list[Piece]], fixed: set[int]) -> dict[int, str]:
    present = set(chapters) | fixed
    if not present:
        return {}
    lo, hi = min(present), max(present)
    out: dict[int, str] = {}
    for ch, pieces in chapters.items():
        loose = sum(unmatched_ms(p.cut.rows) for p in pieces)
        gaps = [n for n in (ch - 1, ch + 1) if lo < n < hi and n not in present]
        if loose >= SUSPECT_MIN_MS and gaps:
            out[ch] = (
                f"holds {loose / 1000:.0f}s of unmatched recitation; chapter "
                f"{gaps[0]} was not found — it may be inside this cut"
            )
    return out
