/**
 * Boundary state of a cross-verse item — one label per verse boundary.
 *
 * Feeds the cross-verse accordion's Unset · Wasl · Waqf chips (counts are
 * boundaries, so a three-verse item contributes two) and the filter that
 * hides or shows items by the states they contain.
 *
 * Three sources, checked in order:
 *   1. a committed / in-progress split (≥2 live members) — read each left
 *      member's `is_wasl`; a uid still awaiting its post-split pick is unset;
 *   2. a staged split (sidecar entry, not dispatched) — read the session picks;
 *   3. neither — the item is one unsplit compound seg: a single unset boundary.
 */

import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { EditOp, Segment } from '../../../../lib/types/view-models';
import type { AutoSplitMap } from '../../stores/auto-split';
import type { StagedPicks } from '../../stores/staged-split';
import { parseSegRef } from '../data/references';
import { getSplitGroupMembers } from './split-group';
import { resolveStagedSplit } from './staged-split';

export type BoundaryState = 'unset' | 'wasl' | 'waqf';
export const BOUNDARY_STATES: readonly BoundaryState[] = ['unset', 'wasl', 'waqf'];

export interface BoundaryCtx {
    chapterSegs: (chapter: number) => Segment[];
    opLog: (chapter: number) => readonly EditOp[];
    splitGroupIndex: Record<string, string[]>;
    pendingWasl: ReadonlySet<string>;
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

function _memberState(left: Segment, pending: ReadonlySet<string>): BoundaryState {
    const uid = left.segment_uid;
    if (uid && pending.has(uid)) return 'unset';
    return left.is_wasl === true ? 'wasl' : 'waqf';
}

export function boundaryStates(item: SegValAnyItem, ctx: BoundaryCtx): BoundaryState[] {
    const uid = (item as { segment_uid?: string | null }).segment_uid ?? null;
    const chapter = (item as { chapter?: number }).chapter;
    if (!uid || chapter == null) return ['unset'];
    const segs = ctx.chapterSegs(chapter);
    const members = getSplitGroupMembers(uid, segs, ctx.splitGroupIndex[uid], ctx.opLog(chapter));
    if (members.length >= 2) {
        const out: BoundaryState[] = [];
        for (let i = 0; i < members.length - 1; i++) {
            if (!isVerseBoundary(members[i]!, members[i + 1]!)) continue;
            out.push(_memberState(members[i]!, ctx.pendingWasl));
        }
        return out.length ? out : ['unset'];
    }
    const root = segs.find((s) => s.segment_uid === uid) ?? null;
    const staged = resolveStagedSplit(root, ctx.autoSplitMap);
    if (staged) {
        const picks = ctx.stagedPicks[uid] ?? [];
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
): Record<BoundaryState, number> {
    const counts: Record<BoundaryState, number> = { unset: 0, wasl: 0, waqf: 0 };
    for (const it of items) for (const s of boundaryStates(it, ctx)) counts[s] += 1;
    return counts;
}

/** Items with at least one boundary in a selected state. */
export function filterByBoundaryStates(
    items: readonly SegValAnyItem[],
    selected: ReadonlySet<BoundaryState>,
    ctx: BoundaryCtx,
): SegValAnyItem[] {
    if (selected.size === 0 || selected.size === BOUNDARY_STATES.length) return items.slice();
    return items.filter((it) => boundaryStates(it, ctx).some((s) => selected.has(s)));
}
