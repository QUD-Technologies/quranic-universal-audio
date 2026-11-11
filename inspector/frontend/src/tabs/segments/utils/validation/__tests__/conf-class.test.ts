import { describe, expect, it } from 'vitest';

import { getConfClass } from '../conf-class';

describe('getConfClass', () => {
    const ref = '1:1:1-1:1:4';

    it('colours a segment under the 85% Low Confidence default as mid, not high', () => {
        expect(getConfClass({ matched_ref: ref, confidence: 0.84 })).toBe('conf-mid');
        expect(getConfClass({ matched_ref: ref, confidence: 0.8 })).toBe('conf-mid');
    });

    it('colours 85% and above high', () => {
        expect(getConfClass({ matched_ref: ref, confidence: 0.85 })).toBe('conf-high');
    });

    it('colours under 60% low and a segment without a ref as failed', () => {
        expect(getConfClass({ matched_ref: ref, confidence: 0.59 })).toBe('conf-low');
        expect(getConfClass({ confidence: 0.99 })).toBe('conf-fail');
    });
});
