"""Pure logic of the split step: which rows of an aligned file belong to which
chapter, and where to cut. No I/O — ``stage_split`` feeds it the staged aligner
results and acts on the answer.

A source file's rows are partitioned by the surah of their ``ref_from``.
Special rows (Isti'adha / Basmala) belong to the surah they introduce, so they
attach *forward*; rows the matcher could not place attach to the surah before
them. Each chapter's window is its first row's start to its last row's end,
padded by ``TRIM_PAD_MS`` (the Katana split's pad), clamped to the file and
never overlapping a neighbour — a collision is cut at the silence midpoint.
Which file a surah is finally taken from is ``resolve.py``'s call.

The same surah test also guards single-chapter files: a file whose matched
audio is mostly another surah was mislabelled in the plan (the
``mohammed_burhaji_yt`` mis-index), and must not be published as this chapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TRIM_PAD_MS = 300
#: A single-chapter file is "another surah" when at least this share of its
#: matched speech aligns elsewhere.
MISMATCH_SHARE = 0.6
_MS = 1000


@dataclass
class ChapterCut:
    chapter: int
    start_ms: int
    end_ms: int
    rows: list[dict] = field(default_factory=list)


def unmatched_ms(rows: list[dict]) -> int:
    """Recitation the aligner could not place (a ``quran`` row with no ref)."""
    return sum(
        round((r.get("time_to", 0) - r.get("time_from", 0)) * 1000)
        for r in rows
        if r.get("kind") == "quran" and not r.get("ref_from")
    )


def row_surah(row: dict) -> int | None:
    ref = row.get("ref_from") or ""
    head = ref.split(":", 1)[0]
    return int(head) if head.isdigit() else None


def _assign(rows: list[dict]) -> list[int | None]:
    """Surah per row: specials attach forward, unplaced rows attach backward."""
    out: list[int | None] = [None if r.get("kind") == "special" else row_surah(r) for r in rows]
    nxt: int | None = None
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get("kind") == "special":
            out[i] = nxt
        elif out[i] is not None:
            nxt = out[i]
    prev: int | None = None
    for i, s in enumerate(out):
        if s is None:
            out[i] = prev
        else:
            prev = s
    first = next((s for s in out if s is not None), None)
    return [s if s is not None else first for s in out]


def cut_file(rows: list[dict], duration_ms: int | None) -> dict[int, ChapterCut]:
    """Every surah the aligner found in one file, with its cut window. Windows
    are computed over all of them, so a surah later assigned to another file
    still bounds its neighbours' cuts."""
    by_surah: dict[int, list[dict]] = {}
    for row, surah in zip(rows, _assign(rows), strict=True):
        if surah is not None:
            by_surah.setdefault(surah, []).append(row)
    return _windows(by_surah, duration_ms)


def matched_ms(rows: list[dict]) -> int:
    """Recitation the aligner placed — how strongly a file holds a surah."""
    return sum(
        round((r.get("time_to", 0) - r.get("time_from", 0)) * _MS)
        for r in rows
        if r.get("kind") == "quran" and r.get("ref_from")
    )


def ayahs(rows: list[dict]) -> set[int]:
    """Ayah numbers the rows cover (``ref_from``..``ref_to`` of their surah)."""
    out: set[int] = set()
    for r in rows:
        head = _ref(r.get("ref_from"))
        if head is None:
            continue
        tail = _ref(r.get("ref_to"))
        last = tail[1] if tail is not None and tail[0] == head[0] else head[1]
        out.update(range(head[1], max(head[1], last) + 1))
    return out


def _ref(ref: str | None) -> tuple[int, int] | None:
    parts = (ref or "").split(":")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    return int(parts[0]), int(parts[1])


def _windows(by_surah: dict[int, list[dict]], duration_ms: int | None) -> dict[int, ChapterCut]:
    order = sorted(by_surah, key=lambda s: min(r["time_from"] for r in by_surah[s]))
    spans = []
    for s in order:
        rows = sorted(by_surah[s], key=lambda r: r["time_from"])
        first = round(rows[0]["time_from"] * _MS)
        last = round(max(r["time_to"] for r in rows) * _MS)
        spans.append((s, first, last, rows))
    cuts: dict[int, ChapterCut] = {}
    for i, (s, first, last, rows) in enumerate(spans):
        start = max(0, first - TRIM_PAD_MS)
        end = last + TRIM_PAD_MS
        if duration_ms is not None:
            end = min(end, duration_ms)
        # Neighbours share one boundary: the midpoint of the silence between
        # them (inside the overlap when a row runs across the surah boundary).
        if i > 0:
            start = max(start, (spans[i - 1][2] + first) // 2)
        if i + 1 < len(spans):
            end = min(end, (last + spans[i + 1][1]) // 2)
        cuts[s] = ChapterCut(chapter=s, start_ms=start, end_ms=max(end, start + 1), rows=rows)
    return cuts


def rebase(rows: list[dict], offset_ms: int) -> list[dict]:
    """Rows on the cut file's timeline. Word timings are segment-relative and
    ride through unchanged."""
    shift = offset_ms / _MS
    return [
        {
            **r,
            "time_from": round(r["time_from"] - shift, 3),
            "time_to": round(r["time_to"] - shift, 3),
        }
        for r in rows
    ]


def dominant_other_surah(chapter: int, rows: list[dict]) -> int | None:
    """The surah a single-chapter file really holds, when it is not ``chapter``."""
    weight: dict[int, float] = {}
    for r in rows:
        s = row_surah(r)
        if s is not None and r.get("kind") != "special":
            weight[s] = weight.get(s, 0.0) + max(0.0, r["time_to"] - r["time_from"])
    total = sum(weight.values())
    if not total:
        return None
    top = max(weight, key=weight.__getitem__)
    if top != chapter and weight[top] / total >= MISMATCH_SHARE:
        return top
    return None
