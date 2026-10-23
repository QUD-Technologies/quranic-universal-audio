/**
 * Chapter-level word assembly for the full-chapter player.
 *
 * `assembleOccasion()` 0-anchors each occasion's words against its own start
 * (`time_start_ms`). The dashboard plays the WHOLE chapter file, so the animation
 * needs chapter-absolute times — recovered by adding each occasion's
 * `time_start_ms` back onto its words.
 *
 * Pure transform: takes already-assembled occasions (a verse ref may recur —
 * loopbacks / re-dos) and emits flat `AnimUnit[]` (one reading-order cell per
 * word, every occurrence kept on `intervals`) + per-ayah boundaries. No
 * data-fetch / tab imports — the caller owns the shard fetch.
 *
 * Time-base caveat: correct for `by_surah` reciters (one chapter file shared by
 * every verse). `by_ayah` reciters have per-verse audio files whose concatenation
 * offset isn't known here, so the dashboard guards them out before calling this.
 */

import type { SpecialKind, TsVerseData } from '../types/ts-client';
import type { AnimUnit, AyahBoundary, ChapterRecitation, TimeSpan } from './types';

/** One assembled occasion (a contiguous recitation of a verse). A verse may
 *  recur across the chapter, so `verseRef` is not unique across the list. */
export interface AssembledVerse {
    verseRef: string;
    data: TsVerseData;
    /** Target ayahKey when this occasion's LAST word continues into the next
     *  verse without a stop (cross-verse waṣl); from `ChapterOccasion.waslOutTo`. */
    waslOutTo?: string | null;
    /** Set when the occasion is a recited opener (`verseRef` is its `0:x` ref);
     *  from `ChapterOccasion.special`. */
    special?: SpecialKind | null;
}

function parseLocation(location: string): { surah: number; ayah: number; word: number } {
    const [s, a, w] = location.split(':');
    return {
        surah: parseInt(s ?? '0', 10),
        ayah: parseInt(a ?? '0', 10),
        word: parseInt(w ?? '0', 10),
    };
}

/**
 * Build chapter-absolute recitation data from assembled occasions.
 *
 * Each occasion's words are 0-anchored against its own start; `time_start_ms`
 * recovers the chapter-absolute time. Words are then collapsed by location
 * ("surah:ayah:word") into one reading-order `AnimUnit` per word, with EVERY
 * occurrence kept on `intervals` (ascending) — so the recitation locator covers
 * the whole audio timeline and the highlight travels back into a re-recited verse
 * instead of duplicating the text. `intervals[0]` is the earliest occurrence: the
 * first-occurrence span the cell geometry + letter timelines anchor on.
 *
 * @param reciter   slug
 * @param chapter   1-based surah number
 * @param occasions assembled occasions (any order; sorted internally by abs start)
 */
