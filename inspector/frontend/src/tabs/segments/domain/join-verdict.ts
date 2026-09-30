/**
 * One answer convention for internal joins and segment edges. Absence = unset.
 *
 * An answer belongs to its word (`after_ref`), not its audio position: a piece
 * ending on the word is a boundary (WASL when `is_wasl`, else WAQF), and an inner
 * word reads the WASL stored for it. Every edit keeps answers on their words.
 */
import type { JoinVerdict } from '../../../lib/types/generated/schemas';
import type { Segment } from '../../../lib/types/view-models';

export type JoinState = 'unset' | JoinVerdict['verdict'];

export function endRef(ref: string): string {
    return ref.split('-').pop() ?? '';
}

export function joinState(
    seg: Pick<Segment, 'join_verdicts'>,
    atMs: number,
    afterRef: string,
): JoinState {
    return seg.join_verdicts?.find((j) => j.at_ms === atMs && j.after_ref === afterRef)?.verdict ?? 'unset';
}

export function edgeState(seg: Segment): JoinState {
    return joinState(seg, seg.time_end, endRef(seg.matched_ref));
}

export function putVerdicts(
    current: readonly JoinVerdict[] | null | undefined,
    answers: readonly JoinVerdict[],
): JoinVerdict[] {
    const byJoin = new Map((current ?? []).map((j) => [j.after_ref, { ...j }]));
    for (const j of answers) byJoin.set(j.after_ref, { ...j });
    return [...byJoin.values()].sort((a, b) => a.at_ms - b.at_ms || a.after_ref.localeCompare(b.after_ref));
}

export function pickedVerdicts(
    cursors: readonly number[],
    refs: readonly string[],
    picks: readonly (boolean | undefined)[],
): JoinVerdict[] {
    return cursors.flatMap((at_ms, i) => picks[i] === undefined ? [] : [{
        at_ms, after_ref: endRef(refs[i]!), verdict: picks[i] ? 'wasl' as const : 'waqf' as const,
    }]);
}

export function edgeAnswer(seg: Segment, value: boolean): JoinVerdict {
    return { at_ms: seg.time_end, after_ref: endRef(seg.matched_ref), verdict: value ? 'wasl' : 'waqf' };
}

function compareRef(a: string, b: string): number {
    const x = a.split(':').map(Number), y = b.split(':').map(Number);
    for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i]! - y[i]!;
    return 0;
}

/** `answers` with the stop answer at `before`'s end moved to `after`'s end (a trimmed edge keeps its answer). */
export function movedEdgeVerdicts(before: Segment, after: Segment, answers = before.join_verdicts ?? []): JoinVerdict[] {
    const end = endRef(before.matched_ref);
    return answers.map((j) => j.at_ms === before.time_end && j.after_ref === end && j.verdict === 'waqf'
        ? { ...j, at_ms: after.time_end }
        : j);
}

/** Answers whose word this piece holds: a WAQF on its last word at its end, a WASL on an inner word inside its audio. */
export function containedVerdicts(seg: Segment, answers = seg.join_verdicts ?? []): JoinVerdict[] {
    const start = seg.matched_ref.split('-')[0]!;
    const end = endRef(seg.matched_ref);
    const lo = seg.time_start, hi = seg.time_end;
    return answers.flatMap((j) => {
        if (compareRef(start, j.after_ref) > 0 || compareRef(j.after_ref, end) > 0) return [];
        if (j.after_ref === end) return j.verdict === 'waqf' ? [{ ...j, at_ms: hi }] : [];
        if (j.verdict !== 'wasl' || hi - lo <= 1) return [];
        return [{ ...j, at_ms: Math.min(Math.max(j.at_ms, lo + 1), hi - 1) }];
    });
}

/** The answer at word `after` among `segs` (see module doc); a piece under recheck answers nothing at its end. */
export function wordAnswer(segs: readonly Segment[], after: string, recheck: ReadonlySet<string> = new Set()): JoinState {
    for (const s of segs) {
        const [from, to] = s.matched_ref?.split('-') ?? [];
        if (!from || !to || compareRef(from, after) > 0 || compareRef(after, to) > 0) continue;
        if (to === after) return recheck.has(s.segment_uid ?? '') ? 'unset' : s.is_wasl ? 'wasl' : 'waqf';
        if (s.join_verdicts?.some((j) => j.after_ref === after && j.verdict === 'wasl')) return 'wasl';
    }
    return 'unset';
}

/** Resolving an item answers all its cursors: cuts stop, the rest continue. */
export function resolvedVerdicts(boundary: { cursors: readonly number[]; refs: readonly string[] }, cuts: readonly number[]): JoinVerdict[] {
    return pickedVerdicts(boundary.cursors, boundary.refs, boundary.cursors.map((c) => !cuts.includes(c)));
}
