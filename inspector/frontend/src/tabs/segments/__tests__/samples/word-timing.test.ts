import { describe, expect, it } from 'vitest';

import { displayWordsForTimings, steadyWordIndexAt, timingsMatchRef, wordIndexAt } from '../../utils/samples/word-timing';

const timings = [
    { word: 'one', location: '35:1:1', start_ms: 100, end_ms: 200 },
    { word: 'two', location: '35:1:2', start_ms: 200, end_ms: 350 },
];

describe('sample word timing', () => {
    it('selects the sounding word at shared boundaries', () => {
        expect(wordIndexAt(100, timings)).toBe(0);
        expect(wordIndexAt(199, timings)).toBe(0);
        expect(wordIndexAt(200, timings)).toBe(1);
        expect(wordIndexAt(350, timings)).toBe(1);
        expect(wordIndexAt(99, timings)).toBe(-1);
    });

    it('rejects timings after the row reference changes', () => {
        expect(timingsMatchRef('35:1:1-35:1:2', timings)).toBe(true);
        expect(timingsMatchRef('35:1:1-35:1:3', timings)).toBe(false);
        expect(timingsMatchRef('Basmala', timings)).toBe(false);
    });

    it('highlights the same Quran text as the ordinary row, including verse markers', () => {
        const words = { '35:1:1': 'ٱلْحَمْدُ', '35:1:2': 'لِلَّهِ' };
        const display = 'ٱلْحَمْدُ لِلَّهِ ۝١';
        expect(displayWordsForTimings(timings, display, words, { '35:1': 2 }, '۝'))
            .toEqual(['ٱلْحَمْدُ', 'لِلَّهِ ۝١']);
        expect(displayWordsForTimings(timings, display, { '35:1:1': 'ٱلْحَمْدُ' }, { '35:1': 2 }, '۝'))
            .toEqual([]);
    });
});

describe('steadyWordIndexAt', () => {
    const words = [
        { location: '2:2:1', start_ms: 1000, end_ms: 1500 },
        { location: '2:2:2', start_ms: 1500, end_ms: 2200 },
        { location: '2:2:3', start_ms: 2200, end_ms: 3000 },
    ];

    it('moves forward with the clock', () => {
        expect(steadyWordIndexAt(1490, words, 0)).toBe(0);
        expect(steadyWordIndexAt(1510, words, 0)).toBe(1);
    });

    it('holds the lit word through clock jitter at its start edge', () => {
        expect(steadyWordIndexAt(1480, words, 1)).toBe(1);
        expect(steadyWordIndexAt(990, words, 0)).toBe(0);
    });

    it('steps back on a real seek', () => {
        expect(steadyWordIndexAt(1100, words, 2)).toBe(0);
        expect(steadyWordIndexAt(1200, words, 1)).toBe(0);
    });

    it('lights from scratch when nothing is held', () => {
        expect(steadyWordIndexAt(2500, words, -1)).toBe(2);
        expect(steadyWordIndexAt(900, words, -1)).toBe(-1);
    });
});
