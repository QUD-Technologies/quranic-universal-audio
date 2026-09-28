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

it('keeps every join answerable through partial split, relabel, merge, and reload', async () => {
    const root = { ...makeSegment(0, 0, 3000, { segment_uid: 'root', matched_ref: '2:1:1-2:1:9', confidence: 0.4 }), chapter: 2 };
    segAllData.set({ segments: [root] } as never);
    const item = { chapter: 2, seg_index: 0, segment_uid: 'root', boundary: {
        cursors: [1000, 2000], refs: ['2:1:1-2:1:2', '2:1:3-2:1:5', '2:1:6-2:1:9'],
    } } as SegValAnyItem;
    const { container, getAllByText } = render(GenericIssueCard, { category: 'missed_waqf', item });
    const pressed = () => [...container.querySelectorAll('.wasl-boundary')].map((el) =>
        el.querySelector('[aria-pressed="true"]')?.textContent ?? 'unset');
    await waitFor(() => expect(pressed()).toEqual(['unset', 'unset']));

    await fireEvent.click(getAllByText('WAQF')[1]!);
    await waitFor(() => expect(get(segAllData)!.segments).toHaveLength(2));
    await waitFor(() => expect(pressed()).toEqual(['unset', 'WAQF']));
    await fireEvent.click(getAllByText('WASL')[0]!);
    await waitFor(() => expect(pressed()).toEqual(['WASL', 'WAQF']));
    expect(get(segAllData)!.segments).toHaveLength(2);
    await fireEvent.click(getAllByText('WASL')[1]!);
    await waitFor(() => expect(get(segAllData)!.segments).toHaveLength(1));
    await waitFor(() => expect(pressed()).toEqual(['WASL', 'WASL']));
    expect(getChapterOps(2).map((op) => op.op_type)).toEqual(['split_segment', 'set_is_wasl', 'merge_segments']);
    expect(get(segAllData)!.segments[0]!.ignored_categories).toBeUndefined();

    const saved = JSON.parse(JSON.stringify(get(segAllData)));
    clearOpLog();
    clearDirtyMap();
    segAllData.set(saved);
    await waitFor(() => expect(pressed()).toEqual(['WASL', 'WASL']));
    await fireEvent.click(getAllByText('WAQF')[0]!);
    await waitFor(() => expect(pressed()).toEqual(['WAQF', 'WASL']));
    expect(get(segAllData)!.segments).toHaveLength(2);
});
