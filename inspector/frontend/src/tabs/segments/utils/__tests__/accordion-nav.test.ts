/**
 * ↑/↓ and the autoplay advance must step SEGMENT to segment — every piece of a
 * multi-piece cross-verse card is its own stop, never a whole card.
 *
 * The regression this pins: the sequence used to dedupe stops by
 * (chapter, index), and the staged pieces of a pre-applied cross-verse split
 * all carry their PARENT's (chapter, index) — so a two-piece card collapsed
 * into a single stop and ↑/↓ jumped card → card, skipping the pieces.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { accordionNavCursor, playingSegmentIndex, stagedPlayheadWindow } from '../../stores/playback';
import { valUiOpenCategory } from '../../stores/validation';
import { accordionSequence, accordionStep, isCurrentStop } from '../accordion-nav';

interface RowSpec {
    uid: string;
    chapter: number;
    index: number;
    start: number;
    end: number;
}

/** Render `.seg-row` elements the way SegmentRow does, in DOM order. */
function installRows(rows: RowSpec[]): void {
    document.body.innerHTML = '';
    for (const r of rows) {
        const el = document.createElement('div');
        el.className = 'seg-row';
        el.dataset.segUid = r.uid;
        el.dataset.segChapter = String(r.chapter);
        el.dataset.segIndex = String(r.index);
        el.dataset.segStart = String(r.start);
        el.dataset.segEnd = String(r.end);
        document.body.appendChild(el);
    }
}

/** Two staged pieces of one cross-verse parent (shared chapter+index), framed
 *  by the previous and next real segments as the card's context rows. */
const STAGED_CARD: RowSpec[] = [
    { uid: 'prev', chapter: 1, index: 3, start: 0, end: 1000 },
    { uid: 'parent', chapter: 1, index: 4, start: 1000, end: 2000 },
    { uid: 'piece-1', chapter: 1, index: 4, start: 2000, end: 3000 },
    { uid: 'next', chapter: 1, index: 5, start: 3000, end: 4000 },
];

beforeEach(() => {
    valUiOpenCategory.set('cross_verse');
    accordionNavCursor.set(null);
    playingSegmentIndex.set(null);
    stagedPlayheadWindow.set(null);
});

afterEach(() => {
    valUiOpenCategory.set(null);
    accordionNavCursor.set(null);
    playingSegmentIndex.set(null);
    stagedPlayheadWindow.set(null);
    document.body.innerHTML = '';
});

describe('accordionSequence', () => {
    it('gives each staged piece its own stop despite the shared parent index', () => {
        installRows(STAGED_CARD);
        expect(accordionSequence().map((r) => r.uid)).toEqual(['prev', 'parent', 'piece-1', 'next']);
    });

    it('carries each stop’s own window', () => {
        installRows(STAGED_CARD);
        const piece = accordionSequence()[2]!;
        expect([piece.startMs, piece.endMs]).toEqual([2000, 3000]);
    });

    it('still dedupes a segment rendered twice (one card’s next is the next card’s main row)', () => {
        installRows([
            { uid: 'a', chapter: 1, index: 1, start: 0, end: 500 },
            { uid: 'b', chapter: 1, index: 2, start: 500, end: 900 },
            { uid: 'b', chapter: 1, index: 2, start: 500, end: 900 },
            { uid: 'c', chapter: 1, index: 3, start: 900, end: 1200 },
        ]);
        expect(accordionSequence().map((r) => r.uid)).toEqual(['a', 'b', 'c']);
    });

    it('is empty with no accordion open', () => {
        installRows(STAGED_CARD);
        valUiOpenCategory.set(null);
        expect(accordionSequence()).toEqual([]);
    });
});

describe('accordionStep', () => {
    it('steps from the first piece to its sibling piece, not past the card', () => {
        installRows(STAGED_CARD);
        accordionNavCursor.set({ uid: 'parent', chapter: 1, index: 4, startMs: 1000, endMs: 2000 });
        expect(accordionStep(1)?.uid).toBe('piece-1');
    });

    it('steps from the last piece to the following segment', () => {
        installRows(STAGED_CARD);
        accordionNavCursor.set({ uid: 'piece-1', chapter: 1, index: 4, startMs: 2000, endMs: 3000 });
        expect(accordionStep(1)?.uid).toBe('next');
    });

    it('steps backwards piece by piece too', () => {
        installRows(STAGED_CARD);
        accordionNavCursor.set({ uid: 'piece-1', chapter: 1, index: 4, startMs: 2000, endMs: 3000 });
        expect(accordionStep(-1)?.uid).toBe('parent');
    });

    it('resolves the current piece from the live staged playhead when the cursor is unset', () => {
        installRows(STAGED_CARD);
        playingSegmentIndex.set({ chapter: 1, index: 4, origin: 'accordion' });
        stagedPlayheadWindow.set({ start: 2000, end: 3000 });
        expect(accordionStep(1)?.uid).toBe('next');
    });

    it('clamps at both ends', () => {
        installRows(STAGED_CARD);
        accordionNavCursor.set({ uid: 'next', chapter: 1, index: 5, startMs: 3000, endMs: 4000 });
        expect(accordionStep(1)?.uid).toBe('next');
        accordionNavCursor.set({ uid: 'prev', chapter: 1, index: 3, startMs: 0, endMs: 1000 });
        expect(accordionStep(-1)?.uid).toBe('prev');
    });

    it('falls back to the first/last stop when nothing is current', () => {
        installRows(STAGED_CARD);
        expect(accordionStep(1)?.uid).toBe('prev');
        expect(accordionStep(-1)?.uid).toBe('next');
    });

    it('returns null with no rows', () => {
        document.body.innerHTML = '';
        expect(accordionStep(1)).toBeNull();
    });
});

describe('isCurrentStop', () => {
    it('distinguishes two pieces of the same parent', () => {
        installRows(STAGED_CARD);
        accordionNavCursor.set({ uid: 'parent', chapter: 1, index: 4, startMs: 1000, endMs: 2000 });
        const seq = accordionSequence();
        expect(isCurrentStop(seq[1]!)).toBe(true);  // 'parent' piece
        expect(isCurrentStop(seq[2]!)).toBe(false); // sibling piece, same index
    });
});
