/**
 * The Mushaf app's repeat plans (`@qud-technologies/mushaf-core/app` `repeatSteps`) over this
 * reciter's chapters: a `PlaySource` whose verses are each verse's canonical take (`canonical.ts`),
 * so a repeat never replays the reciter's re-dos. Pure: `repeat.svelte.ts` owns the audio.
 *
 * `RepeatQueue` hands the plan's steps out one surah at a time, so a chapter's timing is fetched
 * when the plan reaches it and never for the whole range up front.
 */
import {
    type PlaySource,
    type RepeatPlan,
    repeatSteps,
    type Span,
    type SurahStep,
    type SurahTiming,
    surahTiming,
    type VerseSpan,
} from '@qud-technologies/mushaf-core/app';

import type { AnimUnit } from '../../../lib/recitation-animation/types';
import { canonicalTakes } from './canonical';

/** A word anchor in the mushaf: opener readings (`0:…`) are recited but never printed. */
const WORD_ANCHOR = /^[1-9]\d*:[1-9]\d*:[1-9]\d*$/;

/** A chapter's timing as the core's player reads it: word visits, and each verse's canonical take. */
export function chapterTiming(surah: number, units: readonly AnimUnit[]): SurahTiming {
    const words: Span[] = units
        .filter((u) => WORD_ANCHOR.test(u.location))
        .flatMap((u) => u.intervals.map((iv) => ({ anchor: u.location, start: Math.round(iv.start * 1000), end: Math.round(iv.end * 1000) })))
        .sort((a, b) => a.start - b.start);
    const verses: VerseSpan[] = [...canonicalTakes([...units])]
        .filter(([key]) => key.startsWith(`${surah}:`))
        .map(([key, take]) => ({ ayah: Number(key.split(':')[1]), start: take.startMs, end: take.endMs }))
        .sort((a, b) => a.ayah - b.ayah);
    return { ...surahTiming(surah, words, []), verses };
}

/** A play source over the chapters loaded for a plan (`surahs`: those the reciter has). */
export function playSource(surahs: readonly number[], chapters: ReadonlyMap<number, SurahTiming>): PlaySource {
    return {
        surahs: surahs.filter((s) => chapters.has(s)),
        hasOpeners: false,
        surah: (s) => chapters.get(s) ?? surahTiming(s, [], []),
    };
}

/** What a `RepeatQueue` needs before it can go on: this surah's timing. */
export interface NeedsSurah {
    load: number;
}

/**
 * A repeat plan's steps in play order (rounds, then surahs, then the core's verse repeats), read
 * one surah at a time. `next()` answers `{ load }` when it reaches a surah whose timing has not
 * been given yet; `provide()` it and ask again.
 */
export class RepeatQueue {
    private readonly timings = new Map<number, SurahTiming>();
    private steps: Iterator<SurahStep> | null = null;
    private round = 0;
    private index = 0;
    /** Whether the round in progress has played anything (a plan that plays nothing must end). */
    private roundPlayed = false;

    /** `surahs`: the plan's surahs this reciter has, in order (`planSurahs` ∩ the reciter's). */
    constructor(
        private readonly plan: RepeatPlan,
        private readonly surahs: readonly number[],
    ) {}

    provide(surah: number, timing: SurahTiming): void {
        this.timings.set(surah, timing);
    }

    next(): SurahStep | NeedsSurah | null {
        for (;;) {
            if (this.steps) {
                const step = this.steps.next();
                if (!step.done) {
                    this.roundPlayed = true;
                    const at = step.value.repeatAt;
                    return at ? { ...step.value, repeatAt: { ...at, round: this.round } } : step.value;
                }
                this.steps = null;
                this.index++;
            }
            if (this.index >= this.surahs.length) {
                if (!this.roundPlayed) return null;
                this.round++;
                this.index = 0;
                this.roundPlayed = false;
            }
            if (this.round >= this.plan.rangeTimes) return null;
            const surah = this.surahs[this.index]!;
            const timing = this.timings.get(surah);
            if (!timing) return { load: surah };
            const source = playSource([surah], new Map([[surah, timing]]));
            this.steps = repeatSteps(source, { ...this.plan, rangeTimes: 1 })[Symbol.iterator]();
        }
    }
}

/** The surahs a plan from `from` to `to` (`s:a`) crosses. */
export function planSurahs(from: string, to: string): number[] {
    const first = Number(from.split(':')[0]);
    const last = Number(to.split(':')[0]);
    return Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => first + i);
}
