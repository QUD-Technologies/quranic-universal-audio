import { get } from 'svelte/store';

import type { Segment } from '../../../../lib/types/view-models';
import { endRef } from '../../domain/join-verdict';
import { resolveWaslRecheck, waslRecheck } from '../../stores/validation';
import { reviewJoinOwner } from '../validation/join-review';
import type { StagedSplit } from '../validation/staged-split';
import { mergeAdjacent } from './merge';
import { setIsWaslOnSegment } from './setIsWasl';
import { commitSplit, finalizeSplit } from './split-commit';

/** A Low Confidence Waqf pick: record the answer, cutting only at an actual stop. */
export function answerReviewJoin(members: readonly Segment[], boundary: StagedSplit, i: number, wasl: boolean, childUid: string): void {
    const at_ms = boundary.cursors[i]!;
    const after_ref = endRef(boundary.refs[i]!);
    const left = reviewJoinOwner(members, boundary, i);
    if (!left) return;
    const answer = { at_ms, after_ref, verdict: wasl ? 'wasl' as const : 'waqf' as const };
    if (left.time_end === at_ms) {
        const right = members[members.indexOf(left) + 1];
        if (wasl && right?.time_start === at_ms) {
            mergeAdjacent(left, 'next', 'missed_waqf', null, [answer]);
        } else {
            setIsWaslOnSegment(left, wasl, { join: answer, contextCategory: 'missed_waqf', force: get(waslRecheck).has(left.segment_uid ?? '') });
        }
        if (left.segment_uid) resolveWaslRecheck(left.segment_uid);
    } else if (wasl) {
        setIsWaslOnSegment(left, true, { join: answer, contextCategory: 'missed_waqf' });
    } else {
        const commit = commitSplit(left, [at_ms], {
            refs: [
                `${left.matched_ref.split('-')[0]}-${after_ref}`,
                `${boundary.refs[i + 1]!.split('-')[0]}-${endRef(left.matched_ref)}`,
            ],
            wasls: [false], newUids: [childUid], joinVerdicts: [answer], contextCategory: 'missed_waqf',
        });
        if (commit) finalizeSplit(commit);
    }
}
