import { cleanup, fireEvent, render, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FakeIntersectionObserver } from '../../../../../lib/test-helpers/dom-stubs';
import { makeSegment } from '../../../__tests__/helpers/make-segment';
import { segAllData } from '../../../stores/chapter';
import { segConfig } from '../../../stores/config';
import MissingWordsCard from '../MissingWordsCard.svelte';

beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  segAllData.set(null);
  segConfig.update((cfg) => ({ ...cfg, accordionContext: null }));
});

function installSegments(): void {
  const segments = [
    { ...makeSegment(0, 0, 1000), chapter: 1, index: 0 },
    { ...makeSegment(1, 1000, 2000), chapter: 1, index: 1 },
    { ...makeSegment(2, 2000, 3000), chapter: 1, index: 2 },
  ];
  segAllData.set({ segments } as any);
}

/**
 * Gap bracketed by segs 1 and 3 with an unmatched ("no match") seg 2 sitting
 * between them. The backend only names 1 and 3 in `seg_indices` — its coverage
 * map is built from matched references, so the unmatched row is invisible to
 * it.
 */
function installGapWithNoMatch(): void {
  const segments = [
    { ...makeSegment(0, 0, 1000, { matched_ref: '1:1:1-1:1:1' }), chapter: 1, index: 0 },
    { ...makeSegment(1, 1000, 2000, { matched_ref: '1:1:2-1:1:2' }), chapter: 1, index: 1 },
    { ...makeSegment(2, 2000, 3000, { matched_ref: '', matched_text: '' }), chapter: 1, index: 2 },
    { ...makeSegment(3, 3000, 4000, { matched_ref: '1:1:5-1:1:5' }), chapter: 1, index: 3 },
    { ...makeSegment(4, 4000, 5000, { matched_ref: '1:1:6-1:1:6' }), chapter: 1, index: 4 },
  ];
  segAllData.set({ segments } as any);
}

function renderedIndices(container: HTMLElement): number[] {
  return Array.from(container.querySelectorAll('[data-seg-index]')).map((el) =>
    Number((el as HTMLElement).dataset.segIndex),
  );
}

describe('MissingWordsCard no-match filler', () => {
  it('renders unmatched segments sitting between the bracketing pair', async () => {
    installGapWithNoMatch();

    const { container } = render(MissingWordsCard, {
      item: { chapter: 1, verse_key: '1:1', seg_indices: [1, 3], msg: 'missing words: [3, 4]' },
    });

    await waitFor(() => expect(renderedIndices(container)).toEqual([1, 2, 3]));
  });

  it('leaves matched in-between segments out — they belong to their own cards', async () => {
    const segments = [
      { ...makeSegment(0, 0, 1000, { matched_ref: '1:1:1-1:1:1' }), chapter: 1, index: 0 },
      { ...makeSegment(1, 1000, 2000, { matched_ref: '1:2:1-1:2:1' }), chapter: 1, index: 1 },
      { ...makeSegment(2, 2000, 3000, { matched_ref: '1:1:5-1:1:5' }), chapter: 1, index: 2 },
    ];
    segAllData.set({ segments } as any);

    const { container } = render(MissingWordsCard, {
      item: { chapter: 1, verse_key: '1:1', seg_indices: [0, 2], msg: 'missing words: [2, 3, 4]' },
    });

    await waitFor(() => expect(renderedIndices(container)).toEqual([0, 2]));
  });
});

describe('MissingWordsCard context defaults', () => {
  it('opens context by default when configured as shown', async () => {
    installSegments();
    segConfig.update((cfg) => ({ ...cfg, accordionContext: { missing_words: 'shown' } }));

    const { getByText } = render(MissingWordsCard, {
      item: { chapter: 1, verse_key: '1:1', seg_indices: [1], msg: 'missing words: [2]' },
    });

    await waitFor(() => expect(getByText('Hide Context')).toBeTruthy());
  });

  it('allows the user to hide default-open context', async () => {
    installSegments();
    segConfig.update((cfg) => ({ ...cfg, accordionContext: { missing_words: 'shown' } }));

    const { getByText } = render(MissingWordsCard, {
      item: { chapter: 1, verse_key: '1:1', seg_indices: [1], msg: 'missing words: [2]' },
    });

    const button = await waitFor(() => getByText('Hide Context'));
    await fireEvent.click(button);

    await waitFor(() => expect(getByText('Show Context')).toBeTruthy());
  });
});
