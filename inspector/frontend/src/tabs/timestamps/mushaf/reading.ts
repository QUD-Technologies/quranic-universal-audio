/**
 * The read-along for `<qud-mushaf>.setReading`, as the Mushaf app draws it: the data SDK's
 * `ReadingTrack` over this chapter's timings, read by the core's `readingAt` (the word, its
 * letters' states, the cross-word merge sounding, a pause at a stop sign). The track's facts are
 * the SDK's: the last recited letter stays lit between letters, a silent letter follows the
 * pronounced one beside it, a tanween sounds as its own part where it merges.
 *
 * Every take of every word is a visit, so a passage read again lights again. A take's letters are
 * the word's written letters in order (letter `k`, 0-based, is `s:a:w:k+1`); a letter the producer
 * marks silent, or leaves untimed, is not a recited letter. The basmala the reciter opens a surah
 * with (`0:2:w`) reads like 1:1, on the basmala line. Where the recitation stops on a stop sign
 * (`tajweed.stopsAt`) is the track's own `stops()`.
 */
import type { ReadingState } from '@qud-technologies/mushaf-core';
import { basmalaAt, type HighlightMode, readingAt } from '@qud-technologies/mushaf-core/app';
import { type QuranPack, ReadingTrack, type TimingInterval } from '@qud-technologies/quran-data';

import type { AnimLetter, AnimUnit } from '../../../lib/recitation-animation/types';

/** A word anchor in the mushaf: opener readings (`0:…`) are recited but never printed. */
const WORD_ANCHOR = /^[1-9]\d*:[1-9]\d*:[1-9]\d*$/;
/** The opener reading of the basmala: `0:2:w`, word w of 1:1. */
const BASMALA_OPENER = /^0:2:(\d+)$/;

export interface ChapterReading {
    surah: number;
    /** The printed words' read-along. */
    words: ReadingTrack;
    /** The opening basmala's (as 1:1), or null when the recording has none. */
    basmala: ReadingTrack | null;
    /** Where the basmala opener ends (ms); the surah's own words start after it. */
    basmalaEndMs: number;
}

const ms = (s: number): number => Math.round(s * 1000);

/** The letters of a take, when the take has its own (take 0 falls back to the unit's). */
function takeLetters(unit: AnimUnit, take: number): AnimLetter[] | null {
    return unit.occurrenceLetters?.[take] ?? (take === 0 ? unit.letters : null);
}

/** Word visits and recited letters of `units`, under `anchorOf` (null: left out). */
function visits(units: readonly AnimUnit[], anchorOf: (u: AnimUnit) => string | null): { words: TimingInterval[]; letters: TimingInterval[] } {
    const words: TimingInterval[] = [];
    const letters: TimingInterval[] = [];
    for (const unit of units) {
        const anchor = anchorOf(unit);
        if (!anchor) continue;
        unit.intervals.forEach((iv, take) => {
            words.push({ anchor, startMs: ms(iv.start), endMs: ms(iv.end) });
            takeLetters(unit, take)?.forEach((l, k) => {
                if (l.silent || l.start === null || l.end === null || l.end <= l.start) return;
                letters.push({ anchor: `${anchor}:${k + 1}`, startMs: ms(l.start), endMs: ms(l.end) });
            });
        });
    }
    return { words, letters };
}

/** The chapter's read-along tracks. */
export function chapterReading(pack: QuranPack, surah: number, units: readonly AnimUnit[]): ChapterReading {
    const printed = visits(units, (u) => (WORD_ANCHOR.test(u.location) ? u.location : null));
    const opener = visits(units, (u) => {
        const m = BASMALA_OPENER.exec(u.location);
        return m ? `1:1:${m[1]}` : null;
    });
    return {
        surah,
        words: new ReadingTrack(pack, printed.words, printed.letters),
        basmala: opener.words.length ? new ReadingTrack(pack, opener.words, opener.letters) : null,
        basmalaEndMs: opener.words.reduce((end, w) => Math.max(end, w.endMs), 0),
    };
}

/** What the page draws at `t` ms: the basmala line while the opener sounds, else the surah's words. */
export function readingStateAt(reading: ChapterReading, t: number, mode: HighlightMode): ReadingState | null {
    if (reading.basmala && t < reading.basmalaEndMs && reading.basmala.wordAt(t)) {
        return basmalaAt(reading.surah, reading.basmala, t, mode);
    }
    return readingAt(reading.words, t, mode);
}

