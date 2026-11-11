import { describe, expect, it } from 'vitest';

import { lowConfidenceTotal, REVIEW_LOW, REVIEW_NONE, reviewTagLabel, reviewTags } from '../review-signals';

describe('review-signals', () => {
    it('tags a delivery low confidence when either kind has open items', () => {
        expect(reviewTags({ low_confidence: 3, low_confidence_v2: 0 })).toEqual([REVIEW_LOW]);
        expect(reviewTags({ low_confidence: 0, low_confidence_v2: 2 })).toEqual([REVIEW_LOW]);
    });

    it('tags none when the counts are zero or not in yet', () => {
        expect(reviewTags({ low_confidence: 0, low_confidence_v2: 0 })).toEqual([REVIEW_NONE]);
        expect(reviewTags(undefined)).toEqual([REVIEW_NONE]);
    });

    it('sums both kinds into one count', () => {
        expect(lowConfidenceTotal({ low_confidence: 3, low_confidence_v2: 2 })).toBe(5);
        expect(lowConfidenceTotal(undefined)).toBe(0);
    });

    it('labels both rail tags distinctly', () => {
        const labels = [REVIEW_LOW, REVIEW_NONE].map(reviewTagLabel);
        expect(new Set(labels).size).toBe(2);
    });
});
