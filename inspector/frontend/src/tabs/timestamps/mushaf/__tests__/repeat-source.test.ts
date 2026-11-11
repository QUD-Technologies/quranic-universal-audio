import { repeatSteps, type SurahStep } from '@qud-technologies/mushaf-core/app';
import { describe, expect, it } from 'vitest';

import type { AnimUnit } from '../../../../lib/recitation-animation/types';
import { chapterTiming, type NeedsSurah, planSurahs, playSource, RepeatQueue } from '../repeat-source';

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

describe('RepeatQueue', () => {
    const timing113 = chapterTiming(113, [unit('113:1:1', 0, 5)]);
    const timing112 = chapterTiming(112, units);
    const isStep = (x: SurahStep | NeedsSurah | null): x is SurahStep => x !== null && 'kind' in x;

    /** Drain a queue, giving each surah's timing when asked; records what was asked for, in order. */
    function drain(queue: RepeatQueue, limit: number): { asked: number[]; played: string[] } {
        const asked: number[] = [];
        const played: string[] = [];
        while (played.length < limit) {
            const next = queue.next();
            if (next === null) break;
            if (isStep(next)) {
                played.push(`${next.repeatAt?.round}/${next.surah}:${next.verse}`);
                continue;
            }
            asked.push(next.load);
            queue.provide(next.load, next.load === 112 ? timing112 : timing113);
        }
        return { asked, played };
    }

    it('asks for a surah only when the plan reaches it', () => {
        const queue = new RepeatQueue(
            { from: '112:1', to: '113:1', verseTimes: 1, rangeTimes: 1, pauseMs: 0 },
            [112, 113],
        );

        expect(queue.next()).toEqual({ load: 112 });
        queue.provide(112, timing112);
        expect([queue.next(), queue.next()].map((s) => isStep(s) && s.verse)).toEqual([1, 2]);
        expect(queue.next()).toEqual({ load: 113 });
    });

    it('plays every round without asking for a surah twice', () => {
        const queue = new RepeatQueue(
            { from: '112:2', to: '113:1', verseTimes: 1, rangeTimes: 2, pauseMs: 0 },
            [112, 113],
        );

        expect(drain(queue, 10)).toEqual({
            asked: [112, 113],
            played: ['0/112:2', '0/113:1', '1/112:2', '1/113:1'],
        });
    });

    it('loops an endless plan', () => {
        const queue = new RepeatQueue(
            { from: '113:1', to: '113:1', verseTimes: 1, rangeTimes: Infinity, pauseMs: 0 },
            [113],
        );

        expect(drain(queue, 3).played).toEqual(['0/113:1', '1/113:1', '2/113:1']);
    });

    it('ends an endless plan that plays nothing', () => {
        const queue = new RepeatQueue(
            { from: '113:9', to: '113:9', verseTimes: 1, rangeTimes: Infinity, pauseMs: 0 },
            [113],
        );

        expect(drain(queue, 3)).toEqual({ asked: [113], played: [] });
    });
});
