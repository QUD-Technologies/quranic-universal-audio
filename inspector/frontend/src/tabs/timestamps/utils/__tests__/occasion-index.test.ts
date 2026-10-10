import { describe, expect, it } from 'vitest';

import { occasionIndexOfRef } from '../occasion-index';

// 68:1 stopped after نٓ, then recited again joined.
const OCCASIONS = [
    { ref: '68:1', startMs: 14570 },
    { ref: '68:1', startMs: 24245 },
    { ref: '68:2', startMs: 33000 },
];

describe('occasionIndexOfRef', () => {
    it('lands on the rendition starting at the given time', () => {
        expect(occasionIndexOfRef(OCCASIONS, '68:1', 24245)).toBe(1);
    });

    it('falls back to the first rendition without a time', () => {
        expect(occasionIndexOfRef(OCCASIONS, '68:1', null)).toBe(0);
    });

    it('is -1 for a verse the chapter lacks', () => {
        expect(occasionIndexOfRef(OCCASIONS, '68:3', 24245)).toBe(-1);
    });
});
