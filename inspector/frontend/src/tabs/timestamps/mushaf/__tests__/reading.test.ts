import { describe, expect, it } from 'vitest';

import type { AnimUnit } from '../../../../lib/recitation-animation/types';
import { recitationStops } from '../reading';

/** A word unit with the given takes (seconds). */
function unit(location: string, takes: [number, number][]): AnimUnit {
    const [surah, ayah, word] = location.split(':').map(Number) as [number, number, number];
    return {
        location, ayahKey: `${surah}:${ayah}`, surah, ayah, word, text: '',
        start: takes[0]![0], end: takes.at(-1)![1],
        intervals: takes.map(([start, end]) => ({ start, end })),
        letters: [],
    };
}

// 2:2 ... لَا رَيْبَ ۛ فِيهِ ۛ هُدًى: a mu'anaqah pair on words 4 and 5.
const signed = (a: string): boolean => a === '2:2:4' || a === '2:2:5';

describe('recitationStops', () => {
    it('marks the sign the reciter pauses on, and the one read on through', () => {
        const units = [unit('2:2:3', [[10, 10.5]]), unit('2:2:4', [[10.5, 11]]), unit('2:2:5', [[11.6, 12]]), unit('2:2:6', [[12, 12.4]])];
        expect(recitationStops(units, signed)).toEqual({ '2:2:4': true, '2:2:5': false });
    });

    it('reads a gap within the SDK tolerance as reading on', () => {
        const units = [unit('2:2:4', [[10.5, 11]]), unit('2:2:5', [[11.03, 12]]), unit('2:2:6', [[12.6, 13]])];
        expect(recitationStops(units, signed)).toEqual({ '2:2:4': false, '2:2:5': true });
    });

    it('counts a stop on any take of a word read twice', () => {
        // Stops on رَيْبَ, goes back to لَا, then reads on through both.
        const units = [unit('2:2:3', [[10, 10.4], [11.5, 11.9]]), unit('2:2:4', [[10.4, 10.9], [11.9, 12.3]]), unit('2:2:5', [[12.3, 12.8]]), unit('2:2:6', [[12.8, 13.2]])];
        expect(recitationStops(units, signed)).toEqual({ '2:2:4': true, '2:2:5': false });
    });

    it('leaves out opener readings and words without a sign', () => {
        const units = [unit('2:2:3', [[10, 10.4]]), { ...unit('2:2:4', [[1, 2]]), location: '0:2:4' }];
        expect(recitationStops(units, () => true)).toEqual({ '2:2:3': true });
    });
});
