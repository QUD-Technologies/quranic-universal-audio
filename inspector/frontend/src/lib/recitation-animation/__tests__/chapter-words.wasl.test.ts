import { describe, expect, it } from 'vitest';

import { type AssembledVerse, buildChapterRecitation } from '../chapter-words';
import type { TsVerseData } from '../../types/ts-client';

/** A word row carrying only the fields `buildChapterRecitation` reads. */
function w(location: string, start: number, end: number) {
    return { location, start, end, text: location, letters: [] };
}

/** Minimal AssembledVerse — `data` cast to the fields the build consumes. */
function av(
    verseRef: string,
    words: ReturnType<typeof w>[],
    time_start_ms: number,
    waslOutTo: string | null = null,
): AssembledVerse {
    return {
        verseRef,
        waslOutTo,
        data: { time_start_ms, words } as unknown as TsVerseData,
    };
}

describe('buildChapterRecitation — waslTo threading', () => {
    it('stamps waslTo on the LAST word of the bridging take only', () => {
        const built = buildChapterRecitation('r', 20, [
            av('20:25', [w('20:25:1', 0, 0.5), w('20:25:4', 0.5, 1.0)], 0, '20:26'),
            av('20:26', [w('20:26:1', 0, 0.5), w('20:26:5', 0.5, 1.0)], 1000),
        ]);
        const byLoc = new Map(built.units.map((u) => [u.location, u]));
        expect(byLoc.get('20:25:4')!.intervals[0]!.waslTo).toBe('20:26');
        // The non-last word of the bridging take carries nothing.
        expect(byLoc.get('20:25:1')!.intervals[0]!.waslTo).toBeUndefined();
        // The entered verse carries nothing.
        expect(byLoc.get('20:26:5')!.intervals[0]!.waslTo).toBeUndefined();
    });

    it('keeps waslTo per-occurrence: a repeated last word bridges on one take only', () => {
        // 14:1 full (stop) | 14:2 | 14:1[13-16] (waṣl»14:2) | 14:2
        const built = buildChapterRecitation('r', 14, [
            av('14:1', [w('14:1:1', 0, 0.5), w('14:1:16', 0.5, 1.0)], 0), // stop take
            av('14:2', [w('14:2:1', 0, 0.5), w('14:2:9', 0.5, 1.0)], 1000),
            av('14:1', [w('14:1:13', 0, 0.5), w('14:1:16', 0.5, 1.0)], 2000, '14:2'), // waṣl take
            av('14:2', [w('14:2:1', 0, 0.5), w('14:2:9', 0.5, 1.0)], 3000),
        ]);
        const last = built.units.find((u) => u.location === '14:1:16')!;
        // Two occurrences of 14:1:16; exactly one (the waṣl take) carries waslTo.
        const bridging = last.intervals.filter((iv) => iv.waslTo === '14:2');
        expect(bridging).toHaveLength(1);
        expect(last.intervals.length).toBe(2);
    });
});
