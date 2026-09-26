/**
 * Boundary states (cross-verse, missed-waqf) — committed members, ignored,
 * staged picks, unsplit — plus the chip counts and the filter built on them.
 */
import { describe, expect, it } from 'vitest';

import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { EditOp, Segment } from '../../../../lib/types/view-models';
import {
    type BoundaryCtx,
    boundaryStates,
    countBoundaryStates,
    filterByBoundaryStates,
    isVerseBoundary,
} from '../../utils/validation/boundary-state';

const seg = (o: Partial<Segment>): Segment => ({
    index: 0, entry_idx: 0, chapter: 2, time_start: 0, time_end: 1000,
    matched_ref: '2:1:1-2:1:4', confidence: 1, ...o,
});
const item = (o: Record<string, unknown>): SegValAnyItem => o as unknown as SegValAnyItem;

function ctx(segs: Segment[], over: Partial<BoundaryCtx> = {}): BoundaryCtx {
    return {
        chapterSegs: () => segs,
        opLog: () => [] as EditOp[],
        splitGroupIndex: {},
        pendingWasl: new Set(),
        autoSplitMap: null,
        stagedPicks: {},
        ...over,
    };
}

describe('isVerseBoundary', () => {
    it('is true when the join crosses an ayah or surah', () => {
        expect(isVerseBoundary(seg({ matched_ref: '2:1:1-2:1:4' }), seg({ matched_ref: '2:2:1-2:2:3' }))).toBe(true);
        expect(isVerseBoundary(seg({ matched_ref: '2:286:1-2:286:9' }), seg({ matched_ref: '3:1:1-3:1:1' }))).toBe(true);
    });
    it('is false inside one verse', () => {
        expect(isVerseBoundary(seg({ matched_ref: '2:1:1-2:1:2' }), seg({ matched_ref: '2:1:3-2:1:4' }))).toBe(false);
    });
});

describe('boundaryStates', () => {
    const root = item({ chapter: 2, seg_index: 0, segment_uid: 'root' });

    it('reads is_wasl off committed members (split_group_index)', () => {
        const segs = [
            seg({ segment_uid: 'root', index: 0, time_start: 0, time_end: 500, matched_ref: '2:1:1-2:1:4', is_wasl: true }),
            seg({ segment_uid: 'b', index: 1, time_start: 500, time_end: 800, matched_ref: '2:2:1-2:2:3', is_wasl: false }),
            seg({ segment_uid: 'c', index: 2, time_start: 800, time_end: 1000, matched_ref: '2:3:1-2:3:2' }),
        ];
        const c = ctx(segs, { splitGroupIndex: { root: ['b', 'c'] } });
        expect(boundaryStates(root, c)).toEqual(['wasl', 'waqf']);
    });

    it('treats a member awaiting its post-split pick as unset', () => {
        const segs = [
            seg({ segment_uid: 'root', index: 0, time_start: 0, time_end: 500, matched_ref: '2:1:1-2:1:4' }),
            seg({ segment_uid: 'b', index: 1, time_start: 500, time_end: 1000, matched_ref: '2:2:1-2:2:3' }),
        ];
        const c = ctx(segs, { splitGroupIndex: { root: ['b'] }, pendingWasl: new Set(['root']) });
        expect(boundaryStates(root, c)).toEqual(['unset']);
    });

    it('skips same-verse joins inside a committed group', () => {
        const segs = [
            seg({ segment_uid: 'root', index: 0, time_start: 0, time_end: 300, matched_ref: '2:1:1-2:1:2', is_wasl: true }),
            seg({ segment_uid: 'b', index: 1, time_start: 300, time_end: 500, matched_ref: '2:1:3-2:1:4', is_wasl: false }),
            seg({ segment_uid: 'c', index: 2, time_start: 500, time_end: 1000, matched_ref: '2:2:1-2:2:3' }),
        ];
        const c = ctx(segs, { splitGroupIndex: { root: ['b', 'c'] } });
        expect(boundaryStates(root, c)).toEqual(['waqf']);
    });

    it('reads the session picks for a staged split', () => {
        const segs = [seg({ segment_uid: 'root', matched_ref: '2:1:1-2:3:2' })];
        const autoSplitMap = { root: { cursors: [300, 600], refs: ['a', 'b', 'c'], kind: 'cross_verse' as const } };
        expect(boundaryStates(root, ctx(segs, { autoSplitMap }))).toEqual(['unset', 'unset']);
        expect(boundaryStates(root, ctx(segs, { autoSplitMap, stagedPicks: { root: [true, undefined] } })))
            .toEqual(['wasl', 'unset']);
        expect(boundaryStates(root, ctx(segs, { autoSplitMap, stagedPicks: { root: [false, true] } })))
            .toEqual(['waqf', 'wasl']);
    });

    it('is one unset boundary for an unsplit seg without a sidecar entry', () => {
        const segs = [seg({ segment_uid: 'root', matched_ref: '2:1:1-2:2:2' })];
        expect(boundaryStates(root, ctx(segs))).toEqual(['unset']);
        expect(boundaryStates(item({ chapter: 2, seg_index: 0 }), ctx(segs))).toEqual(['unset']);
    });
});

