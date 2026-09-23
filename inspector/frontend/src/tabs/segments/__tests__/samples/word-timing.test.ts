import { describe, expect, it } from 'vitest';

import { displayWordsForTimings, timingsMatchRef, wordIndexAt } from '../../utils/samples/word-timing';

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
