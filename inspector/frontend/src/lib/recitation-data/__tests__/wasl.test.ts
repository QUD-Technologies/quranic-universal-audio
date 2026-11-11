import { describe, expect, it } from 'vitest';

import { chapterOccasions } from '../occasions';
import { nativeReading } from '../test-native-fixture';
import { isInWaslGroup, waslGroupOf } from '../wasl';

describe('native connected-reading groups', () => {
    const chain = chapterOccasions([
        nativeReading('r1', [
            { ref: '79:1', start: 100, end: 1000 },
            { ref: '79:2', start: 1000, end: 2000 },
            { ref: '79:3', start: 2000, end: 3000 },
        ]),
        nativeReading('r2', [{ ref: '79:4', start: 3100, end: 4200 }]),
    ], 79);

    it('expands from any connected member without crossing reading identity', () => {
        expect(waslGroupOf(chain, 1)).toEqual({
            fromIdx: 0,
            toIdx: 2,
            refs: ['79:1', '79:2', '79:3'],
            startMs: 100,
            endMs: 3000,
        });
    });

    it('marks only members of a multi-part native reading as connected', () => {
        expect([0, 1, 2].every((index) => isInWaslGroup(chain, index))).toBe(true);
        expect(isInWaslGroup(chain, 3)).toBe(false);
    });
});
