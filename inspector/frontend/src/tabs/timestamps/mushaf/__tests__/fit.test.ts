import { describe, expect, it } from 'vitest';

import { fitPages, lineEm, MIN_WORD_GAP_EM, PITCH_MAX, PITCH_MIN, spreadFontPx } from '../fit';

const REF_EM = 17;

describe('fitPages', () => {
    it('shows a spread on a wide desktop window, sized by height', () => {
        const m = fitPages({ width: 1600, height: 820 }, REF_EM);
        expect(m.spread).toBe(true);
        expect(m.pitchPx / m.fontPx).toBeCloseTo(PITCH_MIN, 5);
        expect(2 * m.pageWidthPx).toBeLessThanOrEqual(1600);
        expect(m.pageHeightPx).toBeLessThanOrEqual(820);
    });

    it('falls back to one page when two would be too small', () => {
        const m = fitPages({ width: 700, height: 900 }, REF_EM);
        expect(m.spread).toBe(false);
        expect(m.pageWidthPx).toBeLessThanOrEqual(700);
    });

    it('caps line spacing when width is the binding constraint', () => {
        const m = fitPages({ width: 900, height: 2000 }, REF_EM);
        expect(m.pitchPx / m.fontPx).toBeLessThanOrEqual(PITCH_MAX + 1e-9);
    });

    it('keeps the column exactly one reference line wide', () => {
        const m = fitPages({ width: 1600, height: 820 }, REF_EM);
        expect(m.columnPx).toBeCloseTo(REF_EM * m.fontPx, 5);
    });
});

describe('spreadFontPx', () => {
    const m = fitPages({ width: 1600, height: 820 }, REF_EM);

    it('keeps the base size when the densest line fits', () => {
        expect(spreadFontPx(m, REF_EM - 1)).toBe(m.fontPx);
    });

    it('shrinks just enough for a denser spread to fit the column', () => {
        const f = spreadFontPx(m, REF_EM * 1.1);
        expect(f * REF_EM * 1.1).toBeCloseTo(m.columnPx, 5);
    });
});

describe('lineEm', () => {
    it('adds the minimum gap between words', () => {
        expect(lineEm([1, 2, 3])).toBeCloseTo(6 + 2 * MIN_WORD_GAP_EM, 9);
        expect(lineEm([])).toBe(0);
    });
});
