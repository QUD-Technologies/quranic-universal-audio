/** One answer convention for internal joins and segment edges. Absence = unset. */
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
    const byJoin = new Map((current ?? []).map((j) => [`${j.at_ms}:${j.after_ref}`, { ...j }]));
    for (const j of answers) byJoin.set(`${j.at_ms}:${j.after_ref}`, { ...j });
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