export function buildChapterRecitation(
    reciter: string,
    chapter: number,
    occasions: AssembledVerse[],
): ChapterRecitation {
    // Flatten every occasion's words to chapter-absolute units — one per recited
    // occurrence (repeats included).
    const flat: AnimUnit[] = [];
    for (const { data, waslOutTo, special } of occasions) {
        const offsetSec = data.time_start_ms / 1000;
        for (let wi = 0; wi < data.words.length; wi++) {
            const w = data.words[wi]!;
            const { surah, ayah, word } = parseLocation(w.location);
            const start = w.start + offsetSec;
            const end = w.end + offsetSec;
            // The waṣl flag rides the LAST word of the bridging take only.
            const isLastWord = wi === data.words.length - 1;
            const span: TimeSpan =
                isLastWord && waslOutTo
                    ? { start, end, waslTo: waslOutTo }
                    : { start, end };
            flat.push({
                location: w.location,
                ayahKey: `${surah}:${ayah}`,
                surah,
                ayah,
                word,
                text: w.display_text || w.text,
                start,
                end,
                intervals: [span],
                ...(special ? { special } : {}),
                letters: w.letters.map((lt) => ({
                    char: lt.char,
                    start: lt.start === null ? null : lt.start + offsetSec,
                    end: lt.end === null ? null : lt.end + offsetSec,
                    tokenId: lt.tokenId,
                    paintCharacterIds: lt.paintCharacterIds,
                    silent: lt.silent,
                })),
            });
        }
    }

    // Collapse by INDEX (location), not text — a reciter who repeats / goes back
    // re-emits the same word index with a new time span. Each word renders once
    // (reading order); every occurrence's span + its OWN letter timings are kept
    // (parallel `intervals` / `occurrenceLetters`) so the highlight can travel
    // back and re-light the word at the CURRENT take's pace, not a stretch of
    // take 1.
    interface Occurrence {
        start: number;
        end: number;
        letters: AnimUnit['letters'];
        /** The bridging take's waṣl target ayahKey (only the take whose last word
         *  continues into the next verse carries it). */
        waslTo?: string;
    }
    const byLoc = new Map<string, AnimUnit>();
    const occByLoc = new Map<string, Occurrence[]>();
    for (const u of flat) {
        const occ: Occurrence = { start: u.start, end: u.end, letters: u.letters, waslTo: u.intervals[0]?.waslTo };
        const existing = byLoc.get(u.location);
        if (existing) {
            occByLoc.get(u.location)!.push(occ);
            if (u.start < existing.start) existing.start = u.start;
            if (u.end > existing.end) existing.end = u.end;
        } else {
            byLoc.set(u.location, u);
            occByLoc.set(u.location, [occ]);
        }
    }

    // Openers sit on chapter-0 refs (`0:1` Isti'adha, `0:2` Basmala), so the
    // surah-first order puts them ahead of ayah 1 in recitation order.
    const units = [...byLoc.values()].sort(
        (a, b) => a.surah - b.surah || a.ayah - b.ayah || a.word - b.word,
    );
    // Materialise the ascending per-occurrence spans + letters. `intervals[0]` /
    // `letters` stay the earliest occurrence (the geometry + structure anchor).
    for (const u of units) {
        const occ = occByLoc.get(u.location)!.sort((a, b) => a.start - b.start);
        // Keep the per-occurrence waṣl flag so only the bridging take of a
        // repeated word carries it (`filmstrip-model`/`wasl-chains` read it).
        u.intervals = occ.map((o) => ({ start: o.start, end: o.end, waslTo: o.waslTo }));
        u.occurrenceLetters = occ.map((o) => o.letters);
        u.letters = u.occurrenceLetters[0] ?? u.letters;
    }

    // Per-ayah boundaries, in reading order.
    const ayahs: AyahBoundary[] = [];
    const byKey = new Map<string, AyahBoundary>();
    for (const u of units) {
        const startMs = Math.round(u.start * 1000);
        const endMs = Math.round(u.end * 1000);
        const existing = byKey.get(u.ayahKey);
        if (!existing) {
            const b: AyahBoundary = {
                ayahKey: u.ayahKey,
                surah: u.surah,
                ayah: u.ayah,
                startMs,
                endMs,
            };
            byKey.set(u.ayahKey, b);
            ayahs.push(b);
        } else {
            if (startMs < existing.startMs) existing.startMs = startMs;
            if (endMs > existing.endMs) existing.endMs = endMs;
        }
    }
    ayahs.sort((a, b) => a.startMs - b.startMs);

    const contentEndMs = units.length
        ? Math.round(Math.max(...units.map((u) => u.end)) * 1000)
        : 0;

    return { reciter, chapter, units, ayahs, contentEndMs };
}

/** Inclusive [start, end) unit index range for each ayah, keyed by ayahKey.
 *  Assumes `units` is in playback order (as produced above) and each ayah's
 *  units are contiguous — true for sequential recitation. */
export function ayahUnitRanges(units: AnimUnit[]): Map<string, [number, number]> {
    const ranges = new Map<string, [number, number]>();
    for (let i = 0; i < units.length; i++) {
        const key = units[i]!.ayahKey;
        const r = ranges.get(key);
        if (!r) ranges.set(key, [i, i + 1]);
        else r[1] = i + 1;
    }
    return ranges;
}
