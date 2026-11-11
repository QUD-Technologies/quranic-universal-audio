import { describe, expect, it } from 'vitest';

import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../lib/types/view-models';
import { cutSilences, gapFits } from '../../utils/edit/gap-split';

const item = (cuts: object[]) => ({ boundary: { cuts } }) as unknown as SegValAnyItem;
const seg = (time_start: number, time_end: number) => ({ time_start, time_end }) as Segment;

describe('cutSilences', () => {
    it('reads each cursor’s measured silence', () => {
        const it0 = item([
            { cursor_ms: 300, silence_start_ms: 250, silence_end_ms: 380 },
            { cursor_ms: 700 },
        ]);
        expect(cutSilences(it0, [300, 700])).toEqual([[250, 380], null]);
    });

    it('drops a silence that does not hold its cursor', () => {
        expect(cutSilences(item([{ cursor_ms: 300, silence_start_ms: 310, silence_end_ms: 380 }]), [300])).toEqual([null]);
        expect(cutSilences(null, [300])).toEqual([null]);
    });
});

describe('gapFits', () => {
    it('keeps both pieces playable', () => {
        expect(gapFits(seg(0, 300), seg(300, 1000), [250, 380])).toBe(true);
        expect(gapFits(seg(0, 300), seg(300, 1000), [20, 380])).toBe(false);
        expect(gapFits(seg(0, 300), seg(300, 1000), [250, 980])).toBe(false);
    });
});
