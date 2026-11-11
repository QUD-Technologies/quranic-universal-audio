import { describe, expect, it } from 'vitest';

import type { Segment } from '../../../../lib/types/view-models';
import { applyCommand } from '../../domain/apply-command';
import { applyInversePatchToSegments } from '../../domain/inverse-patch';
import { edgeState, endRef, joinState, resolvedVerdicts, wordAnswer } from '../../domain/join-verdict';
import { snapshotSeg } from '../../stores/dirty';

const reviewStates = (members: Segment[], b: { cursors: number[]; refs: string[] }, recheck?: Set<string>) =>
    b.refs.slice(0, b.cursors.length).map((ref) => wordAnswer(members, endRef(ref), recheck));

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

    it('ignore writes all WASL answers, confidence 1, and snapshots without touching is_wasl', () => {
        const seg = root({ is_wasl: false });
        const result = applyCommand(state(seg), { type: 'ignoreIssue', segmentUid: 'root', category: 'missed_waqf',
            joinVerdicts: resolvedVerdicts(boundary, []) });
        const next = result.nextState.byId.root!;
        expect(next.is_wasl).toBe(false);
        expect(next.confidence).toBe(1);
        expect(reviewStates([next], boundary)).toEqual(['wasl', 'wasl']);
        expect(snapshotSeg(next).join_verdicts).toEqual(next.join_verdicts);
        expect(result.patch.before[0]!.join_verdicts).toBeUndefined();
    });

    it('records mixed answers on their current pieces through reload', () => {
        const result = applyCommand(state(root()), {
            type: 'split', segmentUid: 'root', splitMs: [600], newUids: ['right'],
            refs: ['2:1:1-2:1:5', '2:1:6-2:1:9'], sourceCategory: 'missed_waqf',
            joinVerdicts: resolvedVerdicts(boundary, [600]),
        });
        const members: Segment[] = JSON.parse(JSON.stringify(Object.values(result.nextState.byId)));
        expect(reviewStates(members, boundary)).toEqual(['wasl', 'waqf']);
        expect(members[1]!.join_verdicts).toEqual([]);
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
        expect(wordAnswer([next], '2:1:9')).toBe('wasl');
        expect(reviewStates([next], boundary)).toEqual(['wasl', 'wasl']);
        expect(left.join_verdicts![1]!.verdict).toBe('waqf');
        const undone = applyInversePatchToSegments([next], result.patch!);
        expect(undone.map((s) => s.join_verdicts)).toEqual([left.join_verdicts, right.join_verdicts]);
        expect(undone.map((s) => s.is_wasl === true)).toEqual([false, true]);
    });

    it('a cut with a known reference records waqf', () => {
        const result = applyCommand(state(root()), { type: 'split', segmentUid: 'root', splitMs: [300],
            newUids: ['right'], refs: ['2:1:1-2:1:2', '2:1:3-2:1:9'], wasls: [undefined] });
        expect(edgeState(result.nextState.byId.root!)).toBe('waqf');
    });

    it('distinguishes repeated occurrences by cursor and drops answers on reference edits', () => {
        const seg = root({ join_verdicts: [{ at_ms: 300, after_ref: '2:1:2', verdict: 'wasl' }] });
        expect(joinState(seg, 600, '2:1:2')).toBe('unset');
        const result = applyCommand(state(seg), { type: 'editReference', segmentUid: 'root', matched_ref: '2:2:1-2:2:9' });
        expect(result.nextState.byId.root!.join_verdicts).toEqual([]);
    });
    it('trim and reference edits retain only answers in the resulting range', () => {
        const seg = root({ join_verdicts: resolvedVerdicts(boundary, []) });
        const trimmed = applyCommand(state(seg), { type: 'trim', segmentUid: 'root', delta: { time_start: 350 } }).nextState.byId.root!;
        // the words stay in the piece, so their answers stay, inside its audio
        expect(trimmed.join_verdicts!.map((j) => j.at_ms)).toEqual([351, 600]);
        const edited = applyCommand(state(seg), { type: 'editReference', segmentUid: 'root', matched_ref: '2:1:3-2:1:9' }).nextState.byId.root!;
        expect(edited.join_verdicts!.map((j) => j.at_ms)).toEqual([600]);
    });

    it('a merge answers its seam WASL', () => {
        const a = root({ time_end: 300, matched_ref: boundary.refs[0], join_verdicts: [{ at_ms: 300, after_ref: '2:1:2', verdict: 'waqf' }] });
        const b = root({ segment_uid: 'b', index: 1, time_start: 300, matched_ref: '2:1:3-2:1:9' });
        const next = applyCommand(state(a, b), { type: 'merge', fromUid: 'root', toUid: 'b' }).nextState.byId.root!;
        expect(next.join_verdicts).toEqual([{ at_ms: 300, after_ref: '2:1:2', verdict: 'wasl' }]);
        expect(wordAnswer([next], '2:1:2')).toBe('wasl');
    });

    it('marking an edge WASL or WAQF moves its recorded answer with it', () => {
        const a = root({ time_end: 300, matched_ref: '2:1:1-2:1:2', is_wasl: false,
            join_verdicts: [{ at_ms: 300, after_ref: '2:1:2', verdict: 'waqf' }] });
        const wasl = applyCommand(state(a), { type: 'setIsWasl', segmentUid: 'root', is_wasl: true }).nextState.byId.root!;
        expect(wasl.join_verdicts).toEqual([]);
        expect(wordAnswer([wasl], '2:1:2')).toBe('wasl');
        const waqf = applyCommand(state(wasl), { type: 'setIsWasl', segmentUid: 'root', is_wasl: false }).nextState.byId.root!;
        expect(waqf.join_verdicts).toEqual([{ at_ms: 300, after_ref: '2:1:2', verdict: 'waqf' }]);
    });

    it('an answer follows its word through a re-split elsewhere', () => {
        const seg = root({ join_verdicts: [{ at_ms: 300, after_ref: '2:1:2', verdict: 'wasl' }] });
        const r = applyCommand(state(seg), { type: 'split', segmentUid: 'root', splitMs: [700], newUids: ['right'],
            refs: ['2:1:1-2:1:6', '2:1:7-2:1:9'], wasls: [undefined] });
        const pieces = Object.values(r.nextState.byId);
        expect(wordAnswer(pieces, '2:1:2')).toBe('wasl');
        expect(wordAnswer(pieces, '2:1:6')).toBe('waqf');
    });

});

