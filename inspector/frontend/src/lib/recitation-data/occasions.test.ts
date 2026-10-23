import { describe, expect, it } from 'vitest';

import { chapterOccasions } from './occasions';
import { nativeReading } from './test-native-fixture';

describe('chapterOccasions v12', () => {
    it('keeps a connected reading across verse occasions', () => {
        const reading = nativeReading('r1', [
            { ref: '1:3', start: 0, end: 1000 },
            { ref: '1:4', start: 1000, end: 2000 },
        ]);
        const occasions = chapterOccasions([reading], 1);
        expect(occasions.map((one) => one.ref)).toEqual(['1:3', '1:4']);
        expect(occasions[0]!.waslOutTo).toBe('1:4');
        expect(occasions[1]!.waslOutTo).toBeNull();
    });

    it('orders independent readings by absolute part time', () => {
        const later = nativeReading('r2', [{ ref: '1:2', start: 1000, end: 2000 }]);
        const first = nativeReading('r1', [{ ref: '1:1', start: 0, end: 900 }]);
        expect(chapterOccasions([later, first], 1).map((one) => one.ref)).toEqual(['1:1', '1:2']);
    });

    it('does not invent a bridge between distinct native readings', () => {
        const occasions = chapterOccasions([
            nativeReading('r1', [{ ref: '14:1', start: 0, end: 1000 }]),
            nativeReading('r2', [{ ref: '14:2', start: 1000, end: 2000 }]),
        ], 14);
        expect(occasions.every((one) => one.waslOutTo === null)).toBe(true);
    });
});
