/**
 * Word-profile shard assembly — the non-Hafs counterpart to `native-shards.ts`.
 *
 * A word-profile reading has no cells, sounds or animation tokens: its timings
 * come from a Hafs-proxy MFA run and are projected onto the delivery edition's
 * own words. So every letter-, phoneme- and tajweed-level field of `TsVerseData`
 * is empty here BY CONSTRUCTION, not as a degraded fallback — consumers gate on
 * `isWordShard` (or, in shared `lib/` components that must not know the tab's
 * edition, on the absence of letters) and hide those surfaces entirely.
 *
 * Word text comes from the shard row, never from `qpc_hafs` / `digital_khatt`:
 * those two files are the Hafs script, and rendering Warsh coordinates through
 * them would produce plausible-looking words with the wrong spelling.
 */

import type {
    TsVerseData,
    TsWord,
    TsWordShardReading,
    WordProfileReading,
    WordProfileWord,
} from '../types/ts-client';
import { splitWaqf } from '../utils/waqf';
import type { AnyShardReading, ChapterOccasion } from './occasions';

export interface WordAssembleOptions {
    reciter: string;
    members: ChapterOccasion[];
    verseRef: string;
    audioCategory: 'by_surah' | 'by_ayah';
    audioUrl: string;
}

/** True when this occasion's readings carry proxy-timed words rather than cells. */
export function isWordReading(reading: AnyShardReading): reading is TsWordShardReading {
    return !('wire' in reading);
}

function asWord(reading: AnyShardReading): TsWordShardReading {
    if (!isWordReading(reading)) {
        throw new Error(`reading ${reading.id}: native reading routed to the word assembler`);
    }
    return reading;
}

/** Readings in audio order, de-duplicated across the occasion's parts. */
const uniqueReadings = (members: ChapterOccasion[]): TsWordShardReading[] => [
    ...new Set(members.flatMap((member) => member.readings.map((one) => asWord(one.reading)))),
];

/** Which word indices of each reading this occasion actually selected. */
function selectedWords(members: ChapterOccasion[]): Map<TsWordShardReading, Set<number>> {
    const selected = new Map<TsWordShardReading, Set<number>>();
    for (const member of members) {
        for (const entry of member.readings) {
            const reading = asWord(entry.reading);
            const ids = selected.get(reading) ?? new Set<number>();
            entry.parts.flatMap((part) => part.word_ids).forEach((id) => ids.add(id));
            selected.set(reading, ids);
        }
    }
    return selected;
}

function buildWordRows(
    readings: TsWordShardReading[],
    selected: Map<TsWordShardReading, Set<number>>,
    offset: number,
): TsWord[] {
    const words: TsWord[] = [];
    for (const reading of readings) {
        const ids = selected.get(reading);
        reading.words.forEach((row, index) => {
            if (!ids?.has(index)) return;
            words.push({
                location: row.ref,
                text: row.text,
                display_text: row.text,
                start: row.start_ms / 1000 - offset,
                end: row.end_ms / 1000 - offset,
                phoneme_indices: [],
                letters: [],
            });
        });
    }
    return words;
}

/** Shortest gap that counts as a pause the reciter actually took, in seconds. */
const RECORDED_PAUSE_MIN_S = 0.001;

/** True when the gap has real duration — a timestamped pause, not a join. */
export const isRecordedPause = (startS: number, endS: number): boolean =>
    endS - startS > RECORDED_PAUSE_MIN_S;

/**
 * Lift a trailing waqf mark off the word and hand it to the gap, the way the
 * native profile's cell view emits a separate `stop_sign` column per boundary
 * — but only where the reciter actually paused on it. A mark the reciter read
 * through stays in the word, and so does one at a verse end, whose tile
 * renders the verse marker instead; the word cell lights with it, same as the
 * teleprompter, which never splits at a verse end either.
 */
function liftStopSign(
    text: string,
    verseEnd: number | null,
    paused: boolean,
): { text: string; stopSign: string | null } {
    if (verseEnd != null || !paused) return { text, stopSign: null };
    const { clean, mark } = splitWaqf(text);
    return { text: clean, stopSign: mark };
}

/**
 * The render view: words paired with the gap that follows each, carrying the
 * reading id so a report target resolves the same way it does for native cells.
 *
 * Boundary `i + 1` is the gap after word `i` (`boundariesOf` in
 * `compact-shards.ts` emits ids `0..N`, id 0 being the lead-in), so the last
 * selected word still gets its trailing gap — that is where a verse-end marker
 * lives.
 */
function buildReadingViews(
    readings: TsWordShardReading[],
    selected: Map<TsWordShardReading, Set<number>>,
    offset: number,
): WordProfileReading[] {
    // Counts across readings, not within one, because `buildWordRows` walks the
    // same readings in the same order with the same filter — so the nth word it
    // emits is the nth row of `data.words`, whichever reading it came from.
    let displayIndex = 0;
    return readings.map((reading) => {
        const ids = selected.get(reading);
        const gaps = new Map(reading.timing.boundaries.map((row) => [row.boundary_id, row]));
        const words: WordProfileWord[] = [];
        reading.words.forEach((row, index) => {
            if (!ids?.has(index)) return;
            const gap = gaps.get(index + 1);
            const state = reading.states[index];
            const verseEnd = reading.verseEnds[index] ?? null;
            // A word with no gap keeps its mark: there is no bridge to move it to.
            const lifted = gap && state
                ? liftStopSign(
                    row.text, verseEnd,
                    isRecordedPause(gap.start_ms / 1000, gap.end_ms / 1000),
                )
                : null;
            words.push({
                id: index,
                displayIndex: displayIndex++,
                location: row.ref,
                text: lifted?.text ?? row.text,
                start: row.start_ms / 1000 - offset,
                end: row.end_ms / 1000 - offset,
                boundary: gap && state
                    ? {
                        id: gap.boundary_id,
                        start: gap.start_ms / 1000 - offset,
                        end: gap.end_ms / 1000 - offset,
                        state,
                        verseEnd,
                        stopSign: lifted?.stopSign ?? null,
                    }
                    : null,
            });
        });
        return { id: reading.id, words };
    }).filter((reading) => reading.words.length > 0);
}

export function assembleWord(options: WordAssembleOptions): TsVerseData {
    const readings = uniqueReadings(options.members);
    const parts = options.members.flatMap((member) => member.parts);
    const startMs = Math.min(...parts.map((part) => part.t[0]));
    const endMs = Math.max(...parts.map((part) => part.t[1]));
    const offset = options.audioCategory === 'by_surah' ? startMs / 1000 : 0;
    const selected = selectedWords(options.members);
    return {
        reciter: options.reciter,
        chapter: Number(options.verseRef.split(':')[0]),
        verse_ref: options.verseRef,
        audio_url: options.audioUrl,
        audio_category: options.audioCategory === 'by_surah' ? 'by_surah_audio' : 'by_ayah_audio',
        time_start_ms: options.audioCategory === 'by_surah' ? startMs : 0,
        time_end_ms: endMs,
        intervals: [],
        words: buildWordRows(readings, selected, offset),
        native: [],
        wordReadings: buildReadingViews(readings, selected, offset),
    };
}
