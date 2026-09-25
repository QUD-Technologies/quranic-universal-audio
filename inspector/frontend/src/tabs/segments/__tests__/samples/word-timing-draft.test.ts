import { describe, expect, it } from 'vitest';

import { contiguousWordDraft, moveWordBlock, moveWordBoundary } from '../../utils/samples/word-timing-draft';

const words = [
    { start_ms: 0, end_ms: 100 },
    { start_ms: 100, end_ms: 200 },
    { start_ms: 200, end_ms: 300 },
];

describe('word timing editor shared boundaries', () => {
    it('closes historical interior gaps before editing without changing the outer edges', () => {
        expect(contiguousWordDraft([
            { start_ms: 10, end_ms: 90 },
            { start_ms: 110, end_ms: 280 },
        ])).toEqual([{ start_ms: 10, end_ms: 110 }, { start_ms: 110, end_ms: 280 }]);
    });

    it('moves both sides of one interior boundary and preserves minimum word length', () => {
        expect(moveWordBoundary(words, 1, 120, 0, 300)).toEqual([
            { start_ms: 0, end_ms: 120 },
            { start_ms: 120, end_ms: 200 },
            { start_ms: 200, end_ms: 300 },
        ]);
        const clamped = moveWordBoundary(words, 1, 195, 0, 300);
        expect(clamped[0]!.end_ms).toBe(180);
        expect(clamped[1]!.start_ms).toBe(180);
    });

    it('keeps the first start and last end independent of their neighbors', () => {
        expect(moveWordBoundary(words, 0, 25, 0, 300)[0]).toEqual({ start_ms: 25, end_ms: 100 });
        expect(moveWordBoundary(words, 3, 275, 0, 300)[2]).toEqual({ start_ms: 200, end_ms: 275 });
    });

    it('carries adjacent shared boundaries when a whole word moves', () => {
        expect(moveWordBlock(words, 1, 30, 0, 300)).toEqual([
            { start_ms: 0, end_ms: 130 },
            { start_ms: 130, end_ms: 230 },
            { start_ms: 230, end_ms: 300 },
        ]);
        const clamped = moveWordBlock(words, 1, 200, 0, 300);
        expect(clamped[1]).toEqual({ start_ms: 180, end_ms: 280 });
        expect(clamped[0]!.end_ms).toBe(clamped[1]!.start_ms);
        expect(clamped[1]!.end_ms).toBe(clamped[2]!.start_ms);
    });
});
