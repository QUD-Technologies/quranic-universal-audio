/**
 * Filmstrip cell model — recitation-correct geometry + per-verse word fractions.
 *
 * The ayah filmstrip is a verse ruler driven by the actual recitation. This
 * derives, from the chapter's recited `units`, one `VerseCell` per ayah (in
 * reading order) carrying:
 *   - `canonDurSec` — the verse's recited length (sum of each word's
 *     FIRST-occurrence duration), used to SIZE the cell. Never inflated by a
 *     loopback's later occurrence (unlike `AyahBoundary.endMs = max(end)`).
 *   - `words` — a fraction table: each word's `[frac0, frac1)` position within
 *     the verse, so the active cell's progress bar fills to the recited WORD's
 *     position (and loops back proportionally on a within-verse repeat) instead
 *     of growing with elapsed clock time.
 *
 * Plus reverse lookups (`indexByAyahKey`, `cellOfUnit`) so the playback loop can
 * go active-unit → cell in O(1). Built once per chapter. Pure; no fetch/tab
 * imports. Times are seconds (matching `AnimUnit`).
 */

import type { ChapterCoverage } from '../recitation-data/coverage';
import type { SpecialKind } from '../types/ts-client';
import { ayahUnitRanges } from './chapter-words';
import type { AnimUnit } from './types';

/** Weighting for a word's share of its verse's progress bar.
 *  `duration` (default) = canonical word length; `equal` = one slot per word. */
export type WordWeighting = 'duration' | 'equal';

/** Per-cell coverage state: complete, present-but-missing-words, or fully
 *  unrecited (a placeholder cell — no units, non-clickable). */
export type CellMissing = 'none' | 'words' | 'full';

/** The active/selected verse the filmstrip reports up to its parent (drives the
 *  contextual "missing words" pill). */
export interface ActiveCellInfo {
    ayah: number;
    missing: CellMissing;
    /** Set when the active cell is a recited opener (its `ayah` is not a verse). */
    special: SpecialKind | null;
}

/** A word's position within its verse, as a half-open fraction range. */
export interface WordFrac {
    /** Global index into `units`. */
    unitIdx: number;
    /** Word index within the ayah (1-based, from the shard). */
    word: number;
    frac0: number;
    frac1: number;
}

/** One verse cell — geometry source + word-fraction table. */
export interface VerseCell {
    ayahKey: string;
    surah: number;
    ayah: number;
    /** Canonical recited duration (seconds) — sum of word first-occurrence
     *  durations. Drives cell WIDTH regardless of `weighting`. */
    canonDurSec: number;
    /** [start, end) global unit-index range for the verse (reading order). */
    unitStart: number;
    unitEnd: number;
    /** Word-fraction table, ordered by word (= reading order within the verse). */
    words: WordFrac[];
    /** Canonical first-occurrence start (seconds) — the click/drag seek target. */
    canonStartSec: number;
    /** Verse's recited END (seconds) that the between-verse gap measures from.
     *  Starts as the latest FIRST-occurrence word end, then refined in `_assemble`
     *  to the FORWARD-FLOWING occurrence end — the take that leads into the next
     *  verse — so a loopback's later re-recitation isn't counted as silence. Real
     *  wall-clock (not `canonStartSec + canonDurSec`), so within-verse gaps stay
     *  inside the cell. */
    canonEndSec: number;
    /** Audible between-verse silence (seconds) from this verse's forward-flowing
     *  end to the next PRESENT verse's start; 0 for the last cell, a missing next,
     *  or contiguous verses. A loopback detour is excluded (see `canonEndSec`).
     *  Drives the silence-scaled inter-cell gap + continuous scroll-through;
     *  within-verse silences live inside the cell and never appear here. */
    nextGapSec: number;
    /** Coverage state. `full` cells are placeholders (no units, no geometry):
     *  unrecited verses kept in the strip so the gap is visible, but skipped by
     *  playback + navigation. Default `none` when no coverage is supplied. */
    missing: CellMissing;
    /** True when some take of this verse waṣl-bridges into the NEXT present verse
     *  (cross-verse continuation). Drives the gapless mega-cell + accent rail —
     *  any-take-bridges renders permanently merged. `false` on placeholders + the
     *  last cell. */
    waslNext: boolean;
    /** Set on a recited opener's cell (Isti'adha/Basmala), which sits before
     *  ayah 1, is labelled by name, and is never a coverage verse. Its `ayahKey`
     *  is the opener's `0:x` ref and its `ayah` is NOT a verse number. */
    special: SpecialKind | null;
}

