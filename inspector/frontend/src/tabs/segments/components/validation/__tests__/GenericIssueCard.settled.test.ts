import { cleanup, render } from '@testing-library/svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { setEditingMode } from '../../../../../lib/stores/editing-mode';
import { FakeIntersectionObserver } from '../../../../../lib/test-helpers/dom-stubs';
import type { SegValAnyItem } from '../../../../../lib/types/generated/schemas';
import { makeSegment } from '../../../__tests__/helpers/make-segment';
import { segAllData, segData, selectedChapter } from '../../../stores/chapter';
import GenericIssueCard from '../GenericIssueCard.svelte';

beforeEach(() => {
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
    setEditingMode({ kind: 'editor' });
    selectedChapter.set('2');
    segData.set(null);
    const seg = { ...makeSegment(0, 0, 3000, { segment_uid: 'u1', matched_ref: '2:1:1-2:1:9', confidence: 1 }), chapter: 2 };
    segAllData.set({ segments: [seg] } as never);
});

afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    segAllData.set(null);
    setEditingMode({ kind: 'view', viewReason: 'unauthenticated' });
});

const item = { chapter: 2, seg_index: 0, segment_uid: 'u1', ref: '2:1:1-2:1:9', words: [] } as unknown as SegValAnyItem;

it('a Low Confidence v2 card still open offers Ignore', () => {
    const { container } = render(GenericIssueCard, { category: 'low_confidence_v2', item });
    expect(container.querySelector('.ignore-btn')).not.toBeNull();
});

it('a card an edit settled offers no Ignore and is dimmed', () => {
    const { container } = render(GenericIssueCard, { category: 'low_confidence_v2', item, settled: true });
    expect(container.querySelector('.ignore-btn')).toBeNull();
    expect((container.firstElementChild as HTMLElement).style.opacity).toBe('0.5');
});
