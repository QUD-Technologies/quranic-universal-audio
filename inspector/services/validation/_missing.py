"""Missing-words detection helper.

Extracted from validate_reciter_segments. Given the verse_segments map
(built during the main pass), produces the missing_words detail list.
"""

from __future__ import annotations

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from services.storage.data_loader import word_has_stop


def _issue_chapter(surah: int, gap_refs: set[tuple[int, int]]) -> int:
    """The chapter a missing-words issue opens in: the verse's surah when one of its
    gap segments is there, else the (earliest) chapter whose audio holds them."""
    chapters = {ch for ch, _ in gap_refs}
    if not chapters or surah in chapters:
        return surah
    return min(chapters)


def _build_missing_words(
    verse_segments: dict[tuple[int, int], list],
    word_counts: dict[tuple[int, int], int],
    sequence_gaps: list[dict] | None = None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
) -> list[dict]:
    """Build a list of missing-word issue dicts from the verse coverage map.

    verse_segments maps (surah, ayah) → [(word_from, word_to, seg_index, chapter), ...].
    seg_index counts within ``chapter``, the chapter whose audio holds the segment.
    That is usually the verse's surah, but not always: a chapter file that runs into
    the next surah's opening carries a segment of it. The issue points at the chapter
    its gap segments sit in, and auto-fix only offers to stretch a segment in the
    verse's own chapter (stretching a spilled one would claim the wrong audio).
    sequence_gaps carries local audio-order jumps whose skipped words may be
    covered later by a valid backtrack/repetition.
    """
    missing_words = []
    emitted_ranges: set[tuple[str, tuple[int, ...]]] = set()
    for (surah, ayah), seg_list in verse_segments.items():
        expected = word_counts.get((surah, ayah))
        if not expected:
            continue
        seg_list.sort(key=lambda x: x[0])
        covered = set()
        for wf, wt, _, _ in seg_list:
            covered.update(range(wf, wt + 1))
        missing = set(range(1, expected + 1)) - covered
        if not missing:
            continue

        gap_refs: set[tuple[int, int]] = set()  # (chapter, seg_index)
        for j in range(len(seg_list)):
            wf, wt, idx, ch = seg_list[j]
            if j + 1 < len(seg_list):
                next_wf, _, next_idx, next_ch = seg_list[j + 1]
                if next_wf > wt + 1:
                    gap_refs.add((ch, idx))
                    gap_refs.add((next_ch, next_idx))
            if j == len(seg_list) - 1 and wt < expected:
                gap_refs.add((ch, idx))
            if j == 0 and wf > 1:
                gap_refs.add((ch, idx))
        issue_chapter = _issue_chapter(surah, gap_refs)
        gap_indices = {idx for ch, idx in gap_refs if ch == issue_chapter}

        auto_fix = None
        own_audio = all(ch == surah for *_, ch in seg_list)
        if len(missing) == 1 and own_audio:
            mw = next(iter(missing))
            first_wf, first_wt, first_idx, _ = seg_list[0]
            last_wf, last_wt, last_idx, _ = seg_list[-1]

            if mw == 1 and first_wf > 1:
                auto_fix = {
                    "target_seg_index": first_idx,
                    "new_ref_start": f"{surah}:{ayah}:1",
                    "new_ref_end": f"{surah}:{ayah}:{first_wt}",
                }
            elif mw == expected and last_wt < expected:
                auto_fix = {
                    "target_seg_index": last_idx,
                    "new_ref_start": f"{surah}:{ayah}:{last_wf}",
                    "new_ref_end": f"{surah}:{ayah}:{expected}",
                }
            else:
                for j in range(len(seg_list) - 1):
                    wf, wt, idx, _ = seg_list[j]
                    next_wf, next_wt, next_idx, _ = seg_list[j + 1]
                    if wt + 1 == mw and mw + 1 == next_wf:
                        if word_has_stop(surah, ayah, wt, riwayah):
                            auto_fix = {
                                "target_seg_index": next_idx,
                                "new_ref_start": f"{surah}:{ayah}:{mw}",
                                "new_ref_end": f"{surah}:{ayah}:{next_wt}",
                            }
                        elif word_has_stop(surah, ayah, mw, riwayah):
                            auto_fix = {
                                "target_seg_index": idx,
                                "new_ref_start": f"{surah}:{ayah}:{wf}",
                                "new_ref_end": f"{surah}:{ayah}:{mw}",
                            }
                        else:
                            auto_fix_up = {
                                "target_seg_index": idx,
                                "new_ref_start": f"{surah}:{ayah}:{wf}",
                                "new_ref_end": f"{surah}:{ayah}:{mw}",
                            }
                            auto_fix_down = {
                                "target_seg_index": next_idx,
                                "new_ref_start": f"{surah}:{ayah}:{mw}",
                                "new_ref_end": f"{surah}:{ayah}:{next_wt}",
                            }
                        break

        issue: dict = {
            "verse_key": f"{surah}:{ayah}",
            "chapter": issue_chapter,
            "segment_uid": None,
            "msg": f"missing words: {sorted(missing)}",
            "missing_words": sorted(missing),
            "seg_indices": sorted(gap_indices),
        }
        emitted_ranges.add((issue["verse_key"], tuple(issue["missing_words"])))
        if auto_fix:
            issue["auto_fix"] = auto_fix
        if "auto_fix_up" in locals():
            issue["auto_fix_up"] = auto_fix_up
            del auto_fix_up
        if "auto_fix_down" in locals():
            issue["auto_fix_down"] = auto_fix_down
            del auto_fix_down
        missing_words.append(issue)

    for gap in sequence_gaps or []:
        words = sorted(gap.get("missing_words") or [])
        if not words:
            continue
        key: tuple[str, tuple[int, ...]] = (gap["verse_key"], tuple(words))
        if key in emitted_ranges:
            continue
        issue = {
            "verse_key": gap["verse_key"],
            "chapter": gap["chapter"],
            "segment_uid": None,
            "msg": f"missing words in sequence: {words}",
            "missing_words": words,
            "seg_indices": sorted(set(gap.get("seg_indices") or [])),
            "sequence_gap": True,
        }
        emitted_ranges.add(key)
        missing_words.append(issue)

    def _sort_key(item: dict) -> tuple[int, int, int, int]:
        verse_key = item.get("verse_key", "")
        try:
            surah_s, ayah_s = verse_key.split(":", 1)
            surah = int(surah_s)
            ayah = int(ayah_s)
        except (ValueError, AttributeError):
            surah = int(item.get("chapter") or 0)
            ayah = 0
        words = item.get("missing_words") or []
        first_word = min(words) if words else 0
        seg_indices = item.get("seg_indices") or []
        first_seg = min(seg_indices) if seg_indices else 0
        return surah, ayah, first_word, first_seg

    return sorted(missing_words, key=_sort_key)
