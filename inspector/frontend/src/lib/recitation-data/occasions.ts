/**
 * Occasion splitting over shard readings and their original parts.
 *
 * Profile-independent: this only reads `reading.parts` and uses the reading as
 * an identity key, so a word-profile reading splits into occasions exactly like
 * a native one. Surfaces that need cells narrow with `isWordShard` first.
 *
 * A recited opener (Isti'adha/Basmala) is its own reading on its own `0:x`
 * part ref, so it never merges with a verse occasion; `special` names it and
 * `chapter` comes from the shard, never from a ref (an opener's ref is `0:x`).
 */

import type {
    SpecialKind,
    TsShardPart,
    TsShardReading,
    TsWordShardReading,
} from '../types/ts-client';

/** Either profile's reading — occasion splitting does not care which. */
export type AnyShardReading = TsShardReading | TsWordShardReading;

export interface OccasionReading {
    reading: AnyShardReading;
    parts: TsShardPart[];
}

export interface ChapterOccasion {
    /** The shard's chapter (`_meta.chapter`) — the surah the audio belongs to. */
    chapter: number;
    ref: string;
    /** Set when this occasion is a recited opener rather than a verse. */
    special: SpecialKind | null;
    readings: OccasionReading[];
    parts: TsShardPart[];
    firstStartMs: number;
    waslOutTo: string | null;
}

interface IndexedPart {
    reading: AnyShardReading;
    part: TsShardPart;
    readingIndex: number;
}

function orderedParts(readings: AnyShardReading[]): IndexedPart[] {
    return readings.flatMap((reading, readingIndex) =>
        reading.parts.map((part) => ({ reading, part, readingIndex })),
    ).sort((a, b) => a.part.t[0] - b.part.t[0] || a.readingIndex - b.readingIndex);
}

function append(occasion: ChapterOccasion, entry: IndexedPart): void {
    occasion.parts.push(entry.part);
    const current = occasion.readings.at(-1);
    if (current?.reading === entry.reading) current.parts.push(entry.part);
    else occasion.readings.push({ reading: entry.reading, parts: [entry.part] });
}

export function chapterOccasions(
    readings: AnyShardReading[],
    chapter: number,
): ChapterOccasion[] {
    const occasions: ChapterOccasion[] = [];
    let previous: IndexedPart | null = null;
    for (const entry of orderedParts(readings)) {
        const current = occasions.at(-1);
        if (current?.ref === entry.part.ref) {
            append(current, entry);
        } else {
            if (current && previous?.reading === entry.reading) {
                current.waslOutTo = entry.part.ref;
            }
            const next: ChapterOccasion = {
                chapter,
                ref: entry.part.ref,
                special: entry.reading.special ?? null,
                readings: [],
                parts: [],
                firstStartMs: entry.part.t[0],
                waslOutTo: null,
            };
            append(next, entry);
            occasions.push(next);
        }
        previous = entry;
    }
    return occasions;
}
