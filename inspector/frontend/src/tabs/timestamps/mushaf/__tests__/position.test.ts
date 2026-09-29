import { describe, expect, it } from 'vitest';

import type { AnimUnit } from '../../../../lib/recitation-animation/types';
import { buildWordIndex, indexLayout, pageOfWord } from '../layout';
import { indexChapter, positionAt } from '../position';

// Surah 1 v1 = ids 1..3 (id 3 = its marker), v2 = ids 4..6.
const DK = Object.fromEntries(
    [
        [1, '1:1:1', 'بِسْمِ'], [2, '1:1:2', 'ٱللَّهِ'], [3, '1:1:3', '۝١'],
        [4, '1:2:1', 'ٱلْحَمْدُ'], [5, '1:2:2', 'لِلَّهِ'], [6, '1:2:3', '۝٢'],
    ].map(([id, location, text]) => [location, { id, location, text }]),
);

function unit(loc: string, ...spans: [number, number][]): AnimUnit {
    const [s, a, w] = loc.split(':').map(Number);
    return {
        location: loc, ayahKey: `${s}:${a}`, surah: s!, ayah: a!, word: w!, text: '',
        start: spans[0]![0], end: spans.at(-1)![1],
        intervals: spans.map(([start, end]) => ({ start, end })), letters: [],
    };
}

const words = buildWordIndex(DK);
// v1 at 1-3s, v2 at 4-6s, then the reciter goes back to v1 word 2 at 7-8s.
const ix = indexChapter(1, [
    unit('1:1:1', [1, 2]),
    unit('1:1:2', [2, 3], [7, 8]),
    unit('1:2:1', [4, 5]),
    unit('1:2:2', [5, 6]),
], words);

describe('positionAt', () => {
    it('lights the word being recited', () => {
        expect(positionAt(ix, 4.5, -1).pos).toMatchObject({ activeId: 4, verseKey: '1:2', progressId: 4 });
    });

    it('holds the last recited word in silence', () => {
        expect(positionAt(ix, 3.5, -1).pos).toMatchObject({ activeId: 0, verseKey: '1:1', progressId: 2 });
    });

    it('follows a loop-back to an earlier verse', () => {
        expect(positionAt(ix, 7.5, -1).pos).toMatchObject({ activeId: 2, verseKey: '1:1' });
    });

    it('points at the first verse before anything is recited, with nothing reached', () => {
        expect(positionAt(ix, 0.2, -1).pos).toMatchObject({ activeId: 0, verseKey: '1:1', anchorId: 1, progressId: 0 });
    });

    it('seeks a verse from its first occurrence', () => {
        expect(ix.verseStartMs.get('1:1')).toBe(1000);
        expect(ix.verseStartMs.get('1:2')).toBe(4000);
    });
});

describe('pageOfWord', () => {
    const layout = indexLayout({
        year: '1421',
        pages: [[[0, 0, 1, 3]], [[0, 0, 4, 6]]],
        juz: [1, 1],
        hizb: [1, 1],
    });

    it('finds the page a word id sits on', () => {
        expect(pageOfWord(layout, 1)).toBe(1);
        expect(pageOfWord(layout, 3)).toBe(1);
        expect(pageOfWord(layout, 5)).toBe(2);
    });
});
