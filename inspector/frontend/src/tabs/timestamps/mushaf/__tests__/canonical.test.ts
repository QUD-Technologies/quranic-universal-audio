import { describe, expect, it } from 'vitest';

import type { AnimUnit } from '../../../../lib/recitation-animation/types';
import { canonicalTakes } from '../canonical';

/** Unit for word `w` of verse `s:a`, recited at each [start, end] (seconds). */
function unit(s: number, a: number, w: number, ...spans: [number, number][]): AnimUnit {
    return {
        location: `${s}:${a}:${w}`,
        ayahKey: `${s}:${a}`,
        surah: s,
        ayah: a,
        word: w,
        text: '',
        start: spans[0]![0],
        end: spans.at(-1)![1],
        intervals: spans.map(([start, end]) => ({ start, end })),
        letters: [],
    };
}

describe('canonicalTakes', () => {
    it('takes a verse recited once as-is', () => {
        const takes = canonicalTakes([unit(1, 1, 1, [0, 1]), unit(1, 1, 2, [1, 2]), unit(1, 2, 1, [3, 4])]);
        expect(takes.get('1:1')).toEqual({ startMs: 0, endMs: 2000 });
        expect(takes.get('1:2')).toEqual({ startMs: 3000, endMs: 4000 });
    });

    it('drops a leading false start that restarts the verse', () => {
        // Word 1 at 0-1, then restart: words 1,2,3 at 2-5.
        const takes = canonicalTakes([
            unit(2, 1, 1, [0, 1], [2, 3]),
            unit(2, 1, 2, [3, 4]),
            unit(2, 1, 3, [4, 5]),
        ]);
        expect(takes.get('2:1')).toEqual({ startMs: 2000, endMs: 5000 });
    });

    it('keeps the first complete take when the verse is re-recited after the next one', () => {
        // 1:1 fully at 0-2, 1:2 at 3-4, then 1:1 again at 5-7 (a loop-back).
        const takes = canonicalTakes([
            unit(1, 1, 1, [0, 1], [5, 6]),
            unit(1, 1, 2, [1, 2], [6, 7]),
            unit(1, 2, 1, [3, 4]),
        ]);
        expect(takes.get('1:1')).toEqual({ startMs: 0, endMs: 2000 });
    });

    it('cuts the take where the verse completes', () => {
        // Words 1,2 then a repeat of word 2 after completion.
        const takes = canonicalTakes([unit(3, 1, 1, [0, 1]), unit(3, 1, 2, [1, 2], [2.5, 3.5])]);
        expect(takes.get('3:1')).toEqual({ startMs: 0, endMs: 2000 });
    });

    it('keeps a lookback inside the take', () => {
        // 1, 2, back to 2, then 3 — a mid-verse lookback with no word-1 restart.
        const takes = canonicalTakes([
            unit(4, 1, 1, [0, 1]),
            unit(4, 1, 2, [1, 2], [3, 4]),
            unit(4, 1, 3, [4, 5]),
        ]);
        expect(takes.get('4:1')).toEqual({ startMs: 0, endMs: 5000 });
    });
});
