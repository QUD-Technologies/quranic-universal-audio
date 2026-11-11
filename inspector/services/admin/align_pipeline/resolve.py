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

A surah nobody planned is adopted only if its pieces cover at least
``MIN_SURAH_COVERAGE`` of its ayahs — an intro montage or a trailer holds short
excerpts of many surahs, and an excerpt is not a chapter (reported as a
*fragment*). A planned chapter nobody provides is dropped.
A cut holding a lot of unmatched recitation next to a surah missing from the
middle of the delivery is flagged: the aligner likely failed to place that surah
(a short surah just before another) and its audio sits inside this cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .partition import ChapterCut, ayahs, matched_ms, unmatched_ms

#: A further file joins a surah as a piece only if this share of its ayahs is new.
PIECE_MIN_NEW_SHARE = 0.8
#: Share of a surah's ayahs an unplanned chapter must cover to be adopted.
MIN_SURAH_COVERAGE = 0.5
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
    #: Unplanned surahs found only as excerpts → why they were not adopted.
    fragments: dict[int, str] = field(default_factory=dict)
    suspect: dict[int, str] = field(default_factory=dict)
    #: File URL → {surah it holds: URL of the file the surah was taken from}
    #: (a re-upload, or a duplicate uploaded under another surah's name).
    repeats: dict[str, dict[int, str]] = field(default_factory=dict)
    #: Surahs inside the delivery's span (first to last surah found) that no
    #: file provides and nobody planned — a playlist with a hole in it.
    gaps: list[int] = field(default_factory=list)


def resolve(
    files: list[FileCuts], fixed: set[int], ayah_counts: dict[int, int] | None = None
) -> Resolution:
    """``ayah_counts``: surah → number of ayahs; without it no coverage guard."""
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
        chosen = _stitch(pieces)
        short = _too_short(ch, chosen, ayah_counts) if ch not in planned else None
        if short:
            out.fragments[ch] = short
        else:
            out.chapters[ch] = chosen
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
        taken = {ch: out.chapters[ch][0].file.url for ch in unused if ch in out.chapters}
        if taken:
            out.repeats[f.url] = taken
    out.suspect = _suspects(out.chapters, fixed)
    out.gaps = _gaps(out.chapters, fixed, planned)
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


def _too_short(ch: int, pieces: list[Piece], ayah_counts: dict[int, int] | None) -> str | None:
    total = (ayah_counts or {}).get(ch)
    if not total:
        return None
    covered = set().union(*(ayahs(p.cut.rows) for p in pieces))
    if len(covered) >= MIN_SURAH_COVERAGE * total:
        return None
    where = ", ".join(sorted({p.file.url.rsplit("/", 1)[-1] for p in pieces}))
    return f"only {len(covered)} of {total} ayahs found (in {where}) — an excerpt, not adopted"


def _gaps(chapters: dict[int, list[Piece]], fixed: set[int], planned: set[int]) -> list[int]:
    present = set(chapters) | fixed
    if not present:
        return []
    lo, hi = min(present), max(present)
    return [n for n in range(lo + 1, hi) if n not in present and n not in planned]


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
