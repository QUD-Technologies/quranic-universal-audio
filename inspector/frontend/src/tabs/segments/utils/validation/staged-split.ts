/**
 * Staged split — pure helpers that turn a sidecar auto-split entry into the
 * N display pieces a card renders BEFORE the split is dispatched.
 *
 * `resolveStagedSplit` is the eligibility gate: anything short of a clean,
 * in-range sidecar entry of an accepted kind answers `null`, and the card
 * falls back to the classic single row with its Auto Split / Split button.
 * `stagedSplitFor` picks the source per card: a cross-verse card stages the
 * `cross_verse` entry of the Auto Split map; a missed-waqf card stages its own
 * item's `boundary` (cursors + refs), where a WASL pick means "no stop here"
 * and only the boundaries picked WAQF are cut (`waqfOnlySplit`).
 *
 * `buildStagedChildren` slices the parent exactly as `_reduceSplit` would
 * (piece 0 keeps the parent uid + index; later pieces take the memoised
 * child uids), so the staged rows and the committed rows look identical.
 * Pieces carry `_staged: true` so `SegmentRow` can drop edit affordances.
 */

import { get } from 'svelte/store';

import { quranRefs } from '../../../../lib/refs/quran-refs';
import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../lib/types/view-models';
import type { AutoSplitMap } from '../../stores/auto-split';
import type { StagedPick } from '../../stores/staged-split';
import { dkTextForRef, getVerseWordCounts, isCrossVerse } from '../data/references';

export interface StagedSplit {
    cursors: number[];
    refs: string[];
}

/** Also the categories whose card stages a split with a WASL / WAQF pick per boundary. */
export type StagedKind = 'cross_verse' | 'missed_waqf';
export const CROSS_VERSE_KINDS: readonly StagedKind[] = ['cross_verse'];
export const MISSED_WAQF_KINDS: readonly StagedKind[] = ['missed_waqf'];

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

interface ItemBoundary {
    cursors?: number[];
    refs?: string[] | null;
}

function _itemBoundary(item: SegValAnyItem | null): ItemBoundary | null {
    return (item as { boundary?: ItemBoundary } | null)?.boundary ?? null;
}

/** Number of proposed cuts on a missed-waqf item (0 when it carries none). */
export function itemCursorCount(item: SegValAnyItem | null): number {
    const cursors = _itemBoundary(item)?.cursors;
    return Array.isArray(cursors) ? cursors.length : 0;
}

/** The staged split a `category` card shows for `seg` (the item's root), or null. */
export function stagedSplitFor(
    category: StagedKind,
    seg: Segment | null,
    item: SegValAnyItem | null,
    map: AutoSplitMap | null,
): StagedSplit | null {
    if (category === 'cross_verse') return resolveStagedSplit(seg, map, CROSS_VERSE_KINDS);
    const uid = seg?.segment_uid;
    const b = _itemBoundary(item);
    if (!uid || !Array.isArray(b?.cursors) || !Array.isArray(b?.refs)) return null;
    const entry = { cursors: b.cursors, refs: b.refs, kind: 'missed_waqf' as const };
    return resolveStagedSplit(seg, { [uid]: entry }, MISSED_WAQF_KINDS);
}

/** Session-pick key: one seg can sit in both staged accordions with different cuts. */
export function stagedPickKey(category: StagedKind, uid: string): string {
    return category === 'cross_verse' ? uid : `${category}:${uid}`;
}

function joinRefs(a: string, b: string): string {
    return `${a.split('-')[0]}-${b.split('-').pop()}`;
}

/**
 * Only the boundaries picked WAQF (`false`, or unanswered) are cut; a WASL
 * pick merges its two pieces back into one ref. `null` when no boundary is cut.
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

export type StagedCommit =
    | { kind: 'none' }
    | { kind: 'split'; split: StagedSplit; wasls: boolean[]; newUids: string[] };

/**
 * What a `category` card commits from its picks so far. `childUids` are the
 * staged pieces' uids (pieces 1..N).
 *
 * Cross-verse cuts every boundary, carrying each pick as `is_wasl`
 * (unanswered commits as WAQF; the card keeps asking).
 *
 * Missed-waqf cuts only the boundaries answered WAQF: a WASL or unanswered
 * cut is a suspect that never becomes a split. Each committed piece keeps the
 * uid of the staged piece that starts where it starts, so those rows keep
 * their identity. `none` when no cut is answered WAQF.
 */
export function stagedCommit(
    category: StagedKind,
    staged: StagedSplit,
    picks: readonly StagedPick[],
    childUids: readonly string[],
): StagedCommit {
    if (category === 'cross_verse') {
        const wasls = staged.cursors.map((_, i) => picks[i] === true);
        return { kind: 'split', split: staged, wasls, newUids: childUids.slice() };
    }
    const kept = staged.cursors.map((_, i) => picks[i] === false);
    const split = waqfOnlySplit(staged, kept.map((k) => !k));
    if (!split) return { kind: 'none' };
    const newUids = childUids.filter((_, i) => kept[i]);
    return { kind: 'split', split, wasls: split.cursors.map(() => false), newUids };
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
