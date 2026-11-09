/**
 * The Mushaf app's repeat plans (`@qud-technologies/mushaf-core/app` `repeatSteps`) over this
 * reciter's chapters: a `PlaySource` whose verses are each verse's canonical take (`canonical.ts`),
 * so a repeat never replays the reciter's re-dos. Pure: `repeat.svelte.ts` owns the audio.
 */
import { type PlaySource, type Span, type SurahTiming, surahTiming, type VerseSpan } from '@qud-technologies/mushaf-core/app';

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

/** The surahs a plan from `from` to `to` (`s:a`) crosses. */
export function planSurahs(from: string, to: string): number[] {
    const first = Number(from.split(':')[0]);
    const last = Number(to.split(':')[0]);
    return Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => first + i);
}
