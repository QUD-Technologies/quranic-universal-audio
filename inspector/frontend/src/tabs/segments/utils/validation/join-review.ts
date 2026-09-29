/**
 * Project candidate joins onto live pieces; resolved cuts and uncut cursors carry answers.
 * A cut opened to its silence leaves the cursor in the gap: its owner is the piece
 * ending on the cut's word, and answers are matched by that word, not the cursor.
 */
import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../lib/types/view-models';
import { endRef } from '../../domain/join-verdict';
import { type StagedSplit } from './staged-split';

export function reviewBoundary(item: SegValAnyItem): StagedSplit | null {
    const b = (item as { boundary?: Partial<StagedSplit> }).boundary;
    if (!b?.cursors?.length || !b.refs || b.refs.length !== b.cursors.length + 1) return null;
    if (b.refs.some((ref) => !/^\d+:\d+:\d+-\d+:\d+:\d+$/.test(ref))) return null;
    if (b.cursors.some((c, i) => !Number.isFinite(c) || (i > 0 && c <= b.cursors![i - 1]!))) return null;
    return { cursors: b.cursors, refs: b.refs };
}

/**
 * Each cut's answer, by its word: a verdict stored on any piece, else a piece ending
 * on that word (a boundary already made there: WASL when marked so, else WAQF).
 * A piece under WASL recheck answers nothing at its end.
 */
export function reviewStates(members: readonly Segment[], boundary: StagedSplit, recheck: ReadonlySet<string> = new Set()) {
    return boundary.refs.slice(0, boundary.cursors.length).map((ref) => {
        const after = endRef(ref);
        for (const s of members) {
            const answer = s.join_verdicts?.find((j) => j.after_ref === after);
            if (answer && !(recheck.has(s.segment_uid ?? '') && answer.at_ms === s.time_end)) return answer.verdict;
        }
        const edge = members.find((s) => WORD_SPAN.test(s.matched_ref) && endRef(s.matched_ref) === after);
        if (!edge || recheck.has(edge.segment_uid ?? '')) return 'unset';
        return edge.is_wasl ? 'wasl' : 'waqf';
    });
}

const WORD_SPAN = /^\d+:\d+:\d+-\d+:\d+:\d+$/;

/**
 * The split-group pieces inside the item's own words. The group also holds the
 * splits that made the reviewed segment (a cross-verse or low-confidence cut),
 * which are not answers to its cursors.
 */
export function reviewMembers(members: readonly Segment[], boundary: StagedSplit | null): Segment[] {
    if (!boundary) return [...members];
    const first = boundary.refs[0]!.split('-')[0]!;
    const last = endRef(boundary.refs[boundary.refs.length - 1]!);
    return members.filter((s) => {
        if (!WORD_SPAN.test(s.matched_ref)) return false;
        const [from, to] = s.matched_ref.split('-') as [string, string];
        return compareRef(from, first) >= 0 && compareRef(to, last) <= 0;
    });
}

function compareRef(a: string, b: string): number {
    const x = a.split(':').map(Number), y = b.split(':').map(Number);
    for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i]! - y[i]!;
    return 0;
}

/** A stale sidecar must not split a segment whose reference has changed. */
export function reviewJoinOwner(members: readonly Segment[], boundary: StagedSplit, i: number): Segment | undefined {
    const at = boundary.cursors[i]!;
    const after = endRef(boundary.refs[i]!);
    const owner = members.find((s) => s.time_start < at && at <= s.time_end)
        ?? [...members].reverse()
            .find((s) => s.time_end < at && WORD_SPAN.test(s.matched_ref) && endRef(s.matched_ref) === after);
    if (!owner || !/^\d+:\d+:\d+-\d+:\d+:\d+$/.test(owner.matched_ref)) return undefined;
    if (compareRef(after, owner.matched_ref.split('-')[0]!) < 0 || compareRef(after, endRef(owner.matched_ref)) > 0) return undefined;
    if (at >= owner.time_end && after !== endRef(owner.matched_ref)) return undefined;
    if (at < owner.time_end && compareRef(boundary.refs[i + 1]!.split('-')[0]!, endRef(owner.matched_ref)) > 0) return undefined;
    return owner;
}
