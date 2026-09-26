/**
 * Boundary state of a staged-split item (cross-verse or missed-waqf) — one
 * label per boundary.
 *
 * Feeds the accordion's Unset · Wasl · Waqf chips (counts are boundaries, so
 * a three-verse item contributes two) and the filter that hides or shows
 * items by the states they contain.
 *
 * Sources, checked in order:
 *   1. a committed / in-progress split (≥2 live members): each left member's
 *      `is_wasl` (a uid still awaiting its post-split pick, or whose answer is
 *      re-asked, is unset), on every verse join for cross-verse and on every
 *      join for missed-waqf;
 *   2. missed-waqf only: the root is ignored for the category — every
 *      proposed cut reads as wasl;
 *   3. a staged split (not dispatched) — read the session picks;
 *   4. none of these — one unset boundary.
 */

import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { EditOp, Segment } from '../../../../lib/types/view-models';
import type { AutoSplitMap } from '../../stores/auto-split';
import type { StagedPicks } from '../../stores/staged-split';
import { parseSegRef } from '../data/references';
import { isIgnoredFor } from './classified-issues';
import { getSplitGroupMembers } from './split-group';
import { itemCursorCount, type StagedKind, stagedPickKey, stagedSplitFor } from './staged-split';

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

function _memberState(left: Segment, ctx: BoundaryCtx): BoundaryState {
    const uid = left.segment_uid;
    if (uid && (ctx.pendingWasl.has(uid) || ctx.waslRecheck.has(uid))) return 'unset';
    return left.is_wasl === true ? 'wasl' : 'waqf';
}

function _committedStates(
    category: StagedKind,
    members: Segment[],
    ctx: BoundaryCtx,
): BoundaryState[] {
    const out: BoundaryState[] = [];
    for (let i = 0; i < members.length - 1; i++) {
        if (category === 'cross_verse' && !isVerseBoundary(members[i]!, members[i + 1]!)) continue;
        out.push(_memberState(members[i]!, ctx));
    }
    return out.length ? out : ['unset'];
}

export function boundaryStates(
    item: SegValAnyItem,
    ctx: BoundaryCtx,
    category: StagedKind = 'cross_verse',
): BoundaryState[] {
    const uid = (item as { segment_uid?: string | null }).segment_uid ?? null;
    const chapter = (item as { chapter?: number }).chapter;
    if (!uid || chapter == null) return ['unset'];
    const segs = ctx.chapterSegs(chapter);
    const members = getSplitGroupMembers(uid, segs, ctx.splitGroupIndex[uid], ctx.opLog(chapter));
    if (members.length >= 2) return _committedStates(category, members, ctx);
    const root = segs.find((s) => s.segment_uid === uid) ?? null;
    if (category === 'missed_waqf' && root && isIgnoredFor(root, category)) {
        return new Array<BoundaryState>(Math.max(1, itemCursorCount(item))).fill('wasl');
    }
    const staged = stagedSplitFor(category, root, item, ctx.autoSplitMap);
    if (staged) {
        const picks = ctx.stagedPicks[stagedPickKey(category, uid)] ?? [];
        return staged.cursors.map((_, i) => {
            const p = picks[i];
            return p === undefined ? 'unset' : p ? 'wasl' : 'waqf';
        });
    }
    return ['unset'];
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
