import { describe, expect, it } from 'vitest';

import { formatSeconds, profileSections } from '../recitation-profile';

describe('profileSections', () => {
    it('is empty without a profile', () => {
        expect(profileSections(null)).toEqual([]);
    });

    it('lists madd types, then ghunnah and pauses, lengths only where Hafs allows a choice', () => {
        const [madd, sound] = profileSections({
            madd: [
                { kind: 'tabii', mean_ms: 349 },
                { kind: 'munfasil', mean_ms: 1639, length: 'tawassut' },
                { kind: 'arid', mean_ms: 1985, length: 'ishbaa' },
            ],
            ghunnah_ms: 919,
            pause_ms: 453,
        });

        expect(madd?.section).toBe('madd');
        expect(madd?.rows.map((r) => [r.measure, r.ms])).toEqual([['tabii', 349], ['munfasil', 1639], ['arid', 1985]]);
        expect(madd?.rows[0]?.lengths).toEqual([]);
        expect(madd?.rows[1]?.lengths).toEqual([
            { length: 'qasr', count: 2, chosen: false },
            { length: 'tawassut', count: 4, chosen: true },
        ]);
        expect(madd?.rows[2]?.lengths.map((l) => [l.count, l.chosen])).toEqual([[2, false], [4, false], [6, true]]);
        expect(sound).toEqual({
            section: 'sound',
            rows: [{ measure: 'ghunnah', ms: 919, lengths: [] }, { measure: 'pauses', ms: 453, lengths: [] }],
        });
    });

    it('drops lengths for a choice type without a verdict and an empty section', () => {
        const sections = profileSections({ madd: [{ kind: 'leen', mean_ms: 1632, length: null }], ghunnah_ms: null });

        expect(sections).toEqual([{ section: 'madd', rows: [{ measure: 'leen', ms: 1632, lengths: [] }] }]);
    });
});

describe('formatSeconds', () => {
    it('gives two decimals in the locale digits', () => {
        expect(formatSeconds(1639, 'en')).toBe('1.64');
        expect(formatSeconds(453, 'en')).toBe('0.45');
        expect(formatSeconds(1639, 'ar')).toBe('١٫٦٤');
    });
});
