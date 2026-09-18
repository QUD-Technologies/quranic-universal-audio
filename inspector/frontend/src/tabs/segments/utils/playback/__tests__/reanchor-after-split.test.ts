/**
 * After a split replaces one segment with several, the playing pair must name
 * the piece the USER IS ON — and nothing else may move.
 *
 * Two regressions this pins, both seen when labelling a staged cross-verse
 * boundary while on the second piece:
 *
 *  1. Pair pinned to piece 0. `reconcilePlayingAfterMutation` walks the
 *     pre-mutation UID forward and a split preserves it on piece 0, so the
 *     highlight jumped back to the first piece and the cursor froze at its
 *     edge. Worse when a bounded play had just finished: the playhead parks
 *     exactly ON the piece end, which no window contains, so a containment-
 *     only lookup found nothing and left the bad pair in place.
 *  2. Audio restarted. Re-pointing the live `AudioRange` meant
 *     `dispose()` (which pauses) then `start()` (which re-seeks and plays) —
 *     heard as "it paused and jumped back to the first piece". Nothing needs
 *     re-pointing: a split leaves each piece's window exactly where the
 *     staged slice was, so the existing range stays correct.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

import type { Segment } from '../../../../../lib/types/view-models';
import {
    accordionNavCursor,
    playingSegmentIndex,
    segPort,
    stagedPlayheadWindow,
} from '../../../stores/playback';
import { reanchorPlayingAfterSplit } from '../playback';

function piece(index: number, start: number, end: number, uid: string): Segment {
    return {
        segment_uid: uid,
        index,
        chapter: 1,
        time_start: start,
        time_end: end,
        matched_ref: '1:1:1-1:1:1',
        matched_text: 'x',
        confidence: 1,
    } as unknown as Segment;
}

/** Piece 0 keeps the parent uid + index; piece 1 is the staged child. */
const PIECE_0 = piece(4, 1000, 2000, 'parent');
const PIECE_1 = piece(5, 2000, 3000, 'child');
const PIECES = [PIECE_0, PIECE_1];

/** The user is on the second piece — what the nav cursor records on play. */
function onSecondPiece(): void {
    accordionNavCursor.set({ uid: 'child', chapter: 1, index: 4, startMs: 2000, endMs: 3000 });
}

function atTime(ms: number): void {
    vi.spyOn(segPort, 'currentTimeMs').mockReturnValue(ms);
}

let pause: ReturnType<typeof vi.spyOn>;
let seek: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
    // The pair as `reconcilePlayingAfterMutation` leaves it: piece 0.
    playingSegmentIndex.set({ chapter: 1, index: 4, origin: 'accordion' });
    stagedPlayheadWindow.set({ start: 2000, end: 3000 });
    accordionNavCursor.set(null);
    pause = vi.spyOn(segPort, 'pause').mockImplementation(() => {});
    seek = vi.spyOn(segPort, 'seek').mockImplementation(() => {});
});

afterEach(() => {
    vi.restoreAllMocks();
    playingSegmentIndex.set(null);
    stagedPlayheadWindow.set(null);
    accordionNavCursor.set(null);
});

describe('reanchorPlayingAfterSplit', () => {
    it('moves the pair onto the piece the user is on', () => {
        onSecondPiece();
        atTime(2500);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 5, origin: 'accordion' });
    });

    it('still anchors to the second piece when its bounded play just finished', () => {
        // Playhead parked exactly on the piece end — contained by no window.
        onSecondPiece();
        atTime(3000);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 5, origin: 'accordion' });
    });

    it('never pauses or seeks the port — labelling must not disturb playback', () => {
        onSecondPiece();
        atTime(2500);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(pause).not.toHaveBeenCalled();
        expect(seek).not.toHaveBeenCalled();
    });

    it('falls back to the playhead when no nav cursor is recorded', () => {
        atTime(2500);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(playingSegmentIndex)?.index).toBe(5);
    });

    it('falls back to the last piece the playhead has reached', () => {
        atTime(3500); // past every window
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(playingSegmentIndex)?.index).toBe(5);
    });

    it('clears the staged window once the pieces are real rows', () => {
        onSecondPiece();
        atTime(2500);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(stagedPlayheadWindow)).toBeNull();
    });

    it('leaves everything alone when the pair already names the right piece', () => {
        accordionNavCursor.set({ uid: 'parent', chapter: 1, index: 4, startMs: 1000, endMs: 2000 });
        atTime(1500);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 4, origin: 'accordion' });
        expect(get(stagedPlayheadWindow)).toEqual({ start: 2000, end: 3000 });
    });

    it('never moves the nav cursor — an edit must not change where ↑/↓ steps from', () => {
        onSecondPiece();
        const before = get(accordionNavCursor);
        atTime(2500);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(accordionNavCursor)).toEqual(before);
    });

    it('is inert before the playhead reaches the first piece', () => {
        atTime(0);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(playingSegmentIndex)?.index).toBe(4);
    });

    it('is inert for a single-piece result, nothing playing, or another chapter', () => {
        atTime(2500);
        onSecondPiece();

        reanchorPlayingAfterSplit(1, [PIECE_0]);
        expect(get(playingSegmentIndex)?.index).toBe(4);

        reanchorPlayingAfterSplit(2, PIECES);
        expect(get(playingSegmentIndex)?.index).toBe(4);

        playingSegmentIndex.set(null);
        reanchorPlayingAfterSplit(1, PIECES);
        expect(get(playingSegmentIndex)).toBeNull();
    });
});
