/**
 * Mark-ready blocking counts — resolved cross-verse items must not gate.
 */
import { describe, expect, it } from 'vitest';

import type { SegValidateResponse } from '../../../../lib/types/generated/schemas';
import { blockingCountFor } from '../../utils/validation/blocking-count';

const resp = (o: Record<string, unknown>): SegValidateResponse => o as unknown as SegValidateResponse;
const live = new Set(['a', 'b', 'c']);

describe('blockingCountFor', () => {
    it('counts only unresolved cross-verse items', () => {
        const v = resp({
            cross_verse: [
                { chapter: 2, seg_index: 0, segment_uid: 'a', ref: '2:1:1-2:2:2' },
                { chapter: 2, seg_index: 1, segment_uid: 'b', ref: '2:3:1-2:3:4', resolved: true },
                { chapter: 2, seg_index: 2, segment_uid: 'c', ref: '2:4:1-2:4:2', resolved: true },
            ],
        });
        expect(blockingCountFor('cross_verse', v, live, 90)).toBe(1);
    });

    it('is zero when every cross-verse item is resolved', () => {
        const v = resp({
            cross_verse: [{ chapter: 2, seg_index: 0, segment_uid: 'a', ref: '2:1:1-2:1:2', resolved: true }],
        });
        expect(blockingCountFor('cross_verse', v, live, 90)).toBe(0);
    });

    it('drops stale uids before counting', () => {
        const v = resp({ repetitions: [{ chapter: 2, seg_index: 0, segment_uid: 'gone' }] });
        expect(blockingCountFor('repetitions', v, live, 90)).toBe(0);
    });

    it('applies the FE threshold to low_confidence', () => {
        const v = resp({
            low_confidence: [
                { chapter: 2, seg_index: 0, segment_uid: 'a', confidence: 0.5 },
                { chapter: 2, seg_index: 1, segment_uid: 'b', confidence: 0.95 },
            ],
        });
        expect(blockingCountFor('low_confidence', v, live, 90)).toBe(1);
    });

    it('is zero without a response or an empty slot', () => {
        expect(blockingCountFor('cross_verse', null, live, 90)).toBe(0);
        expect(blockingCountFor('cross_verse', resp({ cross_verse: [] }), live, 90)).toBe(0);
    });
});
