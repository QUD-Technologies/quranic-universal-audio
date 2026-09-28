/** Project candidate joins onto live pieces; cuts and answers are independent. */
import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../lib/types/view-models';
import { endRef, joinState } from '../../domain/join-verdict';
import { buildStagedChildren, type StagedSplit } from './staged-split';

export function reviewBoundary(item: SegValAnyItem): StagedSplit | null {
    const b = (item as { boundary?: Partial<StagedSplit> }).boundary;
    if (!b?.cursors?.length || !b.refs || b.refs.length !== b.cursors.length + 1) return null;
    if (b.refs.some((ref) => !/^\d+:\d+:\d+-\d+:\d+:\d+$/.test(ref))) return null;
    if (b.cursors.some((c, i) => !Number.isFinite(c) || (i > 0 && c <= b.cursors![i - 1]!))) return null;
    return { cursors: b.cursors, refs: b.refs };
}

export function reviewStates(members: readonly Segment[], boundary: StagedSplit, recheck: ReadonlySet<string> = new Set()) {
    return boundary.cursors.map((at, i) => {
        const owner = reviewJoinOwner(members, boundary, i);
        if (owner?.time_end === at && recheck.has(owner.segment_uid ?? '')) return 'unset';
        return owner ? joinState(owner, at, endRef(boundary.refs[i]!)) : 'unset';
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
    const owner = members.find((s) => s.time_start < at && at <= s.time_end);
    if (!owner || !/^\d+:\d+:\d+-\d+:\d+:\d+$/.test(owner.matched_ref)) return undefined;
    const after = endRef(boundary.refs[i]!);
    if (compareRef(after, owner.matched_ref.split('-')[0]!) < 0 || compareRef(after, endRef(owner.matched_ref)) > 0) return undefined;
    if (at === owner.time_end && after !== endRef(owner.matched_ref)) return undefined;
    if (at < owner.time_end && compareRef(boundary.refs[i + 1]!.split('-')[0]!, endRef(owner.matched_ref)) > 0) return undefined;
    return owner;
}

/** Virtual slices keep every proposed join reviewable after a partial physical split. */
export function reviewPieces(members: readonly Segment[], boundary: StagedSplit, childUids: readonly string[]): Segment[] {
    return members.flatMap((seg) => {
        const indices = boundary.cursors.flatMap((c, i) => c > seg.time_start && c < seg.time_end && reviewJoinOwner([seg], boundary, i) ? [i] : []);
        if (!indices.length) return [seg];
        const refs = [seg.matched_ref.split('-')[0]!];
        const ranges: string[] = [];
        for (const i of indices) {
            ranges.push(`${refs.pop()}-${endRef(boundary.refs[i]!)}`);
            refs.push(boundary.refs[i + 1]!.split('-')[0]!);
        }
        ranges.push(`${refs.pop()}-${endRef(seg.matched_ref)}`);
        return buildStagedChildren(seg, { cursors: indices.map((i) => boundary.cursors[i]!), refs: ranges }, indices.map((i) => childUids[i]!));
    });
}