describe('a cut opened to its silence', () => {
    const one = { cursors: [300], refs: ['2:1:1-2:1:2', '2:1:3-2:1:9'] };

    it('a trimmed edge keeps its WAQF answer at the new end', () => {
        const left = root({ time_end: 300, matched_ref: '2:1:1-2:1:2',
            join_verdicts: [{ at_ms: 300, after_ref: '2:1:2', verdict: 'waqf' }] });
        const r = applyCommand(state(left), { type: 'trim', segmentUid: 'root', delta: { time_end: 240 } });
        expect(r.nextState.byId.root?.join_verdicts).toEqual([{ at_ms: 240, after_ref: '2:1:2', verdict: 'waqf' }]);
    });

    it('the piece ending on the cut word owns a cursor left in the gap', () => {
        const left = root({ time_end: 240, matched_ref: '2:1:1-2:1:2',
            join_verdicts: [{ at_ms: 240, after_ref: '2:1:2', verdict: 'waqf' }] });
        const right = root({ segment_uid: 'right', index: 1, time_start: 360, time_end: 1000, matched_ref: '2:1:3-2:1:9' });
        expect(reviewStates([left, right], one)).toEqual(['waqf']);
    });

    it('a split made off the cursor answers the cut by its word', () => {
        const left = root({ time_end: 280, matched_ref: '2:1:1-2:1:2' });
        const right = root({ segment_uid: 'right', index: 1, time_start: 280, time_end: 1000, matched_ref: '2:1:3-2:1:9' });
        expect(reviewStates([left, right], one)).toEqual(['waqf']);
        expect(reviewStates([{ ...left, is_wasl: true }, right], one)).toEqual(['wasl']);
        expect(reviewStates([left, right], one, new Set(['root']))).toEqual(['unset']);
    });
});

