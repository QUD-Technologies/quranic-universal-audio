"""Aligner public rows → the staged-run shapes ``promote_build`` reads.

The aligner returns the app's public segment rows (seconds, ``ref_from``/``ref_to``,
``kind``/``special_type``, plus ``merge_group_id``/``merge_members`` when asked).
The Katana pipeline's post-process did three things on top of the matcher's
output before staging, and this module does the same three, in the same order and
with the same index bookkeeping (``qua_sdk.pipelines.align_postprocess``):

1. audit every waqf-sakt auto-merge as a ``waqf_sakt`` event — both snapshots
   in the pre-strip row order, the merged row at index ``k`` having consumed
   ``k + 1``;
2. strip the special rows (Basmala / Isti'adha / …) as ``delete_segment`` events
   recorded at their pre-strip index;
3. stamp the surviving waqf row with its post-strip ``final_index``.

Times go from seconds to integer milliseconds and confidence to two decimals here
and nowhere else, so a row's published time and the time its event recorded can
never round apart.
"""

from __future__ import annotations

from bisect import bisect_left

_MS = 1000
_CONF_DECIMALS = 2
#: A merge is a certainty, not a match score (mirrors the SDK's MERGED_CONFIDENCE).
_MERGED_CONFIDENCE = 1.0
_BASMALA = "Basmala"
_COMBINED_SPECIAL = "Isti'adha+Basmala"


def to_ms(seconds: float) -> int:
    return round(float(seconds) * _MS)


def to_conf(value: float | None) -> float:
    return round(float(value or 0.0), _CONF_DECIMALS)


def matched_ref(row: dict) -> str:
    """``ref_from``/``ref_to`` back to the ``a-b`` span the SDK writes (``""`` if none).

    A one-word segment is ``a-a``, never a bare ``a``. Both text resolvers —
    ``services/reference/quran_refs.py::dk_text_for_ref`` and its frontend mirror
    ``dkTextForRef`` — require two dash-separated endpoints and return no text at
    all for a single coordinate, so a collapsed ref renders as an empty row and
    silently starves everything derived from the text (qalqala stamping,
    validation, tajweed). Every reciter written by the offline pipeline stores the
    degenerate span, muqattaat openers included.
    """
    a, b = row.get("ref_from") or "", row.get("ref_to") or ""
    if not a:
        return ""
    return f"{a}-{b or a}"


def is_special(row: dict) -> bool:
    return row.get("kind") == "special"


def _segment(row: dict, riwayah: str) -> dict:
    """One kept row as a ``DetailedSegment`` dict in the delivery edition's coordinates.

    A non-Hafs delivery's rows arrive projected (the aligner matched against
    Hafs and wrote the edition's refs); the Hafs ``source_ref`` the sidecars and
    the timestamps engine align against is re-derived here through the same
    ``qua_domain`` projection the Inspector's own save path uses, so the aligner
    never has to ship it.
    """
    from services.segments.projection_stamp import stamp_projection

    seg = {
        "time_start": to_ms(row["time_from"]),
        "time_end": to_ms(row["time_to"]),
        "matched_ref": matched_ref(row),
        "confidence": to_conf(row.get("confidence")),
    }
    if row.get("wrap_word_ranges"):
        seg["wrap_word_ranges"] = row["wrap_word_ranges"]
    stamp_projection(seg, riwayah)
    return seg


def _snapshot(index: int, row: dict, *, confidence: float | None = None, ref: str | None = None):
    return {
        "index_at_save": index,
        "time_start": to_ms(row["time_from"]),
        "time_end": to_ms(row["time_to"]),
        "matched_ref": matched_ref(row) if ref is None else ref,
        "confidence": to_conf(row.get("confidence") if confidence is None else confidence),
    }


def waqf_events(chapter: int, rows: list[dict]) -> list[dict]:
    """One ``waqf_sakt`` event per auto-merged row, in pre-strip row order."""
    events = []
    for index, row in enumerate(rows):
        members = row.get("merge_members") or []
        if not row.get("merge_group_id") or len(members) != 2:
            continue
        a, b = members
        after = _snapshot(index, row, confidence=_MERGED_CONFIDENCE)
        events.append(
            {
                "kind": "waqf_sakt",
                "chapter": chapter,
                "targets_before": [_snapshot(index, a), _snapshot(index + 1, b)],
                "targets_after": [after],
            }
        )
    return events


def strip_specials(
    chapter: int, rows: list[dict]
) -> tuple[list[dict], list[dict], list[int], bool]:
    """Drop the special rows. Returns ``(kept, delete events, removed indices, basmala?)``."""
    kept, events, removed = [], [], []
    basmala = False
    for index, row in enumerate(rows):
        if not is_special(row):
            kept.append(row)
            continue
        name = row.get("special_type") or "special"
        if name == _COMBINED_SPECIAL:
            name = _BASMALA
        basmala = basmala or name == _BASMALA
        events.append(
            {
                "kind": "delete_segment",
                "chapter": chapter,
                "targets_before": [_snapshot(index, row, ref=name)],
                "targets_after": [],
            }
        )
        removed.append(index)
    return kept, events, removed, basmala


def finalise_indices(events: list[dict], removed: list[int]) -> None:
    for ev in events:
        if ev["kind"] != "waqf_sakt":
            continue
        for snap in ev["targets_after"]:
            snap["final_index"] = snap["index_at_save"] - bisect_left(
                removed, snap["index_at_save"]
            )


def source_offset(result: dict) -> int:
    """Where the chapter starts inside its source file: the split stage's cut
    offset for a chapter of a combined file, else 0."""
    return int((result.get("_inspector") or {}).get("split_offset_ms") or 0)


def adapt_chapter(
    chapter: int, result: dict, *, source_url: str, riwayah: str
) -> tuple[dict, list[dict], bool]:
    """One aligner item result → ``(ChapterCandidate doc, events, basmala_deleted)``.

    Rows the matcher could not place (empty ``ref_from``) are kept with an empty
    ``matched_ref``, exactly as the Katana staging did — the Inspector's
    validation surfaces them rather than the pipeline silently dropping audio.
    """
    rows = list(result.get("segments") or [])
    events = waqf_events(chapter, rows)
    kept, deletes, removed, basmala = strip_specials(chapter, rows)
    finalise_indices(events, removed)
    candidate = {
        "chapter": chapter,
        "entries": [{"ref": str(chapter), "segments": [_segment(r, riwayah) for r in kept]}],
        "source_url": source_url,
        "source_offset_ms": source_offset(result),
        "trim_span_ms": None,
        "riwayah": riwayah,
    }
    # Events in the order the passes made them: merges first, then the strip.
    return candidate, events + deletes, basmala