export interface FilmstripModel {
    cells: VerseCell[];
    /** ayahKey → cell index. */
    indexByAyahKey: Map<string, number>;
    /** Global unitIdx → cell index (−1 for none, though every unit maps). */
    cellOfUnit: Int32Array;
}

const EMPTY_MODEL: FilmstripModel = {
    cells: [],
    indexByAyahKey: new Map(),
    cellOfUnit: new Int32Array(0),
};

/** Each word's cell duration = its FIRST occurrence's span (seconds). A repeat
 *  re-emits the word with a new interval, but the first is the reading-order
 *  length; `>= eps` so a zero-length word still gets a slot. */
function canonDur(u: AnimUnit): number {
    const iv = u.intervals[0];
    if (!iv) return 0.001;
    return Math.max(0.001, iv.end - iv.start);
}

/** A placeholder cell for a fully-missing (unrecited) verse — no geometry, no
 *  units. `canonDurSec: 0` makes the strip clamp it to the min cell width. */
function placeholderCell(surah: number, ayah: number): VerseCell {
    return {
        ayahKey: `${surah}:${ayah}`,
        surah,
        ayah,
        canonDurSec: 0,
        unitStart: -1,
        unitEnd: -1,
        words: [],
        canonStartSec: -1,
        canonEndSec: -1,
        nextGapSec: 0,
        missing: 'full',
        waslNext: false,
        special: null,
    };
}

/**
 * Build the filmstrip cell model from the chapter's recited units.
 *
 * @param units    recited `AnimUnit[]` in reading order (from `buildChapterRecitation`)
 * @param weighting word-fraction weighting (`duration` default, `equal` swappable)
 * @param coverage optional chapter coverage. When supplied, present cells are
 *   tagged `none`/`words` and a `full` placeholder cell is inserted for each
 *   unrecited verse so the gap shows in the strip. Omitted → all cells `none`,
 *   no placeholders (back-compat).
 */
export function buildFilmstripModel(
    units: AnimUnit[],
    weighting: WordWeighting,
    coverage?: ChapterCoverage,
): FilmstripModel {
    if (!units.length) return EMPTY_MODEL;

    // Present cells (one per recited verse), with their global unit ranges.
    const present: VerseCell[] = [];
    // `ayahUnitRanges` preserves reading order and assumes each ayah's units are
    // contiguous (true for the deduped reading-order list).
    for (const [ayahKey, [unitStart, unitEnd]] of ayahUnitRanges(units)) {
        const head = units[unitStart]!;
        let canonDurSec = 0;
        let total = 0;
        let canonEndSec = head.intervals[0]?.end ?? head.end;
        for (let i = unitStart; i < unitEnd; i++) {
            const d = canonDur(units[i]!);
            canonDurSec += d;
            total += weighting === 'equal' ? 1 : d;
            const end = units[i]!.intervals[0]?.end ?? units[i]!.end;
            if (end > canonEndSec) canonEndSec = end;
        }
        if (total <= 0) total = 1; // degenerate guard (all-zero weights)

        const words: WordFrac[] = [];
        let cum = 0;
        for (let i = unitStart; i < unitEnd; i++) {
            const u = units[i]!;
            const wgt = weighting === 'equal' ? 1 : canonDur(u);
            const frac0 = cum / total;
            cum += wgt;
            words.push({ unitIdx: i, word: u.word, frac0, frac1: cum / total });
        }

        present.push({
            ayahKey,
            surah: head.surah,
            ayah: head.ayah,
            canonDurSec,
            unitStart,
            unitEnd,
            words,
            canonStartSec: head.intervals[0]?.start ?? head.start,
            canonEndSec,
            nextGapSec: 0,
            // An opener is no coverage verse: its `ayah` (1 or 2 of `0:x`) would
            // read that verse's status.
            missing: !head.special && coverage?.status.get(head.ayah) === 'words' ? 'words' : 'none',
            waslNext: false, // refined in `_assemble` (needs the next present cell)
            special: head.special ?? null,
        });
    }

    if (!coverage) return _assemble(present, units);

    // Openers lead the strip in recitation order; placeholders for fully-missing
    // verses merge into the verse cells in ayah order. Defensive max covers a
    // present ayah beyond the qpc reference count (shouldn't happen).
    const openers = present.filter((c) => c.special);
    const verses = present.filter((c) => !c.special);
    const surah = verses[0]?.surah;
    if (surah === undefined) return _assemble(present, units);
    const presentByAyah = new Map(verses.map((c) => [c.ayah, c]));
    let maxAyah = coverage.numVerses;
    for (const c of verses) if (c.ayah > maxAyah) maxAyah = c.ayah;

    const merged: VerseCell[] = [...openers];
    for (let a = 1; a <= maxAyah; a++) {
        merged.push(presentByAyah.get(a) ?? placeholderCell(surah, a));
    }
    return _assemble(merged, units);
}

