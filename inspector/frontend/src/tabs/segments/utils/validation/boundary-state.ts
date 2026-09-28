/**
 * Boundary state of a staged-split item (cross-verse or missed-waqf) — one
 * label per boundary.
 *
 * Feeds the accordion's Unset · Wasl · Waqf chips (counts are boundaries, so
 * a three-verse item contributes two) and the filter that hides or shows
 * items by the states they contain.
 *
 * Join verdicts record the cut and ignore answers. A pending recheck overrides
 * an edge answer. Cross-verse session picks apply before committing; saved
 * boundaries use their verdict or the segment's is_wasl flag.
 */

import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { EditOp, Segment } from '../../../../lib/types/view-models';
import type { AutoSplitMap } from '../../stores/auto-split';
import type { StagedPicks } from '../../stores/staged-split';
import { parseSegRef } from '../data/references';
import { edgeState } from '../../domain/join-verdict';
import { reviewBoundary, reviewStates } from './join-review';
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

function _memberState(left: Segment, ctx: BoundaryCtx): BoundaryState {
    const uid = left.segment_uid;
    if (uid && (ctx.pendingWasl.has(uid) || ctx.waslRecheck.has(uid))) return 'unset';
    const explicit = edgeState(left);
    return explicit === 'unset' ? (left.is_wasl === true ? 'wasl' : 'waqf') : explicit;
}

function _committedStates(
    members: Segment[],
    ctx: BoundaryCtx,
): BoundaryState[] {
    const out: BoundaryState[] = [];
    for (let i = 0; i < members.length - 1; i++) {
        if (!isVerseBoundary(members[i]!, members[i + 1]!)) continue;
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
    if (category === 'missed_waqf') {
        const boundary = reviewBoundary(item);
        if (boundary) {
            const saved = reviewStates(members, boundary, ctx.waslRecheck);
            const picks = ctx.stagedPicks[stagedPickKey(category, uid)] ?? [];
            return saved.map((s, i) => picks[i] === undefined ? s : picks[i] ? 'wasl' : 'waqf');
        }
    }
    if (members.length >= 2) return _committedStates(members, ctx);
    const root = segs.find((s) => s.segment_uid === uid) ?? null;
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
