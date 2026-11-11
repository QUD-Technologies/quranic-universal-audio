import { cleanup, fireEvent, render, waitFor } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { setEditingMode } from '../../../../../lib/stores/editing-mode';
import { FakeIntersectionObserver } from '../../../../../lib/test-helpers/dom-stubs';
import type { SegValAnyItem } from '../../../../../lib/types/generated/schemas';
import { makeSegment } from '../../../__tests__/helpers/make-segment';
import { segAllData, segData, selectedChapter } from '../../../stores/chapter';
import { clearDirtyMap, clearOpLog, getChapterOps } from '../../../stores/dirty';
import { clearAllStagedPicks } from '../../../stores/staged-split';
import GenericIssueCard from '../GenericIssueCard.svelte';

beforeEach(() => {
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
    setEditingMode({ kind: 'editor' });
    selectedChapter.set('2');
    segData.set(null);
});

afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    clearDirtyMap();
    clearOpLog();
    clearAllStagedPicks();
    segAllData.set(null);
    setEditingMode({ kind: 'view', viewReason: 'unauthenticated' });
});


const item = { chapter: 2, seg_index: 0, segment_uid: 'root', boundary: {
    cursors: [1000, 2000], refs: ['2:1:1-2:1:2', '2:1:3-2:1:5', '2:1:6-2:1:9'],
} } as SegValAnyItem;

function setup() {
    const root = { ...makeSegment(0, 0, 3000, { segment_uid: 'root', matched_ref: '2:1:1-2:1:9', confidence: 0.4 }), chapter: 2 };
    segAllData.set({ segments: [root] } as never);
    return render(GenericIssueCard, { category: 'missed_waqf', item });
}

it('keeps the staged flow and writes mixed answers when it commits', async () => {
    const { getAllByText, container } = setup();
    await fireEvent.click(getAllByText('WAQF')[1]!);
    expect(get(segAllData)!.segments).toHaveLength(1);
    expect(getChapterOps(2)).toHaveLength(0);
    await fireEvent.click(getAllByText('WASL')[0]!);
    await waitFor(() => expect(get(segAllData)!.segments).toHaveLength(2));
    expect(get(segAllData)!.segments[0]!.join_verdicts).toEqual([
        { at_ms: 1000, after_ref: '2:1:2', verdict: 'wasl' },
        { at_ms: 2000, after_ref: '2:1:5', verdict: 'waqf' },
    ]);
    expect(container.querySelectorAll('.wasl-boundary')).toHaveLength(1);
    expect(getChapterOps(2).map((op) => op.op_type)).toEqual(['split_segment']);
    await fireEvent.click(getAllByText('WASL')[0]!);
    await waitFor(() => expect(get(segAllData)!.segments).toHaveLength(1));
    expect(get(segAllData)!.segments[0]!.join_verdicts!.map((j) => j.verdict)).toEqual(['wasl', 'wasl']);
    expect(get(segAllData)!.segments[0]!.ignored_categories).toContain('missed_waqf');
});

it('ignore answers every asked cursor WASL and raises confidence to 1', async () => {
    const { container } = setup();
    await fireEvent.click(container.querySelector('.ignore-btn')!);
    await waitFor(() => expect(get(segAllData)!.segments[0]!.confidence).toBe(1));
    expect(get(segAllData)!.segments[0]!.join_verdicts!.map((j) => j.verdict)).toEqual(['wasl', 'wasl']);
    expect(getChapterOps(2)[0]!.targets_after[0]!.join_verdicts).toHaveLength(2);
});