/** Build reverse lookups for an ordered cell list. `cellOfUnit[u]` maps a global
 *  unit index to its cell; placeholder cells (empty unit range) contribute none.
 *  Also refines each present cell's `canonEndSec` to its forward-flowing end and
 *  stamps `nextGapSec` (audible silence to the next present cell, loopback
 *  excluded, skipping unrecited placeholders). */
function _assemble(cells: VerseCell[], units: AnimUnit[]): FilmstripModel {
    const indexByAyahKey = new Map<string, number>();
    const cellOfUnit = new Int32Array(units.length).fill(-1);
    for (let idx = 0; idx < cells.length; idx++) {
        const c = cells[idx]!;
        indexByAyahKey.set(c.ayahKey, idx);
        for (let u = c.unitStart; u < c.unitEnd; u++) cellOfUnit[u] = idx;
    }
    // Between-verse silence: from this verse's FORWARD-FLOWING recited end (the
    // occurrence that leads into the next verse) → the next present cell's start.
    // The forward end is the latest occurrence end that still precedes that start,
    // so a loopback's re-recitation time is excluded — the gap is the audible
    // pause, not the detour. `canonEndSec` (the first-occurrence end) is refined
    // to that forward end here. Placeholders (canonStartSec < 0) are skipped on
    // both sides so an unrecited verse doesn't fabricate a silence.
    for (let i = 0; i < cells.length; i++) {
        const c = cells[i]!;
        if (c.canonStartSec < 0) continue;
        let nextIdx = -1;
        for (let j = i + 1; j < cells.length; j++) {
            if (cells[j]!.canonStartSec < 0) continue;
            nextIdx = j;
            break;
        }
        if (nextIdx < 0) continue; // last present cell — no next, gap stays 0
        const nextStart = cells[nextIdx]!.canonStartSec;
        // Cross-verse waṣl: does any take of this verse bridge into the next
        // present verse? The flag rides the bridging take's last-word occurrence
        // (`waslTo` = target ayahKey). Any-take-bridges → permanently merged.
        const nextKey = cells[nextIdx]!.ayahKey;
        for (let u = c.unitStart; u < c.unitEnd && !c.waslNext; u++) {
            for (const iv of units[u]!.intervals) {
                if (iv.waslTo === nextKey) {
                    c.waslNext = true;
                    break;
                }
            }
        }
        let fwdEnd = -Infinity;
        for (let u = c.unitStart; u < c.unitEnd; u++) {
            for (const iv of units[u]!.intervals) {
                if (iv.end <= nextStart && iv.end > fwdEnd) fwdEnd = iv.end;
            }
        }
        if (fwdEnd > -Infinity) c.canonEndSec = fwdEnd;
        c.nextGapSec = Math.max(0, nextStart - c.canonEndSec);
    }
    return { cells, indexByAyahKey, cellOfUnit };
}
