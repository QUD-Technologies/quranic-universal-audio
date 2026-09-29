/**
 * Playback time → mushaf position, for the loaded chapter.
 *
 * Built once per chapter over the recitation units (every take of every word,
 * so loop-backs of one or several verses move the highlight back with the
 * reciter). In silence the position holds on the word recited last in TIME,
 * not in reading order, which is what a loop-back needs.
 */
import {
    buildSortedIntervals,
    findActiveAt,
    nextIntervalAfter,
    type SortedInterval,
} from '../../../lib/recitation-animation/recitation-active';
import type { AnimUnit } from '../../../lib/recitation-animation/types';
import type { WordIndex } from './layout';

export interface ChapterIndex {
    chapter: number;
    units: AnimUnit[];
    sorted: SortedInterval[];
    /** DK word id per unit (0 when the location isn't in the script). */
    idOfUnit: number[];
    unitOfLoc: Map<string, number>;
    /** First-occurrence start (ms) of each verse, "s:a" → ms. */
    verseStartMs: Map<string, number>;
    /** DK id just before the chapter's first recited word. */
    beforeFirstId: number;
}

export interface MushafPosition {
    /** Word being recited now, 0 in silence. */
    activeId: number;
    verseKey: string;
    /** Last word reached (reading-order cut-off for "upcoming"). */
    progressId: number;
    /** Word the page should show: the active or last one, else the next. */
    anchorId: number;
}

export function indexChapter(chapter: number, units: AnimUnit[], words: WordIndex): ChapterIndex {
    const idOfUnit = units.map((u) => words.idOfLoc.get(u.location) ?? 0);
    const unitOfLoc = new Map(units.map((u, i) => [u.location, i]));
    const verseStartMs = new Map<string, number>();
    for (const u of units) {
        const ms = Math.round((u.intervals[0]?.start ?? u.start) * 1000);
        const cur = verseStartMs.get(u.ayahKey);
        if (cur === undefined || ms < cur) verseStartMs.set(u.ayahKey, ms);
    }
    const ids = idOfUnit.filter((id) => id > 0);
    return {
        chapter,
        units,
        sorted: buildSortedIntervals(units),
        idOfUnit,
        unitOfLoc,
        verseStartMs,
        beforeFirstId: ids.length ? Math.min(...ids) - 1 : 0,
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
        const id = ix.idOfUnit[hit.unitIdx] ?? 0;
        const verseKey = ix.units[hit.unitIdx]!.ayahKey;
        return { pos: { activeId: id, verseKey, progressId: id, anchorId: id }, unit: hit.unitIdx };
    }
    const prev = lastStarted(ix.sorted, t);
    if (prev) {
        const id = ix.idOfUnit[prev.unitIdx] ?? 0;
        const verseKey = ix.units[prev.unitIdx]!.ayahKey;
        return { pos: { activeId: 0, verseKey, progressId: id, anchorId: id }, unit: -1 };
    }
    const next = nextIntervalAfter(ix.sorted, t) ?? ix.sorted[0] ?? null;
    const nextId = next ? (ix.idOfUnit[next.unitIdx] ?? 0) : 0;
    const verseKey = next ? ix.units[next.unitIdx]!.ayahKey : '';
    return {
        pos: { activeId: 0, verseKey, progressId: ix.beforeFirstId, anchorId: nextId },
        unit: -1,
    };
}
