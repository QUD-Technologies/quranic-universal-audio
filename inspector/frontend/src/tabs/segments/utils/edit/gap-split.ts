/**
 * Open a committed cut's measured silence as a gap between its two pieces.
 *
 * A Low Confidence Waqf cut carries the pause the cut-timing pass found
 * (`silence_start_ms` / `silence_end_ms` on the item's cut). After the split,
 * the left piece is trimmed to end where the pause starts and the right piece
 * to start where it ends. The split and both trims chain through the pieces'
 * uids, so history shows them as one edit and undo reverts them together.
 */

import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../lib/types/view-models';
import { EDIT_MIN_DURATION_MS } from '../constants';
import { commitTrim } from './trim';

export type Silence = readonly [number, number];

interface TimedCut {
    cursor_ms?: number | null;
    silence_start_ms?: number | null;
    silence_end_ms?: number | null;
}

/** The measured silence around each of `cursors` on `item`, or null where none was measured. */
export function cutSilences(item: SegValAnyItem | null, cursors: readonly number[]): (Silence | null)[] {
    const cuts = (item as { boundary?: { cuts?: TimedCut[] } } | null)?.boundary?.cuts ?? [];
    return cursors.map((at) => {
        const cut = cuts.find((c) => c.cursor_ms === at);
        const a = cut?.silence_start_ms, b = cut?.silence_end_ms;
        return typeof a === 'number' && typeof b === 'number' && a <= at && at <= b && a < b ? [a, b] : null;
    });
}

/** Whether `gap` fits between `left` and `right`, leaving each a playable piece. */
export function gapFits(left: Segment, right: Segment, gap: Silence): boolean {
    return gap[0] - left.time_start >= EDIT_MIN_DURATION_MS && right.time_end - gap[1] >= EDIT_MIN_DURATION_MS;
}

/** Trim `pieces` around each committed cut to its silence (`silences[i]` belongs to cut `i`). */
export function openCutGaps(
    pieces: readonly Segment[],
    silences: readonly (Silence | null)[],
    contextCategory: string | null,
): void {
    silences.forEach((gap, i) => {
        const left = pieces[i], right = pieces[i + 1];
        if (!gap || !left || !right || !gapFits(left, right, gap)) return;
        if (gap[0] < left.time_end) commitTrim(left, { time_end: gap[0] }, contextCategory);
        if (gap[1] > right.time_start) commitTrim(right, { time_start: gap[1] }, contextCategory);
    });
}
