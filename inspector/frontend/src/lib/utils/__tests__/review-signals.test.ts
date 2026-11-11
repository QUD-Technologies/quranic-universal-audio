import { describe, expect, it } from 'vitest';

import { REVIEW_LOW, REVIEW_LOW_V2, REVIEW_NONE, reviewTagLabel, reviewTags } from '../review-signals';

describe('review-signals', () => {
    it('tags a delivery with each signal that has open items', () => {
        expect(reviewTags({ low_confidence: 3, low_confidence_v2: 2 })).toEqual([REVIEW_LOW, REVIEW_LOW_V2]);
        expect(reviewTags({ low_confidence: 0, low_confidence_v2: 4 })).toEqual([REVIEW_LOW_V2]);
    });

    it('tags none when the counts are zero or not in yet', () => {
        expect(reviewTags({ low_confidence: 0, low_confidence_v2: 0 })).toEqual([REVIEW_NONE]);
        expect(reviewTags(undefined)).toEqual([REVIEW_NONE]);
    });

    it('labels every rail tag distinctly', () => {
        const labels = [REVIEW_LOW, REVIEW_LOW_V2, REVIEW_NONE].map(reviewTagLabel);
        expect(new Set(labels).size).toBe(3);
        expect(labels.every((l) => l.length > 0)).toBe(true);
    });
});
