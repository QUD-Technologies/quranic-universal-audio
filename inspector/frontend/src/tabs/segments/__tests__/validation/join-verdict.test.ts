import { describe, expect, it } from 'vitest';

import type { Segment } from '../../../../lib/types/view-models';
import { applyCommand } from '../../domain/apply-command';
import { applyInversePatchToSegments } from '../../domain/inverse-patch';
import { edgeState, joinState } from '../../domain/join-verdict';
import { snapshotSeg } from '../../stores/dirty';
import { reviewPieces, reviewStates } from '../../utils/validation/join-review';

const root = (extra: Partial<Segment> = {}): Segment => ({
    index: 0, entry_idx: 0, chapter: 2, segment_uid: 'root',
    time_start: 0, time_end: 1000, matched_ref: '2:1:1-2:1:9', confidence: 0.4, ...extra,
});
const state = (...segs: Segment[]) => ({
    byId: Object.fromEntries(segs.map((s) => [s.segment_uid!, s])),
    idsByChapter: { 2: segs.map((s) => s.segment_uid!) }, selectedChapter: 2,
});
const boundary = { cursors: [300, 600], refs: ['2:1:1-2:1:2', '2:1:3-2:1:5', '2:1:6-2:1:9'] };

describe('one join verdict convention', () => {
    it('does not infer a verdict from a default flag or an ignored category', () => {
        const seg = root({ is_wasl: false, ignored_categories: ['missed_waqf'] });
        expect(edgeState(seg)).toBe('unset');
        expect(reviewStates([seg], boundary)).toEqual(['unset', 'unset']);
    });

    it('records an explicit WAQF even when the legacy flag was already false', () => {
        const seg = root({ is_wasl: false });
        const result = applyCommand(state(seg), { type: 'setIsWasl', segmentUid: 'root', is_wasl: false });
        const next = result.nextState.byId.root!;
        expect(edgeState(next)).toBe('waqf');
        expect(result.operation.targets_after[0]!.join_verdicts).toEqual(next.join_verdicts);
        expect(result.patch!.before[0]!.join_verdicts).toBeUndefined();
        expect(seg.join_verdicts).toBeUndefined();
    });

    it('stores WASL inside an unsplit segment without changing its outer edge', () => {
        const seg = root({ is_wasl: false });
        const result = applyCommand(state(seg), {
            type: 'setIsWasl', segmentUid: 'root', is_wasl: true,
            join: { at_ms: 300, after_ref: '2:1:2' }, contextCategory: 'missed_waqf',
        });
        const next = result.nextState.byId.root!;
        expect(next.is_wasl).toBe(false);
        expect(next.confidence).toBe(0.4);
        expect(reviewStates([next], boundary)).toEqual(['wasl', 'unset']);
        expect(snapshotSeg(next).join_verdicts).toEqual(next.join_verdicts);
    });

    it('keeps a dropped unknown cursor unset through a partial split and reload', () => {
        const result = applyCommand(state(root()), {
            type: 'split', segmentUid: 'root', splitMs: [600], newUids: ['right'],
            refs: ['2:1:1-2:1:5', '2:1:6-2:1:9'], wasls: [false],
        });
        const members: Segment[] = JSON.parse(JSON.stringify(Object.values(result.nextState.byId)));
        expect(reviewStates(members, boundary)).toEqual(['unset', 'waqf']);
        const display = reviewPieces(members, boundary, ['virtual', 'right']);
        expect(display.map((s) => [s.time_start, s.time_end])).toEqual([[0, 300], [300, 600], [600, 1000]]);
        expect(display.map((s) => s.matched_ref)).toEqual(boundary.refs);
    });

    it('preserves internal answers and the right outer edge when merging', () => {
        const left = root({ time_end: 600, matched_ref: '2:1:1-2:1:5', is_wasl: false,
            join_verdicts: [{ at_ms: 300, after_ref: '2:1:2', verdict: 'wasl' }, { at_ms: 600, after_ref: '2:1:5', verdict: 'waqf' }] });
        const right = root({ segment_uid: 'right', index: 1, time_start: 600, matched_ref: '2:1:6-2:1:9', is_wasl: true,
            join_verdicts: [{ at_ms: 1000, after_ref: '2:1:9', verdict: 'wasl' }] });
        const result = applyCommand(state(left, right), { type: 'merge', fromUid: 'root', toUid: 'right',
            joinVerdicts: [{ at_ms: 600, after_ref: '2:1:5', verdict: 'wasl' }] });
        const next = result.nextState.byId.root!;
        expect(next.is_wasl).toBe(true);
        expect(edgeState(next)).toBe('wasl');
        expect(reviewStates([next], boundary)).toEqual(['wasl', 'wasl']);
        expect(left.join_verdicts![1]!.verdict).toBe('waqf');
        const undone = applyInversePatchToSegments([next], result.patch!);
        expect(undone.map((s) => s.join_verdicts)).toEqual([left.join_verdicts, right.join_verdicts]);
        expect(undone.map((s) => s.is_wasl === true)).toEqual([false, true]);
    });

    it('does not turn unpicked cross-verse split cursors into answers', () => {
        const result = applyCommand(state(root()), { type: 'split', segmentUid: 'root', splitMs: [300],
            newUids: ['right'], refs: ['2:1:1-2:1:2', '2:1:3-2:1:9'], wasls: [undefined] });
        expect(edgeState(result.nextState.byId.root!)).toBe('unset');
    });

    it('distinguishes repeated occurrences by cursor and drops answers on reference edits', () => {
        const seg = root({ join_verdicts: [{ at_ms: 300, after_ref: '2:1:2', verdict: 'wasl' }] });
        expect(joinState(seg, 600, '2:1:2')).toBe('unset');
        const result = applyCommand(state(seg), { type: 'editReference', segmentUid: 'root', matched_ref: '2:2:1-2:2:9' });
        expect(result.nextState.byId.root!.join_verdicts).toEqual([]);
    });
});
