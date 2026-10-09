import type { CellBoundary } from '@quranic-phonemizer/cells';
import { describe, expect, it } from 'vitest';

import { adoptDisplayStopMark } from '../display-stop-mark';

const JEEM = 'ۚ'; // ۚ
const QILA = 'ۗ'; // ۗ
const SALA = 'ۖ'; // ۖ
const SEEN = 'ۜ'; // ۜ sakt

function boundary(stopText: string): CellBoundary {
    return { columns: [{ role: 'stop_sign', text: stopText }] } as unknown as CellBoundary;
}

const stopText = (b: CellBoundary) => b.columns.find((c) => c.role === 'stop_sign')?.text;

describe('adoptDisplayStopMark', () => {
    it('lifts a mark the shard lacks out of the word (84:15:1)', () => {
        const b = boundary('');
        expect(adoptDisplayStopMark(`بَلَىٰٓ${JEEM}`, b)).toBe('بَلَىٰٓ');
        expect(stopText(b)).toBe(JEEM);
    });

    it('draws the display mark once where the editions differ (33:1:8)', () => {
        const b = boundary(QILA);
        expect(adoptDisplayStopMark(`وَٱلْمُنَٰفِقِينَ${JEEM}`, b)).toBe('وَٱلْمُنَٰفِقِينَ');
        expect(stopText(b)).toBe(JEEM);
    });

    it('drops a mark the display text does not write (3:49:10)', () => {
        const b = boundary(SALA);
        expect(adoptDisplayStopMark('رَّبِّكُمْ', b)).toBe('رَّبِّكُمْ');
        expect(stopText(b)).toBe('');
    });

    it('keeps an agreeing mark in the tile only', () => {
        const b = boundary(SALA);
        expect(adoptDisplayStopMark(`كَلَّا${SALA}`, b)).toBe('كَلَّا');
        expect(stopText(b)).toBe(SALA);
    });

    it('keeps a sakt seen the producer placed in the column', () => {
        const b = boundary(SEEN);
        expect(adoptDisplayStopMark(`عِوَجَا${SEEN}`, b)).toBe('عِوَجَا');
        expect(stopText(b)).toBe(SEEN);
    });
});
