/**
 * Staged split — pure helpers that turn a sidecar auto-split entry into the
 * N display pieces a card renders BEFORE the split is dispatched.
 *
 * `resolveStagedSplit` is the eligibility gate: anything short of a clean,
 * in-range sidecar entry of an accepted kind answers `null`, and the card
 * falls back to the classic single row with its Auto Split / Split button.
 * A cross-verse card stages `cross_verse` entries; a hidden-pause card stages
 * `hidden_pause` entries, where a WASL pick means "no pause here" and only
 * the boundaries picked WAQF are cut (`waqfOnlySplit`).
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

export type StagedKind = 'cross_verse' | 'hidden_pause';
export const CROSS_VERSE_KINDS: readonly StagedKind[] = ['cross_verse'];
export const HIDDEN_PAUSE_KINDS: readonly StagedKind[] = ['hidden_pause'];

export type StagedSegment = Segment & { _staged: true };

export function isStagedSegment(seg: Segment): seg is StagedSegment {
    return (seg as { _staged?: boolean })._staged === true;
}

/** Sidecar entry of one of `kinds` usable as a pre-applied split for `seg`, else `null`. */
export function resolveStagedSplit(
    seg: Segment | null,
    map: AutoSplitMap | null,
    kinds: readonly StagedKind[] = CROSS_VERSE_KINDS,
): StagedSplit | null {
    if (!seg || !map) return null;
    const uid = seg.segment_uid;
    if (!uid) return null;
    const entry = map[uid];
    if (!entry || !(kinds as readonly string[]).includes(entry.kind)) return null;
    if (entry.kind === 'cross_verse' && !isCrossVerse(seg.matched_ref)) return null;
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

function joinRefs(a: string, b: string): string {
    return `${a.split('-')[0]}-${b.split('-').pop()}`;
}

/**
 * The split a hidden-pause card commits: only the boundaries picked WAQF
 * (`false`, or unanswered) are cut; a WASL pick merges its two pieces back
 * into one ref. `null` when no boundary is cut.
 */
export function waqfOnlySplit(staged: StagedSplit, picks: readonly StagedPick[]): StagedSplit | null {
    const cursors: number[] = [];
    const refs: string[] = [];
    let open = staged.refs[0]!;
    for (let i = 0; i < staged.cursors.length; i++) {
        const next = staged.refs[i + 1]!;
        if (picks[i] === true) {
            open = joinRefs(open, next);
            continue;
        }
        cursors.push(staged.cursors[i]!);
        refs.push(open);
        open = next;
    }
    refs.push(open);
    return cursors.length ? { cursors, refs } : null;
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
