/** A freshly opened recitation lands on its first chapter — chapter 1 when present, so its Basmala shows. */

import { describe, expect, it } from 'vitest';

import type { SegAllState } from '../../stores/chapter';
import { firstChapter } from '../../utils/data/reciter-actions';

const state = (...chapters: number[]) =>
    ({ segments: chapters.map((chapter) => ({ chapter })) }) as unknown as SegAllState;

describe('firstChapter', () => {
    it('picks chapter 1 when the recitation has it', () => {
        expect(firstChapter(state(2, 1, 114))).toBe('1');
    });

    it('falls back to the lowest chapter, or none', () => {
        expect([firstChapter(state(78, 36)), firstChapter(state()), firstChapter(null)]).toEqual(['36', '', '']);
    });
});
