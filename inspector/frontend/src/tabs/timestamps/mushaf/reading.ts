/**
 * The read-along playhead for `<qud-mushaf>.setReading`: the word being recited, the letters
 * sounding now (`lit`) and the ones already sung in it (`sung`), as the data SDK's letter
 * anchors (`s:a:w:l`). A unit's letters are the word's written letters in order, so letter
 * `k` (0-based) is `s:a:w:k+1`. A repeated word lights with the take being recited.
 *
 * Feel (tuned with the reciter's timings in the highlight lab): light a little early, so the eye
 * meets the voice, and keep a word lit across a gap shorter than `BRIDGE_S`, so the pill does
 * not blink between words read in one breath.
 */
import type { ReadingState } from '@qud-technologies/mushaf-core';

import { findActiveAt } from '../../../lib/recitation-animation/recitation-active';
import type { AnimLetter, AnimUnit } from '../../../lib/recitation-animation/types';
import type { ChapterIndex } from './position';

/** Light this much ahead of the timestamp (s). */
export const LEAD_S = 0.1;
/** A word stays lit across a silence shorter than this (s). */
export const BRIDGE_S = 0.1;

/** The letters of the take of `unit` that starts at `ivStart`. */
function takeLetters(unit: AnimUnit, ivStart: number): AnimLetter[] {
    const take = unit.intervals.findIndex((iv) => iv.start === ivStart);
    return (take >= 0 ? unit.occurrenceLetters?.[take] : undefined) ?? unit.letters;
}

/** Lit and sung letter anchors of `anchor`'s word at `t`; a letter without timing (silent) is sung once a later one starts. */
function letterStates(anchor: string, letters: AnimLetter[], t: number): Pick<ReadingState, 'lit' | 'sung'> {
    const lit: string[] = [];
    const sung: string[] = [];
    let started = false;
    for (let k = letters.length - 1; k >= 0; k--) {
        const { start, end } = letters[k]!;
        const at = `${anchor}:${k + 1}`;
        if (start === null || end === null) {
            if (started) sung.push(at);
            continue;
        }
        if (t >= start) started = true;
        if (t >= start && t < end) lit.push(at);
        else if (t >= end) sung.push(at);
    }
    return { lit, sung };
}

export interface Playhead {
    /** The state to draw, or null in silence. */
    state: ReadingState | null;
    /** Unit index found (fast-path hint for the next frame). */
    unit: number;
}

/** The playhead at `t` seconds (lead applied by the caller); `prev` bridges short silences. */
export function playheadAt(ix: ChapterIndex, t: number, hint: number, prev: { unit: number; end: number } | null): Playhead {
    const hit = findActiveAt(ix.units, ix.sorted, t, hint);
    const unitIdx = hit?.unitIdx ?? (prev && t - prev.end < BRIDGE_S ? prev.unit : -1);
    const anchor = unitIdx >= 0 ? ix.anchorOfUnit[unitIdx] : '';
    if (!anchor) return { state: null, unit: hit?.unitIdx ?? -1 };
    const unit = ix.units[unitIdx]!;
    const letters = hit ? takeLetters(unit, hit.ivStart) : unit.letters;
    // Bridged: the word stays lit with every letter sung.
    const at = hit ? t : Number.POSITIVE_INFINITY;
    return { state: { word: anchor, ...letterStates(anchor, letters, at) }, unit: hit?.unitIdx ?? -1 };
}

/** End (s) of the take of unit `u` containing or last before `t`. */
export function takeEnd(unit: AnimUnit, t: number): number {
    let end = unit.end;
    for (const iv of unit.intervals) if (iv.start <= t) end = iv.end;
    return end;
}
