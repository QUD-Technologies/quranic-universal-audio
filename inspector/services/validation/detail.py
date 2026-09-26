"""Per-segment detail-list builder for validate_reciter_segments.

Iterates entries and returns the detail lists (failed, low_confidence,
boundary_adj, cross_verse, audio_bleeding, repetitions, muqattaat,
qalqala, basmala_amin) plus the verse_segments coverage map. Each detail item carries a
``classified_issues`` field — the full category list the segment matches
under the unified classifier (forward-compat for multi-category card
indicators on the frontend).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from config import LOW_CONFIDENCE_DETAIL_THRESHOLD, MISSED_BASMALA_FLAG_MIN_DELETED
from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from services.reference import edition_tables
from services.reference.quran_refs import dk_text_for_ref
from services.storage.data_loader import load_detailed
from services.validation.classifier import (
    classify_flags,
    classify_segment,
    is_ignored_for,
    is_suppressed_for,
)
from services.validation.registry import PER_SEGMENT_CATEGORIES
from utils.formatting import format_ms
from utils.references import chapter_from_ref, seg_belongs_to_entry


def _compute_surah_offsets(
    word_counts: dict[tuple[int, int], int],
) -> dict[tuple[int, int], int]:
    """Return ``(surah, ayah) -> sum(word_counts[(surah, v)] for v in 1..ayah-1)``.

    Computed once per ``_build_detail_lists`` call and passed down — no cache.
    For the production singleton (~6 236 entries) this is ~1 ms; for synthetic
    test dicts (≤ 100 entries) it's negligible. Only contiguous prefixes from
    ayah=1 get an entry — if there's a gap in ``word_counts`` for a surah,
    post-gap ayahs are omitted so ``_word_ord`` returns None for them.
    """
    by_surah: dict[int, dict[int, int]] = {}
    for (surah, ayah), wc in word_counts.items():
        by_surah.setdefault(surah, {})[ayah] = wc
    offsets: dict[tuple[int, int], int] = {}
    for surah, ayah_to_wc in by_surah.items():
        running = 0
        ayah = 1
        while ayah in ayah_to_wc:
            offsets[(surah, ayah)] = running
            running += ayah_to_wc[ayah]
            ayah += 1
    return offsets


def _word_ord(
    surah: int,
    ayah: int,
    word: int,
    word_counts: dict[tuple[int, int], int],
    offsets: dict[tuple[int, int], int],
) -> int | None:
    """Return 1-based word ordinal within a surah, or None if out of range.

    ``offsets`` is the precomputed prefix-sum table from
    ``_compute_surah_offsets`` — hoist it once at the top of
    ``_build_detail_lists`` and pass it down.
    """
    base = offsets.get((surah, ayah))
    if base is None:
        return None
    wc = word_counts.get((surah, ayah), 0)
    if word < 1 or word > wc:
        return None
    return base + word


def _words_by_verse_for_ord_gap(
    surah: int,
    start_ord: int,
    end_ord: int,
    word_counts: dict[tuple[int, int], int],
) -> list[tuple[int, list[int]]]:
    """Split a surah-local ordinal gap into ``[(ayah, [word, ...]), ...]``."""
    out: list[tuple[int, list[int]]] = []
    offset = 0
    ayah = 1
    while (surah, ayah) in word_counts and offset < end_ord:
        wc = word_counts[(surah, ayah)]
        verse_start = offset + 1
        verse_end = offset + wc
        lo = max(start_ord, verse_start)
        hi = min(end_ord, verse_end)
        if lo <= hi:
            out.append((ayah, [ord_ - offset for ord_ in range(lo, hi + 1)]))
        offset = verse_end
        ayah += 1
    return out


def _classified_issues_from_flags(flags: dict, *, detail: bool) -> list[str]:
    """Translate a classifier flags dict to a category list (registry order).

    Mirrors the public ``classify_segment`` output so detail items and the
    standalone classifier API agree on the field's contents.
    """
    cats: list[str] = []
    for cat in PER_SEGMENT_CATEGORIES:
        if flags.get(cat):
            cats.append(cat)
    if detail and flags.get("low_confidence_detail") and "low_confidence" not in cats:
        cats.append("low_confidence_detail")
    return cats


def _int_or_none(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _cut_payload(cut: dict) -> dict:
    return {
        "cursor_ms": _int_or_none(cut.get("cursor_ms")),
        "axes": [str(a) for a in (cut.get("axes") or [])],
        "gap_ms": _int_or_none(cut.get("gap_ms")),
        "score": _int_or_none(cut.get("score")),
        "word": cut.get("word"),
        "final_class": cut.get("final_class"),
        "verse_end": bool(cut.get("verse_end", False)),
        "evidence": dict(cut.get("evidence") or {}),
    }


def hidden_pause_boundary(entry: dict) -> dict:
    """Project a ``hidden_pause_v1`` / ``missed_waqf_v1`` by-uid entry to the
    wire ``boundary`` shape."""
    refs = entry.get("refs")
    return {
        "cursors": [int(c) for c in (entry.get("cursors") or [])],
        "refs": [str(r) for r in refs] if isinstance(refs, list) else None,
        "score": _int_or_none(entry.get("score")) or 0,
        "cuts": [_cut_payload(c) for c in (entry.get("cuts") or []) if isinstance(c, dict)],
    }


def false_split_boundary(entry: dict) -> dict:
    """Project a ``false_split_v1`` / ``unmarked_wasl_v1`` by-uid entry to the
    wire ``boundary`` shape (both sidecars describe the join to ``next_uid``)."""
    return {
        "next_uid": entry.get("next_uid"),
        "axes": [str(a) for a in (entry.get("axes") or [])],
        "gap_ms": _int_or_none(entry.get("gap_ms")),
        "score": _int_or_none(entry.get("score")) or 0,
        "word": entry.get("word"),
        "final_class": entry.get("final_class"),
        "verse_end": bool(entry.get("verse_end", False)),
        "is_wasl": bool(entry.get("is_wasl", False)),
        "evidence": dict(entry.get("evidence") or {}),
    }


def _build_detail_lists(
    entries: list[dict],
    is_by_ayah: bool,
    word_counts: dict,
    canonical: dict | None,
    single_word_verses: set,
    probe_failed_uids: set | None = None,
    deleted_basmala_chapters: set[int] | None = None,
    hidden_pause_map: dict[str, dict] | None = None,
    missed_waqf_map: dict[str, dict] | None = None,
    false_split_map: dict[str, dict] | None = None,
    unmarked_wasl_map: dict[str, dict] | None = None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
) -> dict:
    """Iterate entries and build all detail lists + verse_segments map.

    Returns a dict with keys:
      chapter_seg_idx, verse_segments,
      failed, low_confidence, low_confidence_v2, hidden_pause, missed_waqf,
      false_split, unmarked_wasl, boundary_adj, cross_verse, audio_bleeding, repetitions,
      muqattaat, qalqala, basmala_amin.

    ``hidden_pause_map`` / ``missed_waqf_map`` / ``false_split_map`` /
    ``unmarked_wasl_map`` are the ``by_uid`` maps of the offline boundary-review
    sidecars; each flagged item carries the sidecar payload under ``boundary``
    so the card can render the evidence. A live ``missed_waqf`` seg whose flag
    is suppressed (ignored = every cut WASL, or split from the card) is still
    emitted, with ``resolved: True``, so its labels stay reviewable.

    ``probe_failed_uids`` is the set of segment UIDs flagged by the
    extraction-time MFA tight-beam probe; pass ``None`` (or omit) when
    the sidecar isn't present and the v2 list should stay empty.

    ``deleted_basmala_chapters`` is the set of chapters from which the
    alignment pipeline (strip_specials) already deleted a Basmala. When the
    count of deleted chapters reaches ``MISSED_BASMALA_FLAG_MIN_DELETED``, the
    rule flags the first segment of every other chapter (excluding 1 and 9)
    as a "possibly missed Basmala" in ``basmala_amin``. Below the threshold
    (or when the set is empty / ``None``) the augmentation is skipped — the
    reciter is treated as one who doesn't routinely recite inter-chapter
    basmalas, so flagging every chapter would just be noise.
    """
    # Precompute surah-offsets table once per validate pass. Used by every
    # ``_word_ord`` call below to convert (surah, ayah, word) refs into a
    # surah-local 1-based word ordinal in O(1). Computing it inline (~1 ms
    # over the ~6 236-entry singleton) is cheaper than the cache dance the
    # prior implementation needed to defend against test ``id()`` reuse.
    offsets = _compute_surah_offsets(word_counts)

    failed: list[dict] = []
    low_confidence: list[dict] = []
    low_confidence_v2: list[dict] = []
    hidden_pause: list[dict] = []
    missed_waqf: list[dict] = []
    false_split: list[dict] = []
    unmarked_wasl: list[dict] = []
    boundary_adj: list[dict] = []
    cross_verse: list[dict] = []
    audio_bleeding: list[dict] = []
    repetitions: list[dict] = []
    muqattaat: list[dict] = []
    qalqala: list[dict] = []
    basmala_11: list[tuple[dict, bool]] = []
    basmala_amin_17: list[tuple[dict, bool]] = []
    chapter_seg_idx: dict[int, int] = {}
    verse_segments: dict[tuple[int, int], list] = defaultdict(list)
    sequence_gaps: list[dict] = []
    first_seg_by_chapter: dict[int, dict] = {}
    # Parallel store: the live seg dict for each chapter's first segment, so
    # the missed-Basmala augmentation below can honour per-segment ignores
    # via ``is_suppressed_for(seg, "basmala_amin")``.
    first_seg_obj_by_chapter: dict[int, dict] = {}

    for entry in entries:
        chapter = chapter_from_ref(entry["ref"])
        entry_ref = entry.get("ref", "")
        prev_end_by_surah: dict[int, int] = {}
        prev_seg_idx_by_surah: dict[int, int] = {}
        for seg in entry.get("segments", []):
            i = chapter_seg_idx.get(chapter, 0)
            chapter_seg_idx[chapter] = i + 1
            matched_ref = seg.get("matched_ref", "")
            confidence = seg.get("confidence", 0.0)
            t_start = seg.get("time_start", 0)
            t_end = seg.get("time_end", 0)
            seg_uid = seg.get("segment_uid") or None

            if i == 0 and chapter not in first_seg_by_chapter:
                # Capture identity of the first seg of every chapter, regardless
                # of whether matched_ref is valid — the missed-Basmala rule
                # below needs this even when the first seg failed to align.
                first_seg_by_chapter[chapter] = {
                    "chapter": chapter,
                    "seg_index": 0,
                    "segment_uid": seg_uid,
                    "ref": matched_ref,
                    "time": f"{format_ms(t_start)}-{format_ms(t_end)}",
                }
                first_seg_obj_by_chapter[chapter] = seg

            if not matched_ref:
                # Respect is_ignored_for so _all / ignored=True suppresses the
                # failed entry from detail lists and category_counts alike —
                # mirrors the classify_flags gate added for MUST-6 consistency.
                if not is_ignored_for(seg, "failed"):
                    failed.append(
                        {
                            "chapter": chapter,
                            "seg_index": i,
                            "segment_uid": seg_uid,
                            "time": f"{format_ms(t_start)}-{format_ms(t_end)}",
                            "classified_issues": classify_segment(seg, riwayah=riwayah),
                        }
                    )
                continue

            parts = matched_ref.split("-")
            if len(parts) != 2:
                # Malformed structural ref — fall back to a minimal flag check
                # so audio_bleeding / repetitions / low_confidence still surface.
                fallback_issues: list[str] = []
                if (
                    is_by_ayah
                    and ":" in entry_ref
                    and not seg_belongs_to_entry(matched_ref, entry_ref)
                    and not is_suppressed_for(seg, "audio_bleeding")
                ):
                    seg_start = matched_ref.split("-")[0]
                    seg_parts = seg_start.split(":")
                    matched_verse = (
                        f"{seg_parts[0]}:{seg_parts[1]}" if len(seg_parts) >= 2 else matched_ref
                    )
                    audio_bleeding.append(
                        {
                            "chapter": chapter,
                            "seg_index": i,
                            "segment_uid": seg_uid,
                            "entry_ref": entry_ref,
                            "matched_verse": matched_verse,
                            "ref": matched_ref,
                            "confidence": round(confidence, 4),
                            "time": f"{format_ms(t_start)}-{format_ms(t_end)}",
                            "msg": f"audio {entry_ref} contains segment matching verse {matched_verse}",
                            "classified_issues": ["audio_bleeding"],
                        }
                    )
                    fallback_issues.append("audio_bleeding")
                if seg.get("wrap_word_ranges") and not is_suppressed_for(seg, "repetitions"):
                    repetitions.append(
                        {
                            "chapter": chapter,
                            "seg_index": i,
                            "segment_uid": seg_uid,
                            "ref": matched_ref,
                            "display_ref": matched_ref,
                            "confidence": round(confidence, 4),
                            "time": f"{format_ms(t_start)}-{format_ms(t_end)}",
                            "text": dk_text_for_ref(matched_ref, riwayah),
                            "classified_issues": ["repetitions"],
                        }
                    )
                    fallback_issues.append("repetitions")
                if confidence < LOW_CONFIDENCE_DETAIL_THRESHOLD:
                    low_confidence.append(
                        {
                            "ref": matched_ref,
                            "chapter": chapter,
                            "seg_index": i,
                            "segment_uid": seg_uid,
                            "confidence": round(confidence, 4),
                            "classified_issues": ["low_confidence"] + fallback_issues,
                        }
                    )
                continue

            start_parts = parts[0].split(":")
            end_parts = parts[1].split(":")
            if len(start_parts) != 3 or len(end_parts) != 3:
                continue
            try:
                surah = int(start_parts[0])
                s_ayah = int(start_parts[1])
                s_word = int(start_parts[2])
                e_ayah = int(end_parts[1])
                e_word = int(end_parts[2])
            except (ValueError, IndexError):
                continue

            start_ord = _word_ord(surah, s_ayah, s_word, word_counts, offsets)
            end_ord = _word_ord(surah, e_ayah, e_word, word_counts, offsets)
            if start_ord is not None and end_ord is not None:
                prev_end = prev_end_by_surah.get(surah)
                if prev_end is not None and start_ord > prev_end + 1:
                    prev_idx = prev_seg_idx_by_surah.get(surah)
                    indices = [idx for idx in (prev_idx, i) if idx is not None]
                    for ayah, missing in _words_by_verse_for_ord_gap(
                        surah, prev_end + 1, start_ord - 1, word_counts
                    ):
                        sequence_gaps.append(
                            {
                                "verse_key": f"{surah}:{ayah}",
                                "chapter": surah,
                                "missing_words": missing,
                                "seg_indices": indices,
                            }
                        )
                prev_end_by_surah[surah] = end_ord
                prev_seg_idx_by_surah[surah] = i

            flags = classify_flags(
                seg,
                entry_ref,
                is_by_ayah,
                surah,
                s_ayah,
                e_ayah,
                s_word,
                e_word,
                single_word_verses,
                canonical,
                probe_failed_uids=probe_failed_uids,
                hidden_pause_uids=hidden_pause_map,
                missed_waqf_uids=missed_waqf_map,
                false_split_uids=false_split_map,
                unmarked_wasl_uids=unmarked_wasl_map,
                riwayah=riwayah,
            )
            classified = _classified_issues_from_flags(flags, detail=True)

            if flags["audio_bleeding"]:
                seg_start = matched_ref.split("-")[0]
                sp = seg_start.split(":")
                matched_verse = f"{sp[0]}:{sp[1]}" if len(sp) >= 2 else matched_ref
                audio_bleeding.append(
                    {
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "entry_ref": entry_ref,
                        "matched_verse": matched_verse,
                        "ref": matched_ref,
                        "confidence": round(confidence, 4),
                        "time": f"{format_ms(t_start)}-{format_ms(t_end)}",
                        "msg": f"audio {entry_ref} contains segment matching verse {matched_verse}",
                        "classified_issues": classified,
                    }
                )

            if flags["repetitions"]:
                display_ref = matched_ref
                rp = matched_ref.split("-")
                if len(rp) == 2:
                    s_rp = rp[0].split(":")
                    e_rp = rp[1].split(":")
                    if len(s_rp) >= 2 and len(e_rp) >= 2 and s_rp[1] == e_rp[1]:
                        display_ref = f"{s_rp[0]}:{s_rp[1]}"
                repetitions.append(
                    {
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "ref": matched_ref,
                        "display_ref": display_ref,
                        "confidence": round(confidence, 4),
                        "time": f"{format_ms(t_start)}-{format_ms(t_end)}",
                        "text": dk_text_for_ref(matched_ref, riwayah),
                        "classified_issues": classified,
                    }
                )

            if flags["low_confidence_detail"]:
                lp = matched_ref.split("-")
                display_ref = matched_ref
                if len(lp) == 2:
                    s = lp[0].split(":")
                    e = lp[1].split(":")
                    if len(s) >= 2 and len(e) >= 2:
                        display_ref = f"{s[0]}:{s[1]}" if s[1] == e[1] else f"{s[0]}:{s[1]}-{e[1]}"
                low_confidence.append(
                    {
                        "ref": display_ref,
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "confidence": round(confidence, 4),
                        "classified_issues": classified,
                    }
                )

            if flags["low_confidence_v2"]:
                low_confidence_v2.append(
                    {
                        "ref": matched_ref,
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "classified_issues": classified,
                    }
                )

            if flags["hidden_pause"]:
                hidden_pause.append(
                    {
                        "ref": matched_ref,
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "classified_issues": classified,
                        "boundary": hidden_pause_boundary((hidden_pause_map or {})[str(seg_uid)]),
                    }
                )

            missed_waqf_entry = (missed_waqf_map or {}).get(str(seg_uid)) if seg_uid else None
            if missed_waqf_entry is not None:
                item = {
                    "ref": matched_ref,
                    "chapter": chapter,
                    "seg_index": i,
                    "segment_uid": seg_uid,
                    "classified_issues": classified,
                    "boundary": hidden_pause_boundary(missed_waqf_entry),
                }
                if not flags["missed_waqf"]:
                    item["resolved"] = True
                missed_waqf.append(item)

            if flags["false_split"]:
                false_split.append(
                    {
                        "ref": matched_ref,
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "classified_issues": classified,
                        "boundary": false_split_boundary((false_split_map or {})[str(seg_uid)]),
                    }
                )

            if flags["unmarked_wasl"]:
                unmarked_wasl.append(
                    {
                        "ref": matched_ref,
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "classified_issues": classified,
                        "boundary": false_split_boundary((unmarked_wasl_map or {})[str(seg_uid)]),
                    }
                )

            if flags["cross_verse"]:
                cross_verse.append(
                    {
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "ref": matched_ref,
                        "classified_issues": classified,
                    }
                )

            if flags["boundary_adj"]:
                boundary_adj.append(
                    {
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "ref": matched_ref,
                        "verse_key": f"{surah}:{s_ayah}",
                        "classified_issues": classified,
                    }
                )

            if flags["muqattaat"]:
                muqattaat.append(
                    {
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "ref": matched_ref,
                        "classified_issues": classified,
                    }
                )

            if flags["qalqala"]:
                qalqala.append(
                    {
                        "chapter": chapter,
                        "seg_index": i,
                        "segment_uid": seg_uid,
                        "ref": matched_ref,
                        "qalqala_letter": flags["qalqala_letter"],
                        "end_of_verse": (e_word == word_counts.get((surah, e_ayah), 0)),
                        "classified_issues": classified,
                    }
                )

            # Two independent sub-checks on al-Fatiha, and only the second
            # applies to every edition.
            #
            # The sounded-Basmala check asks "is the Basmala at 1:1 covered by a
            # segment?", which is a question only where the Basmala IS verse 1:1
            # — Hafs and Shu'bah. Warsh and Qalun render it as an unnumbered
            # opener that the offline pipeline strips, so there is no 1:1 to
            # cover and flagging one would be permanent, unresolvable noise.
            #
            # The Amin check asks about the verse AFTER al-Fatiha's last, which
            # every edition has. Warsh's al-Fatiha still ends at 7 (it drops the
            # numbered Basmala and splits a later verse), but the number is read
            # from the edition rather than assumed.
            if surah == 1:
                item = {
                    "chapter": chapter,
                    "seg_index": i,
                    "segment_uid": seg_uid,
                    "ref": matched_ref,
                    "classified_issues": classified,
                }
                # Suppression is applied at the output gate (below), not at
                # candidate collection. If a user resolves the canonical
                # candidate (first 1:1 or last 1:<fatiha_last>), the card should
                # disappear -- NOT promote a neighbouring seg, which would
                # be whack-a-mole.
                suppressed = is_suppressed_for(seg, "basmala_amin")
                fatiha_last = edition_tables.fatiha_last_ayah(riwayah)
                if edition_tables.basmala_is_numbered(riwayah) and s_ayah <= 1 <= e_ayah:
                    basmala_11.append((item, suppressed))
                if s_ayah <= fatiha_last <= e_ayah:
                    basmala_amin_17.append((item, suppressed))

            # Accumulate verse coverage (3-tuple: word_from, word_to, seg_index)
            if s_ayah != e_ayah:
                for ayah in range(s_ayah, e_ayah + 1):
                    if ayah == s_ayah:
                        wc = word_counts.get((surah, ayah), s_word)
                        verse_segments[(surah, ayah)].append((s_word, wc, i))
                    elif ayah == e_ayah:
                        verse_segments[(surah, ayah)].append((1, e_word, i))
                    else:
                        wc = word_counts.get((surah, ayah), 1)
                        verse_segments[(surah, ayah)].append((1, wc, i))
            else:
                verse_segments[(surah, s_ayah)].append((s_word, e_word, i))

    # "Missed Basmala" augmentation. For every chapter (≠1, ≠9) whose Basmala
    # the alignment pipeline did NOT strip, the first segment is flagged as a
    # candidate that may need manual handling. Chapter 1 (Al-Fatiha) has
    # Basmala as its first ayah by design (the canonical 1:1 entry is already
    # flagged above); chapter 9 (At-Tawbah) traditionally has no Basmala.
    #
    # Gated on ``len(deleted_basmala_chapters) >= MISSED_BASMALA_FLAG_MIN_DELETED``:
    # for reciters who don't conventionally recite an inter-chapter Basmala the
    # pipeline strips none of them, so flagging every chapter would flood the
    # accordion with false positives. Only kick the augmentation in once the
    # reciter has shown they DO recite basmalas regularly.
    missed_basmalas: list[dict] = []
    if (
        deleted_basmala_chapters is not None
        and len(deleted_basmala_chapters) >= MISSED_BASMALA_FLAG_MIN_DELETED
    ):
        for chapter, first_seg in first_seg_by_chapter.items():
            if chapter in (1, 9):
                continue
            if chapter in deleted_basmala_chapters:
                continue
            seg_obj = first_seg_obj_by_chapter.get(chapter)
            if seg_obj is not None and is_suppressed_for(seg_obj, "basmala_amin"):
                continue
            missed_basmalas.append(
                {
                    **first_seg,
                    "missed_basmala": True,
                    "classified_issues": ["basmala_amin"],
                }
            )

    # Order: first 1:1 seg → last 1:7 seg → remaining 1:1 segs → missed basmalas
    # (from other chapters). Dedup by (chapter, seg_index, segment_uid) preserves
    # the explicit order while collapsing the overlap when a single seg spans
    # both 1:1 and 1:7 (synthetic short fixtures).
    #
    # Suppression is applied here, not at candidate collection: if the canonical
    # candidate (first 1:1, last 1:7) is ignored or resolved-by-edit, the slot
    # is left empty instead of being filled by the next-best seg.
    combined_basmala_amin: list[dict] = []
    seen_basmala_amin: set[tuple[int, int, str | None]] = set()
    ordered_items: list[dict] = []
    if basmala_11 and not basmala_11[0][1]:
        ordered_items.append(basmala_11[0][0])
    if basmala_amin_17:
        last_item, last_suppressed = basmala_amin_17[-1]
        if not last_suppressed:
            ordered_items.append(last_item)
    for item, suppressed in basmala_11[1:]:
        if not suppressed:
            ordered_items.append(item)
    ordered_items.extend(missed_basmalas)
    for item in ordered_items:
        key = (item["chapter"], item["seg_index"], item.get("segment_uid"))
        if key in seen_basmala_amin:
            continue
        seen_basmala_amin.add(key)
        combined_basmala_amin.append(item)

    return {
        "chapter_seg_idx": chapter_seg_idx,
        "verse_segments": verse_segments,
        "sequence_gaps": sequence_gaps,
        "failed": failed,
        "low_confidence": low_confidence,
        "low_confidence_v2": low_confidence_v2,
        "hidden_pause": hidden_pause,
        "missed_waqf": missed_waqf,
        "false_split": false_split,
        "unmarked_wasl": unmarked_wasl,
        "boundary_adj": boundary_adj,
        "cross_verse": cross_verse,
        "audio_bleeding": audio_bleeding,
        "repetitions": repetitions,
        "muqattaat": muqattaat,
        "qalqala": qalqala,
        "basmala_amin": combined_basmala_amin,
    }


# ---------------------------------------------------------------------------
# Identity helpers (IS-10, MUST-9)
# ---------------------------------------------------------------------------


def resolve_segment_by_uid(reciter: str, uid: str) -> dict | None:
    """Return the live segment dict matching *uid*, or ``None`` if absent."""
    entries = load_detailed(reciter)
    if not entries:
        return None
    for entry in entries:
        for seg in entry.get("segments", []):
            if seg.get("segment_uid") == uid:
                return seg
    return None


def resolve_segment_for_issue(reciter: str, issue: dict) -> dict | None:
    """Return the live segment for *issue*, using uid when present, else seg_index.

    Uid-first: if the issue carries ``segment_uid``, look up by uid.
    Falls back to ``(chapter, seg_index)`` for legacy issues without a uid.
    """
    uid = issue.get("segment_uid")
    if uid:
        return resolve_segment_by_uid(reciter, uid)

    chapter = issue.get("chapter")
    seg_index = issue.get("seg_index")
    if chapter is None or seg_index is None:
        return None

    entries = load_detailed(reciter)
    if not entries:
        return None

    idx = 0
    for entry in entries:
        if chapter_from_ref(entry["ref"]) != chapter:
            continue
        for seg in entry.get("segments", []):
            if idx == seg_index:
                return seg
            idx += 1
    return None


def filter_stale_issues(issues: list[dict], live_uids: set[str]) -> list[dict]:
    """Drop issue items whose ``segment_uid`` is not in *live_uids*.

    Items without a ``segment_uid`` (legacy seg_index path) are kept
    untouched so existing resolution logic still applies to them.
    """
    result: list[dict] = []
    for issue in issues:
        uid = issue.get("segment_uid")
        if uid is None:
            result.append(issue)
        elif uid in live_uids:
            result.append(issue)
    return result
