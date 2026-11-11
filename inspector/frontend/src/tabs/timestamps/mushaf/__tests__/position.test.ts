import { describe, expect, it } from 'vitest';

import type { AnimUnit } from '../../../../lib/recitation-animation/types';
import { compareAnchors, indexChapter, positionAt } from '../position';

function unit(loc: string, ...spans: [number, number][]): AnimUnit {
    const [s, a, w] = loc.split(':').map(Number);
    return {
        location: loc, ayahKey: `${s}:${a}`, surah: s!, ayah: a!, word: w!, text: '',
        start: spans[0]![0], end: spans.at(-1)![1],
        intervals: spans.map(([start, end]) => ({ start, end })), letters: [],
    };
}

// v1 at 1-3s, v2 at 4-6s, then the reciter goes back to v1 word 2 at 7-8s.
const ix = indexChapter(1, [
    unit('1:1:1', [1, 2]),
    unit('1:1:2', [2, 3], [7, 8]),
    unit('1:2:1', [4, 5]),
    unit('1:2:2', [5, 6]),
]);

describe('positionAt', () => {
    it('lights the word being recited', () => {
        expect(positionAt(ix, 4.5, -1).pos).toMatchObject({ active: '1:2:1', verseKey: '1:2', progress: '1:2:1' });
    });

    it('holds the last recited word in silence', () => {
        expect(positionAt(ix, 3.5, -1).pos).toMatchObject({ active: '', verseKey: '1:1', progress: '1:1:2' });
    });

    it('follows a loop-back to an earlier verse', () => {
        expect(positionAt(ix, 7.5, -1).pos).toMatchObject({ active: '1:1:2', verseKey: '1:1' });
    });

    it('points at the first verse before anything is recited, with nothing reached', () => {
        expect(positionAt(ix, 0.2, -1).pos).toMatchObject({ active: '', verseKey: '1:1', anchor: '1:1:1', progress: '' });
        expect(ix.firstAnchor).toBe('1:1:1');
    });

    it('seeks a verse from its first occurrence', () => {
        expect(ix.verseStartMs.get('1:1')).toBe(1000);
        expect(ix.verseStartMs.get('1:2')).toBe(4000);
    });

    it('never points the mushaf at an opener reading it does not print', () => {
        const withOpener = indexChapter(2, [unit('0:2:1', [0, 1]), unit('2:1:1', [1, 2])]);
        expect(withOpener.firstAnchor).toBe('2:1:1');
        expect(positionAt(withOpener, 0.5, -1).pos).toMatchObject({ active: '', anchor: '' });
    });
});

describe('compareAnchors', () => {
    it('orders words in reading order across ayahs and surahs', () => {
        expect(['2:1:3', '1:7:9', '2:1:10', '2:10:1'].sort(compareAnchors)).toEqual(['1:7:9', '2:1:3', '2:1:10', '2:10:1']);
    });
});
