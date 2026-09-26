/**
 * Staged cross-verse split — eligibility gate + display-piece builder.
 */
import { describe, expect, it } from 'vitest';

import type { Segment } from '../../../../lib/types/view-models';
import type { AutoSplitMap } from '../../stores/auto-split';
import {
    buildStagedChildren,
    HIDDEN_PAUSE_KINDS,
    isStagedSegment,
    resolveStagedSplit,
    waqfOnlySplit,
} from '../../utils/validation/staged-split';

const seg = (o: Partial<Segment> = {}): Segment => ({
    index: 4,
    entry_idx: 0,
    chapter: 2,
    segment_uid: 'root',
    time_start: 1000,
    time_end: 5000,
    matched_ref: '2:1:1-2:2:5',
    confidence: 0.6,
    ignored_categories: ['low_confidence'],
    wrap_word_ranges: [[1, 2]],
    ...o,
});

const map = (entry: Partial<AutoSplitMap[string]> = {}): AutoSplitMap => ({
    root: { cursors: [3000], refs: ['2:1:1-2:1:4', '2:2:1-2:2:5'], kind: 'cross_verse', ...entry },
});

describe('resolveStagedSplit', () => {
    it('returns cursors + refs for a clean cross_verse entry', () => {
        expect(resolveStagedSplit(seg(), map())).toEqual({
            cursors: [3000],
            refs: ['2:1:1-2:1:4', '2:2:1-2:2:5'],
        });
    });

    it('is null without a seg, map, uid or entry', () => {
        expect(resolveStagedSplit(null, map())).toBeNull();
        expect(resolveStagedSplit(seg(), null)).toBeNull();
        expect(resolveStagedSplit(seg({ segment_uid: undefined }), map())).toBeNull();
        expect(resolveStagedSplit(seg({ segment_uid: 'other' }), map())).toBeNull();
    });

    it('is null when the seg is not cross-verse', () => {
        expect(resolveStagedSplit(seg({ matched_ref: '2:1:1-2:1:7' }), map())).toBeNull();
    });

    it('is null for a repetition / hidden_pause entry', () => {
        expect(resolveStagedSplit(seg(), map({ kind: 'repetition' }))).toBeNull();
        expect(resolveStagedSplit(seg(), map({ kind: 'hidden_pause' }))).toBeNull();
    });

    it('stages a hidden_pause entry inside one verse when asked for that kind', () => {
        const inVerse = seg({ matched_ref: '2:1:1-2:1:9' });
        const entry = map({ kind: 'hidden_pause', refs: ['2:1:1-2:1:4', '2:1:5-2:1:9'] });
        expect(resolveStagedSplit(inVerse, entry, HIDDEN_PAUSE_KINDS)).toEqual({
            cursors: [3000],
            refs: ['2:1:1-2:1:4', '2:1:5-2:1:9'],
        });
        expect(resolveStagedSplit(inVerse, entry)).toBeNull();
        expect(resolveStagedSplit(seg(), map(), HIDDEN_PAUSE_KINDS)).toBeNull();
    });

    it('is null when refs length does not match cursors + 1', () => {
        expect(resolveStagedSplit(seg(), map({ refs: ['2:1:1-2:1:4'] }))).toBeNull();
    });

    it('is null when a cursor is outside the seg or not ascending', () => {
        expect(resolveStagedSplit(seg(), map({ cursors: [1000] }))).toBeNull();
        expect(resolveStagedSplit(seg(), map({ cursors: [5000] }))).toBeNull();
        expect(resolveStagedSplit(seg(), map({
            cursors: [3000, 2000],
            refs: ['a', 'b', 'c'],
        }))).toBeNull();
    });
});

describe('buildStagedChildren', () => {
    const staged = { cursors: [2000, 3500], refs: ['2:1:1-2:1:4', '2:2:1-2:2:3', '2:3:1-2:3:2'] };

    it('slices the parent at the cursors with sidecar refs', () => {
        const kids = buildStagedChildren(seg(), staged, ['k1', 'k2']);
        expect(kids.map((k) => [k.time_start, k.time_end])).toEqual([[1000, 2000], [2000, 3500], [3500, 5000]]);
        expect(kids.map((k) => k.matched_ref)).toEqual(staged.refs);
    });

    it('keeps the parent uid + index on piece 0 and uses the child uids after', () => {
        const kids = buildStagedChildren(seg(), staged, ['k1', 'k2']);
        expect(kids.map((k) => k.segment_uid)).toEqual(['root', 'k1', 'k2']);
        expect(kids.every((k) => k.index === 4)).toBe(true);
        expect(kids.every((k) => k.chapter === 2)).toBe(true);
    });

    it('marks every piece staged and drops repetition metadata', () => {
        const kids = buildStagedChildren(seg(), staged, ['k1', 'k2']);
        expect(kids.every(isStagedSegment)).toBe(true);
        expect(kids.every((k) => !('wrap_word_ranges' in k))).toBe(true);
        expect(isStagedSegment(seg())).toBe(false);
    });

    it('reflects picks as is_wasl on non-last pieces; last inherits the parent', () => {
        const none = buildStagedChildren(seg(), staged, ['k1', 'k2']);
        expect(none.map((k) => k.is_wasl)).toEqual([null, null, false]);
        const some = buildStagedChildren(seg({ is_wasl: true }), staged, ['k1', 'k2'], [true, undefined]);
        expect(some.map((k) => k.is_wasl)).toEqual([true, null, true]);
        const all = buildStagedChildren(seg(), staged, ['k1', 'k2'], [false, true]);
        expect(all.map((k) => k.is_wasl)).toEqual([false, true, false]);
    });

    it('inherits ignored_categories so an ignore before commit carries over', () => {
        const kids = buildStagedChildren(seg(), staged, ['k1', 'k2']);
        expect(kids.every((k) => k.ignored_categories?.includes('low_confidence'))).toBe(true);
    });
});

describe('waqfOnlySplit', () => {
    const staged = { cursors: [2000, 3500], refs: ['2:1:1-2:1:4', '2:1:5-2:1:8', '2:1:9-2:1:12'] };

    it('cuts only the boundaries picked WAQF or left unanswered', () => {
        expect(waqfOnlySplit(staged, [false, false])).toEqual(staged);
        expect(waqfOnlySplit(staged, [])).toEqual(staged);
        expect(waqfOnlySplit(staged, [true, false])).toEqual({
            cursors: [3500],
            refs: ['2:1:1-2:1:8', '2:1:9-2:1:12'],
        });
        expect(waqfOnlySplit(staged, [false, true])).toEqual({
            cursors: [2000],
            refs: ['2:1:1-2:1:4', '2:1:5-2:1:12'],
        });
    });

    it('is null when every boundary is WASL', () => {
        expect(waqfOnlySplit(staged, [true, true])).toBeNull();
    });
});
