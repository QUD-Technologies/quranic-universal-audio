/**
 * Chapter coverage gaps for the recitation filmstrip — pure, client-side.
 *
 * Diffs the recited words (the `AnimUnit[]` the filmstrip already holds)
 * against the mushaf reference word counts (the DK verse
 * word counts, `ts-source.loadVerseWordCounts`) to classify every verse of a chapter:
 *   - `full`  — never recited (no units at all),
 *   - `words` — present but missing some reference word index,
 *   - (absent) — complete.
 *
 * Mirrors the backend release gate (`select_complete_verses`): a verse is
 * complete iff `{1..N_ref}` ⊆ recited word indices; an unknown reference count
 * is treated as complete (fail-open). No fetch, no tab imports.
 */

import type { AnimUnit } from '../recitation-animation/types';

export interface ChapterCoverage {
    /** Mushaf verse count for the chapter (max reference ayah). */
    numVerses: number;
    /** ayah → status. ONLY incomplete / fully-missing verses are listed;
     *  a complete verse is absent from the map. */
    status: Map<number, 'words' | 'full'>;
    /** ayah → missing reference word indices (set for `words` verses only). */
    missingWords: Map<number, number[]>;
}

const EMPTY: ChapterCoverage = {
    numVerses: 0,
    status: new Map(),
    missingWords: new Map(),
};

/**
 * Classify each verse `1..numVerses` of `chapter` from the deduped `units`
 * (canonical recited words) against `refWordCounts` (ayah → reference word
 * count for THIS chapter, from the DK verse word counts). Returns an empty coverage
 * when the reference is unavailable (fail-open: mark nothing rather than guess).
 */
export function computeChapterCoverage(
    units: AnimUnit[],
    chapter: number,
    refWordCounts: Map<number, number> | undefined,
): ChapterCoverage {
    if (!refWordCounts || refWordCounts.size === 0) return EMPTY;

    let numVerses = 0;
    for (const ayah of refWordCounts.keys()) {
        if (ayah > numVerses) numVerses = ayah;
    }
    if (numVerses === 0) return EMPTY;

    // Recited word indices per ayah, from the units of this chapter.
    const recited = new Map<number, Set<number>>();
    for (const u of units) {
        if (u.surah !== chapter) continue;
        let set = recited.get(u.ayah);
        if (!set) {
            set = new Set();
            recited.set(u.ayah, set);
        }
        set.add(u.word);
    }

    const status = new Map<number, 'words' | 'full'>();
    const missingWords = new Map<number, number[]>();
    for (let ayah = 1; ayah <= numVerses; ayah++) {
        const present = recited.get(ayah);
        if (!present || present.size === 0) {
            status.set(ayah, 'full');
            continue;
        }
        const refCount = refWordCounts.get(ayah);
        if (!refCount) continue; // unknown reference → treat complete (fail-open)
        const missing: number[] = [];
        for (let w = 1; w <= refCount; w++) {
            if (!present.has(w)) missing.push(w);
        }
        if (missing.length) {
            status.set(ayah, 'words');
            missingWords.set(ayah, missing);
        }
    }
    return { numVerses, status, missingWords };
}
