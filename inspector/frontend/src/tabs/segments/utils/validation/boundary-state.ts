/**
 * Boundary state of a staged-split item (cross-verse or missed-waqf) — one
 * label per boundary.
 *
 * Feeds the accordion's Unset · Wasl · Waqf chips (counts are boundaries, so
 * a three-verse item contributes two) and the filter that hides or shows
 * items by the states they contain.
 *
 * A cross-verse segment reads each inner verse end from its item's `verse_joins`.
 * A Missed Waqf item reads only the split pieces inside its own words.
 * Join verdicts record the cut and ignore answers; a Missed Waqf item answered
 * before verdicts existed reads its cuts as waqf and its other cursors (or an
 * ignored root) as wasl. A pending recheck overrides
 * an edge answer. Cross-verse session picks apply before committing; saved
 * boundaries use their verdict or the segment's is_wasl flag.
 */

import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { EditOp, Segment } from '../../../../lib/types/view-models';
import type { AutoSplitMap } from '../../stores/auto-split';
import type { StagedPicks } from '../../stores/staged-split';
import { parseSegRef } from '../data/references';
import { isIgnoredFor } from './classified-issues';
import { edgeState } from '../../domain/join-verdict';
import { reviewBoundary, reviewMembers, reviewStates } from './join-review';
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

/**
 * A Missed Waqf answer saved without verdicts: its cuts stop, the item's other cursors and an
 * ignored root continue. A split counts only when the server resolved the item from its card —
 * the split group also holds splits made elsewhere (verse-end auto splits).
 */
function _answeredMissedWaqf(item: SegValAnyItem, members: Segment[], root: Segment | null): BoundaryState[] | null {
    const n = Math.max(1, itemCursorCount(item));
    const resolved = (item as { resolved?: boolean }).resolved === true;
    if (members.length >= 2 && resolved) {
        const cuts = Math.min(members.length - 1, n);
        return [..._repeat('waqf', cuts), ..._repeat('wasl', n - cuts)];
    }
    return root && isIgnoredFor(root, 'missed_waqf') ? _repeat('wasl', n) : null;
}

function _repeat(state: BoundaryState, n: number): BoundaryState[] {
    return new Array<BoundaryState>(n).fill(state);
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
    const root = segs.find((s) => s.segment_uid === uid) ?? null;
    if (category === 'missed_waqf') {
        const boundary = reviewBoundary(item);
        const own = reviewMembers(members, boundary);
        const answered = _answeredMissedWaqf(item, own, root);
        if (boundary) {
            const saved = reviewStates(own, boundary, ctx.waslRecheck);
            const picks = ctx.stagedPicks[stagedPickKey(category, uid)] ?? [];
            const base = answered && saved.every((s) => s === 'unset') ? answered : saved;
            return base.map((s, i) => picks[i] === undefined ? s : picks[i] ? 'wasl' : 'waqf');
        }
        if (answered) return answered;
    }
    if (members.length >= 2) return _committedStates(members, ctx);
    const joins = (item as { verse_joins?: { verdict?: 'wasl' | 'waqf' | null }[] }).verse_joins;
    if (category === 'cross_verse' && joins?.length) {
        const picks = ctx.stagedPicks[stagedPickKey(category, uid)] ?? [];
        return joins.map((j, i) => picks[i] === undefined ? j.verdict ?? 'unset' : picks[i] ? 'wasl' : 'waqf');
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
