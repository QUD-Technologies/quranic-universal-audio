import { repeatSteps } from '@qud-technologies/mushaf-core/app';
import { describe, expect, it } from 'vitest';

import type { AnimUnit } from '../../../../lib/recitation-animation/types';
import { chapterTiming, planSurahs, playSource } from '../repeat-source';

/** A word unit with one take (seconds). */
function unit(location: string, start: number, end: number): AnimUnit {
    const [surah, ayah, word] = location.split(':').map(Number) as [number, number, number];
    return { location, ayahKey: `${surah}:${ayah}`, surah, ayah, word, text: '', start, end, intervals: [{ start, end }], letters: [] };
}

const units = [unit('0:2:1', 0, 1), unit('112:1:1', 1, 2), unit('112:1:2', 2, 3), unit('112:2:1', 3, 4)];

describe('repeat source', () => {
    it('times each verse by its take and leaves opener readings out', () => {
        const t = chapterTiming(112, units);
        expect(t.words.map((w) => w.anchor)).toEqual(['112:1:1', '112:1:2', '112:2:1']);
        expect(t.verses).toEqual([{ ayah: 1, start: 1000, end: 3000 }, { ayah: 2, start: 3000, end: 4000 }]);
    });

    it('plays a plan with the core: each verse twice, the range once', () => {
        const source = playSource([112], new Map([[112, chapterTiming(112, units)]]));
        const steps = [...repeatSteps(source, { from: '112:1', to: '112:2', verseTimes: 2, rangeTimes: 1, pauseMs: 0 })];
        expect(steps.map((s) => [s.verse, s.from, s.to])).toEqual([[1, 1000, 3000], [1, 1000, 3000], [2, 3000, 4000], [2, 3000, 4000]]);
    });

    it('lists the surahs a plan crosses', () => {
        expect(planSurahs('2:255', '4:1')).toEqual([2, 3, 4]);
    });
});
