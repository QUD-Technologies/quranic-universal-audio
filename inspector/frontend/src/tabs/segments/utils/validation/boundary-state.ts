/**
 * Boundary state of a staged-split item (cross-verse or missed-waqf) — one
 * label per boundary.
 *
 * Feeds the accordion's Unset · Wasl · Waqf chips (counts are boundaries, so
 * a three-verse item contributes two) and the filter that hides or shows
 * items by the states they contain.
 *
 * A Missed Waqf item asks its cut words; a cross-verse item asks every verse end
 * across its pieces. Each word reads its answer by word among the item's own
 * rendition (`occurrencePieces`, `wordAnswer`), so splits,
 * trims and merges on top of an answer never lose it. A settled item with no word
 * left to ask has no boundary.
 */

import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { EditOp, Segment } from '../../../../lib/types/view-models';
import type { AutoSplitMap } from '../../stores/auto-split';
import type { StagedPicks } from '../../stores/staged-split';
import { getVerseWordCounts, parseSegRef, verseEndsIn } from '../data/references';
import { isIgnoredFor } from './classified-issues';
import { endRef, occurrencePieces, wordAnswer } from '../../domain/join-verdict';
import { reviewBoundary, reviewMembers } from './join-review';
import { getSplitGroupMembers } from './split-group';
import { type StagedKind, stagedPickKey, stagedSplitFor } from './staged-split';

export type BoundaryState = 'unset' | 'wasl' | 'waqf';
export const BOUNDARY_STATES: readonly BoundaryState[] = ['unset', 'wasl', 'waqf'];

export interface BoundaryCtx {
    chapterSegs: (chapter: number) => Segment[];
    opLog: (chapter: number) => readonly EditOp[];
    splitGroupIndex: Record<string, string[]>;
    pendingWasl: ReadonlySet<string>;
    /** Left-piece uids whose WASL / WAQF answer is re-asked (`wasl_recheck`). */
    waslRecheck: ReadonlySet<string>;
    autoSplitMap: AutoSplitMap | null;
    stagedPicks: StagedPicks;
}

/** True when the join between `a` and `b` crosses a verse boundary. */
export function isVerseBoundary(a: Segment, b: Segment): boolean {
    const pa = parseSegRef(a.matched_ref);
    const pb = parseSegRef(b.matched_ref);
    if (!pa || !pb) return false;
    return pa.surah !== pb.surah || pa.ayah_to !== pb.ayah_from;
}

/** Every word end a `category` item asks, in order: a Missed Waqf item's cut words, a
 *  cross-verse item's verse ends across its pieces. */
function _askedWords(item: SegValAnyItem, members: readonly Segment[], root: Segment | null, category: StagedKind): string[] {
    if (category === 'missed_waqf') {
        const boundary = reviewBoundary(item);
        return boundary ? boundary.refs.slice(0, boundary.cursors.length).map(endRef) : [];
    }
    const pieces = members.length ? members : root ? [root] : [];
    const ref = pieces.length
        ? `${pieces[0]!.matched_ref.split('-')[0]}-${endRef(pieces[pieces.length - 1]!.matched_ref)}`
        : (item as { ref?: string }).ref;
    return verseEndsIn(ref, getVerseWordCounts());
}

/** Session picks on the card's staged cuts, by word. */
function _picksByWord(item: SegValAnyItem, root: Segment | null, ctx: BoundaryCtx, category: StagedKind, uid: string) {
    const picks = ctx.stagedPicks[stagedPickKey(category, uid)] ?? [];
    const staged = stagedSplitFor(category, root, item, ctx.autoSplitMap);
    const out = new Map<string, boolean>();
    staged?.refs.slice(0, staged.cursors.length).forEach((ref, i) => {
        if (picks[i] !== undefined) out.set(endRef(ref), picks[i]!);
    });
    return out;
}

/**
 * One state per asked word: a session pick, else the word's answer among the item's
 * own pieces (`occurrencePieces`, `wordAnswer`). A settled item (resolved, or its root ignored) reads its
 * unanswered words WASL. Pieces awaiting a WASL confirmation answer nothing at their end.
 */
export function boundaryStates(
    item: SegValAnyItem,
    ctx: BoundaryCtx,
    category: StagedKind = 'cross_verse',
): BoundaryState[] {
    const uid = (item as { segment_uid?: string | null }).segment_uid ?? null;
    const chapter = (item as { chapter?: number }).chapter;
    if (!uid || chapter == null) return ['unset'];
    const segs = ctx.chapterSegs(chapter);
    const root = segs.find((s) => s.segment_uid === uid) ?? null;
    const members = getSplitGroupMembers(uid, segs, ctx.splitGroupIndex[uid], ctx.opLog(chapter));
    const own = category === 'missed_waqf' ? reviewMembers(members, reviewBoundary(item)) : members;
    const words = _askedWords(item, own, root, category);
    const settled = (item as { resolved?: boolean }).resolved === true
        || (root !== null && isIgnoredFor(root, category));
    // A settled item whose pieces no longer hold a verse end has nothing left to ask.
    if (!words.length) return settled ? [] : ['unset'];
    const picks = _picksByWord(item, root, ctx, category, uid);
    const waiting = new Set([...ctx.waslRecheck, ...ctx.pendingWasl]);
    const pieces = occurrencePieces(segs, members);
    return words.map((word) => {
        const pick = picks.get(word);
        if (pick !== undefined) return pick ? 'wasl' : 'waqf';
        const state = wordAnswer(pieces, word, waiting);
        return state === 'unset' && settled ? 'wasl' : state;
    });
}

/** Boundary totals over `items`. */
export function countBoundaryStates(
    items: readonly SegValAnyItem[],
    ctx: BoundaryCtx,
    category: StagedKind = 'cross_verse',
): Record<BoundaryState, number> {
    const counts: Record<BoundaryState, number> = { unset: 0, wasl: 0, waqf: 0 };
    for (const it of items) for (const s of boundaryStates(it, ctx, category)) counts[s] += 1;
    return counts;
}

/** Items with at least one boundary in a selected state. */
export function filterByBoundaryStates(
    items: readonly SegValAnyItem[],
    selected: ReadonlySet<BoundaryState>,
    ctx: BoundaryCtx,
    category: StagedKind = 'cross_verse',
): SegValAnyItem[] {
    if (selected.size === 0 || selected.size === BOUNDARY_STATES.length) return items.slice();
    return items.filter((it) => boundaryStates(it, ctx, category).some((s) => selected.has(s)));
}
