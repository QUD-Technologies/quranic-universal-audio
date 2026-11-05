/**
 * Playback time → mushaf position, for the loaded chapter.
 *
 * Built once per chapter over the recitation units (every take of every word,
 * so loop-backs of one or several verses move the highlight back with the
 * reciter). In silence the position holds on the word recited last in TIME,
 * not in reading order, which is what a loop-back needs. Positions are word
 * anchors (`s:a:w`), the mushaf package's coordinates.
 */
import {
    buildSortedIntervals,
    findActiveAt,
    nextIntervalAfter,
    type SortedInterval,
} from '../../../lib/recitation-animation/recitation-active';
import type { AnimUnit } from '../../../lib/recitation-animation/types';

/** A word anchor in the mushaf: opener readings (`0:…`) are recited but never printed. */
const WORD_ANCHOR = /^[1-9]\d*:[1-9]\d*:[1-9]\d*$/;

export interface ChapterIndex {
    chapter: number;
    units: AnimUnit[];
    sorted: SortedInterval[];
    /** Mushaf anchor per unit ('' for a reading the mushaf does not print). */
    anchorOfUnit: string[];
    unitOfLoc: Map<string, number>;
    /** First-occurrence start (ms) of each verse, "s:a" → ms. */
    verseStartMs: Map<string, number>;
    /** The chapter's first recited word in reading order ('' when none). */
    firstAnchor: string;
}

export interface MushafPosition {
    /** Word being recited now, '' in silence. */
    active: string;
    verseKey: string;
    /** Last word reached; '' before the first one (everything from `firstAnchor` is upcoming). */
    progress: string;
    /** Word the page should show: the active or last one, else the next. */
    anchor: string;
}

function readingKey(anchor: string): number[] {
    return anchor.split(':').map(Number);
}

/** Reading-order comparison of two word anchors. */
export function compareAnchors(a: string, b: string): number {
    const [x, y] = [readingKey(a), readingKey(b)];
    return x[0]! - y[0]! || x[1]! - y[1]! || x[2]! - y[2]!;
}

export function indexChapter(chapter: number, units: AnimUnit[]): ChapterIndex {
    const anchorOfUnit = units.map((u) => (WORD_ANCHOR.test(u.location) ? u.location : ''));
    const unitOfLoc = new Map(units.map((u, i) => [u.location, i]));
    const verseStartMs = new Map<string, number>();
    for (const u of units) {
        const ms = Math.round((u.intervals[0]?.start ?? u.start) * 1000);
        const cur = verseStartMs.get(u.ayahKey);
        if (cur === undefined || ms < cur) verseStartMs.set(u.ayahKey, ms);
    }
    const printed = anchorOfUnit.filter(Boolean).sort(compareAnchors);
    return {
        chapter,
        units,
        sorted: buildSortedIntervals(units),
        anchorOfUnit,
        unitOfLoc,
        verseStartMs,
        firstAnchor: printed[0] ?? '',
    };
}

/** Interval that started most recently at or before `t`, or null before the first. */
function lastStarted(sorted: SortedInterval[], t: number): SortedInterval | null {
    if (!sorted.length || t < sorted[0]!.start) return null;
    let lo = 0;
    let hi = sorted.length - 1;
    while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (sorted[mid]!.start <= t) lo = mid;
        else hi = mid - 1;
    }
    return sorted[lo]!;
}

/** Position at `t` seconds. `hint` = previous active unit index (fast path). */
export function positionAt(ix: ChapterIndex, t: number, hint: number): { pos: MushafPosition; unit: number } {
    const hit = findActiveAt(ix.units, ix.sorted, t, hint);
    if (hit) {
        const anchor = ix.anchorOfUnit[hit.unitIdx] ?? '';
        const verseKey = ix.units[hit.unitIdx]!.ayahKey;
        return { pos: { active: anchor, verseKey, progress: anchor, anchor }, unit: hit.unitIdx };
    }
    const prev = lastStarted(ix.sorted, t);
    if (prev) {
        const anchor = ix.anchorOfUnit[prev.unitIdx] ?? '';
        const verseKey = ix.units[prev.unitIdx]!.ayahKey;
        return { pos: { active: '', verseKey, progress: anchor, anchor }, unit: -1 };
    }
    const next = nextIntervalAfter(ix.sorted, t) ?? ix.sorted[0] ?? null;
    const anchor = next ? (ix.anchorOfUnit[next.unitIdx] ?? '') : '';
    const verseKey = next ? ix.units[next.unitIdx]!.ayahKey : '';
    return { pos: { active: '', verseKey, progress: '', anchor }, unit: -1 };
}
