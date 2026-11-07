import { describe, expect, it } from 'vitest';

import type { SegSpecialsResponse, SpecialEntry } from '../../../../lib/types/generated/schemas';
import { listedSpecials, specialPreviewUid } from '../../utils/specials/chapter-specials';

function entry(over: Partial<SpecialEntry>): SpecialEntry {
    return { type: 'Basmala', start_ms: 0, end_ms: 1000, source: 'align', ...over };
}

describe('listedSpecials', () => {
    it('lists the recorded openers and closers in time order', () => {
        const doc: SegSpecialsResponse = {
            chapters: {
                '2': [
                    entry({ type: 'Basmala', start_ms: 4000, end_ms: 8000 }),
                    entry({ type: "Isti'adha", start_ms: 0, end_ms: 3500 }),
                ],
            },
        };
        expect(listedSpecials(doc, 2).map((e) => e.type)).toEqual(["Isti'adha", 'Basmala']);
    });

    it('leaves out entries that already are segment rows', () => {
        const doc: SegSpecialsResponse = {
            chapters: {
                '1': [
                    entry({ type: "Isti'adha", start_ms: 0, end_ms: 3000 }),
                    entry({ type: 'Basmala', start_ms: 3200, end_ms: 6000, source: 'fatiha_verse' }),
                ],
                '91': [entry({ type: 'Basmala', start_ms: 0, end_ms: 5450, source: 'in_detailed' })],
            },
        };
        expect(listedSpecials(doc, 1).map((e) => e.type)).toEqual(["Isti'adha"]);
        expect(listedSpecials(doc, 91)).toEqual([]);
    });

    it('is empty without a doc or a chapter entry', () => {
        expect(listedSpecials(null, 2)).toEqual([]);
        expect(listedSpecials({ chapters: {} }, 2)).toEqual([]);
    });
});

describe('specialPreviewUid', () => {
    it('is unique per chapter, type and start', () => {
        const a = specialPreviewUid(2, entry({ start_ms: 10 }));
        const b = specialPreviewUid(2, entry({ type: "Isti'adha", start_ms: 10 }));
        const c = specialPreviewUid(3, entry({ start_ms: 10 }));
        expect(new Set([a, b, c]).size).toBe(3);
    });
});
