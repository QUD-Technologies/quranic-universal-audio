/**
 * Staged split — pure helpers that turn a sidecar auto-split entry into the
 * N display pieces a cross-verse card renders BEFORE the split is dispatched.
 *
 * `resolveStagedSplit` is the eligibility gate: anything short of a clean,
 * in-range, cross_verse sidecar entry answers `null`, and the card falls back
 * to the classic single row with its Auto Split / Split button.
 *
 * `buildStagedChildren` slices the parent exactly as `_reduceSplit` would
 * (piece 0 keeps the parent uid + index; later pieces take the memoised
 * child uids), so the staged rows and the committed rows look identical.
 * Pieces carry `_staged: true` so `SegmentRow` can drop edit affordances.
 */

import { get } from 'svelte/store';

import { quranRefs } from '../../../../lib/refs/quran-refs';
import type { Segment } from '../../../../lib/types/view-models';
import type { AutoSplitMap } from '../../stores/auto-split';
import type { StagedPick } from '../../stores/staged-split';
import { dkTextForRef, getVerseWordCounts, isCrossVerse } from '../data/references';

export interface StagedSplit {
    cursors: number[];
    refs: string[];
}

export type StagedSegment = Segment & { _staged: true };

export function isStagedSegment(seg: Segment): seg is StagedSegment {
    return (seg as { _staged?: boolean })._staged === true;
}

/** Sidecar entry usable as a pre-applied split for `seg`, else `null`. */
export function resolveStagedSplit(seg: Segment | null, map: AutoSplitMap | null): StagedSplit | null {
    if (!seg || !map) return null;
    const uid = seg.segment_uid;
    if (!uid || !isCrossVerse(seg.matched_ref)) return null;
    const entry = map[uid];
    if (!entry || entry.kind !== 'cross_verse') return null;
    const { cursors, refs } = entry;
    if (!Array.isArray(cursors) || !Array.isArray(refs)) return null;
    if (cursors.length < 1 || refs.length !== cursors.length + 1) return null;
    for (let i = 0; i < cursors.length; i++) {
        const c = cursors[i]!;
        if (!Number.isFinite(c) || c <= seg.time_start || c >= seg.time_end) return null;
        if (i > 0 && c <= cursors[i - 1]!) return null;
    }
    if (refs.some((r) => typeof r !== 'string' || !r)) return null;
    return { cursors: cursors.slice(), refs: refs.slice() };
}

/**
 * Display pieces for a staged split. `childUids` has `cursors.length` entries
 * (pieces 1..N); `picks` (optional) sets `is_wasl` on every non-last piece.
 */
export function buildStagedChildren(
    seg: Segment,
    staged: StagedSplit,
    childUids: readonly string[],
    picks?: readonly StagedPick[],
): StagedSegment[] {
    const { cursors, refs } = staged;
    const n = cursors.length + 1;
    const dk = get(quranRefs)?.dk_words;
    const vwc = getVerseWordCounts();
    const out: StagedSegment[] = [];
    for (let i = 0; i < n; i++) {
        const piece: StagedSegment = {
            ...seg,
            time_start: i === 0 ? seg.time_start : cursors[i - 1]!,
            time_end: i === n - 1 ? seg.time_end : cursors[i]!,
            matched_ref: refs[i]!,
            _staged: true,
        };
        delete piece.wrap_word_ranges;
        delete piece._derived;
        delete piece.silence_after_ms;
        delete piece.silence_after_raw_ms;
        piece.matched_text = dkTextForRef(refs[i]!, dk, vwc);
        if (i > 0) piece.segment_uid = childUids[i - 1];
        if (i < n - 1) {
            const pick = picks?.[i];
            piece.is_wasl = pick === undefined ? null : pick;
        } else {
            piece.is_wasl = seg.is_wasl === true;
        }
        out.push(piece);
    }
    return out;
}