describe('counts + filter', () => {
    const segs = [
        seg({ segment_uid: 'r1', index: 0, time_start: 0, time_end: 500, matched_ref: '2:1:1-2:1:4', is_wasl: true }),
        seg({ segment_uid: 'r1b', index: 1, time_start: 500, time_end: 800, matched_ref: '2:2:1-2:2:3' }),
        seg({ segment_uid: 'r2', index: 2, time_start: 800, time_end: 1000, matched_ref: '2:3:1-2:4:2' }),
    ];
    const c = ctx(segs, { splitGroupIndex: { r1: ['r1b'] } });
    const items = [item({ chapter: 2, seg_index: 0, segment_uid: 'r1' }), item({ chapter: 2, seg_index: 2, segment_uid: 'r2' })];

    it('counts boundaries across items', () => {
        expect(countBoundaryStates(items, c)).toEqual({ unset: 1, wasl: 1, waqf: 0 });
    });

    it('keeps items with any boundary in a selected state', () => {
        expect(filterByBoundaryStates(items, new Set(['unset']), c).map((i) => (i as { segment_uid: string }).segment_uid)).toEqual(['r2']);
        expect(filterByBoundaryStates(items, new Set(['wasl']), c).map((i) => (i as { segment_uid: string }).segment_uid)).toEqual(['r1']);
        expect(filterByBoundaryStates(items, new Set(['waqf']), c)).toEqual([]);
        expect(filterByBoundaryStates(items, new Set(['unset', 'wasl', 'waqf']), c)).toHaveLength(2);
    });
});

describe('boundaryStates — missed_waqf', () => {
    const boundary = { cursors: [300, 600], refs: ['2:1:1-2:1:2', '2:1:3-2:1:5', '2:1:6-2:1:9'] };
    const mw = item({ chapter: 2, seg_index: 0, segment_uid: 'root', boundary });

    it('reads the session picks under the missed_waqf key, from the item boundary', () => {
        const segs = [seg({ segment_uid: 'root', matched_ref: '2:1:1-2:1:9' })];
        expect(boundaryStates(mw, ctx(segs), 'missed_waqf')).toEqual(['unset', 'unset']);
        const picked = ctx(segs, { stagedPicks: { 'missed_waqf:root': [true, false] } });
        expect(boundaryStates(mw, picked, 'missed_waqf')).toEqual(['wasl', 'waqf']);
        // cross-verse picks for the same uid do not leak in
        expect(boundaryStates(mw, ctx(segs, { stagedPicks: { root: [true, true] } }), 'missed_waqf'))
            .toEqual(['unset', 'unset']);
    });

    it('counts every cut of a split as waqf and every dropped cursor as wasl', () => {
        const segs = [
            seg({ segment_uid: 'root', index: 0, time_start: 0, time_end: 600, matched_ref: '2:1:1-2:1:5', is_wasl: false }),
            seg({ segment_uid: 'b', index: 1, time_start: 600, time_end: 1000, matched_ref: '2:1:6-2:1:9' }),
        ];
        const c = ctx(segs, { splitGroupIndex: { root: ['b'] } });
        expect(boundaryStates(mw, c, 'missed_waqf')).toEqual(['waqf', 'wasl']);
    });

    it('counts every cursor of an ignored item as wasl', () => {
        const segs = [seg({ segment_uid: 'root', matched_ref: '2:1:1-2:1:9', ignored_categories: ['missed_waqf'] })];
        expect(boundaryStates(mw, ctx(segs), 'missed_waqf')).toEqual(['wasl', 'wasl']);
    });

    it('drives the Unset · Wasl · Waqf chips across items', () => {
        const segs = [
            seg({ segment_uid: 'r1', index: 0, time_start: 0, time_end: 300, matched_ref: '2:1:1-2:1:2' }),
            seg({ segment_uid: 'r1b', index: 1, time_start: 300, time_end: 1000, matched_ref: '2:1:3-2:1:9' }),
            seg({ segment_uid: 'r2', index: 2, time_start: 1000, time_end: 2000, matched_ref: '2:2:1-2:2:9', ignored_categories: ['missed_waqf'] }),
            seg({ segment_uid: 'r3', index: 3, time_start: 2000, time_end: 3000, matched_ref: '2:3:1-2:3:9' }),
        ];
        const c = ctx(segs, { splitGroupIndex: { r1: ['r1b'] } });
        const items = [
            item({ chapter: 2, seg_index: 0, segment_uid: 'r1', boundary: { cursors: [300], refs: ['2:1:1-2:1:2', '2:1:3-2:1:9'] } }),
            item({ chapter: 2, seg_index: 2, segment_uid: 'r2', boundary: { cursors: [1500], refs: ['2:2:1-2:2:4', '2:2:5-2:2:9'] } }),
            item({ chapter: 2, seg_index: 3, segment_uid: 'r3', boundary: { cursors: [2500], refs: ['2:3:1-2:3:4', '2:3:5-2:3:9'] } }),
        ];
        expect(countBoundaryStates(items, c, 'missed_waqf')).toEqual({ unset: 1, wasl: 1, waqf: 1 });
        const uids = (sel: string[]) => filterByBoundaryStates(items, new Set(sel as never[]), c, 'missed_waqf')
            .map((i) => (i as { segment_uid: string }).segment_uid);
        expect(uids(['unset'])).toEqual(['r3']);
        expect(uids(['wasl'])).toEqual(['r2']);
        expect(uids(['waqf'])).toEqual(['r1']);
    });
});
