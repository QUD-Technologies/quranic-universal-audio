// Unit tests for applyInversePatchToSegments — frontend port of the
// server's apply_inverse_patch (inspector/domain/command.py). Each case
// constructs a fresh forward patch and asserts that inverting it returns
// the segments to their pre-op state.

import { describe, expect, it } from 'vitest';

import type { EditOpPatch, Segment } from '../../../../lib/types/view-models';
import { applyInversePatchToSegments } from '../../domain/inverse-patch';

function seg(overrides: Partial<Segment> & { segment_uid: string; chapter: number; time_start: number }): Segment {
    return {
        index: 0,
        entry_idx: 0,
        time_end: overrides.time_start + 1000,
        matched_ref: '',
        matched_text: '',
        confidence: 1,
        audio_url: '',
        ...overrides,
    };
}

function snap(s: Segment): Record<string, unknown> {
    return {
        segment_uid: s.segment_uid,
        index_at_save: s.index,
        audio_url: s.audio_url,
        time_start: s.time_start,
        time_end: s.time_end,
        matched_ref: s.matched_ref,
        matched_text: s.matched_text,
        confidence: s.confidence,
        chapter: s.chapter,
        ...(s.ignored_categories ? { ignored_categories: s.ignored_categories } : {}),
    };
}

describe('applyInversePatchToSegments', () => {
    it('reverts a trim (in-place mutation, same uid)', () => {
        const before = seg({ segment_uid: 'a', chapter: 1, time_start: 1000, time_end: 2000, confidence: 0.5 });
        const after: Segment = { ...before, time_start: 1100, time_end: 1900, confidence: 1.0 };
        const patch: EditOpPatch = {
            before: [snap(before)],
            after: [snap(after)],
            removedIds: [],
            insertedIds: [],
            affectedChapterIds: [1],
        };
        const result = applyInversePatchToSegments([after], patch);
        expect(result).toHaveLength(1);
        expect(result[0]!.time_start).toBe(1000);
        expect(result[0]!.time_end).toBe(2000);
        expect(result[0]!.confidence).toBe(0.5);
    });

    it('reverts an edit_reference (matched_ref/text restored)', () => {
        const before = seg({
            segment_uid: 'a', chapter: 1, time_start: 0, time_end: 1000,
            matched_ref: '1:1:1', matched_text: 'old',
            confidence: 0.7,
        });
        const after: Segment = { ...before, matched_ref: '1:1:2', matched_text: 'new', confidence: 1.0 };
        const patch: EditOpPatch = {
            before: [snap(before)], after: [snap(after)],
            removedIds: [], insertedIds: [], affectedChapterIds: [1],
        };
        const [restored] = applyInversePatchToSegments([after], patch);
        expect(restored!.matched_ref).toBe('1:1:1');
        expect(restored!.matched_text).toBe('old');
        expect(restored!.confidence).toBe(0.7);
    });

    it('reverts an ignore_issue (ignored_categories restored)', () => {
        const before = seg({ segment_uid: 'a', chapter: 1, time_start: 0 });
        const after: Segment = { ...before, ignored_categories: ['repetitions'] };
        const patch: EditOpPatch = {
            before: [snap(before)], after: [snap(after)],
            removedIds: [], insertedIds: [], affectedChapterIds: [1],
        };
        const [restored] = applyInversePatchToSegments([after], patch);
        expect(restored!.ignored_categories).toBeUndefined();
    });

    it('reverts a delete (re-inserts removed seg sorted by time_start)', () => {
        const removed = seg({ segment_uid: 'b', chapter: 1, time_start: 500 });
        const remaining = seg({ segment_uid: 'a', chapter: 1, time_start: 0 });
        const other = seg({ segment_uid: 'c', chapter: 1, time_start: 1000 });
        const patch: EditOpPatch = {
            before: [snap(removed)],
            after: [],
            removedIds: ['b'],
            insertedIds: [],
            affectedChapterIds: [1],
        };
        const result = applyInversePatchToSegments([remaining, other], patch);
        expect(result.map((s) => s.segment_uid)).toEqual(['a', 'b', 'c']);
    });

    it('reverts a split (drops the two halves, restores original)', () => {
        const original = seg({ segment_uid: 'a', chapter: 1, time_start: 0, time_end: 2000 });
        const firstHalf = seg({ segment_uid: 'a', chapter: 1, time_start: 0, time_end: 1000 });
        const secondHalf = seg({ segment_uid: 'b', chapter: 1, time_start: 1000, time_end: 2000 });
        const patch: EditOpPatch = {
            before: [snap(original)],
            after: [snap(firstHalf), snap(secondHalf)],
            removedIds: [],
            insertedIds: ['b'],
            affectedChapterIds: [1],
        };
        const result = applyInversePatchToSegments([firstHalf, secondHalf], patch);
        expect(result).toHaveLength(1);
        expect(result[0]!.segment_uid).toBe('a');
        expect(result[0]!.time_start).toBe(0);
        expect(result[0]!.time_end).toBe(2000);
    });

    it('reverts a merge (drops merged, restores N originals)', () => {
        const a = seg({ segment_uid: 'a', chapter: 1, time_start: 0, time_end: 1000 });
        const b = seg({ segment_uid: 'b', chapter: 1, time_start: 1000, time_end: 2000 });
        const merged = seg({ segment_uid: 'a', chapter: 1, time_start: 0, time_end: 2000 });
        const patch: EditOpPatch = {
            before: [snap(a), snap(b)],
            after: [snap(merged)],
            removedIds: ['b'],
            insertedIds: [],
            affectedChapterIds: [1],
        };
        const result = applyInversePatchToSegments([merged], patch);
        expect(result.map((s) => s.segment_uid)).toEqual(['a', 'b']);
        expect(result[0]!.time_end).toBe(1000);
        expect(result[1]!.time_start).toBe(1000);
    });

    it('preserves segments in unaffected chapters', () => {
        const ch1Before = seg({ segment_uid: 'a', chapter: 1, time_start: 0, time_end: 1000, confidence: 0.5 });
        const ch1After: Segment = { ...ch1Before, time_start: 100, confidence: 1.0 };
        const ch2Untouched = seg({ segment_uid: 'z', chapter: 2, time_start: 5000 });
        const patch: EditOpPatch = {
            before: [snap(ch1Before)], after: [snap(ch1After)],
            removedIds: [], insertedIds: [], affectedChapterIds: [1],
        };
        const result = applyInversePatchToSegments([ch1After, ch2Untouched], patch);
        const restoredCh2 = result.find((s) => s.segment_uid === 'z');
        expect(restoredCh2).toBeDefined();
        expect(restoredCh2!.time_start).toBe(5000);
        const restoredCh1 = result.find((s) => s.segment_uid === 'a');
        expect(restoredCh1!.confidence).toBe(0.5);
    });
});
