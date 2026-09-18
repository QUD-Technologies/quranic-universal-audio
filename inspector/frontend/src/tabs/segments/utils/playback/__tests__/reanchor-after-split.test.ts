/**
 * After a split replaces one segment with several, the playing pair must name
 * the piece the PLAYHEAD is inside.
 *
 * `reconcilePlayingAfterMutation` walks the pre-mutation UID forward, and a
 * split preserves that UID on piece 0 — so labelling a staged cross-verse
 * boundary while a later piece was sounding pinned the pair to piece 0, the
 * draw loop clamped the cursor to piece 0's window, and it sat frozen at that
 * row's edge while its sibling played on.
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
import { reanchorPlayingToPlayhead } from '../playback';

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

const PIECE_0 = piece(4, 1000, 2000, 'parent');
const PIECE_1 = piece(5, 2000, 3000, 'child');

function atTime(ms: number): void {
    vi.spyOn(segPort, 'currentTimeMs').mockReturnValue(ms);
}

beforeEach(() => {
    playingSegmentIndex.set({ chapter: 1, index: 4, origin: 'accordion' });
    stagedPlayheadWindow.set({ start: 2000, end: 3000 });
    accordionNavCursor.set(null);
});

afterEach(() => {
    vi.restoreAllMocks();
    playingSegmentIndex.set(null);
    stagedPlayheadWindow.set(null);
    accordionNavCursor.set(null);
});

describe('reanchorPlayingToPlayhead', () => {
    it('moves the pair onto the later piece the playhead is inside', () => {
        atTime(2500);
        reanchorPlayingToPlayhead(1, [PIECE_0, PIECE_1]);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 5, origin: 'accordion' });
    });

    it('clears the staged playhead window — the pieces are real rows now', () => {
        atTime(2500);
        reanchorPlayingToPlayhead(1, [PIECE_0, PIECE_1]);
        expect(get(stagedPlayheadWindow)).toBeNull();
    });

    it('leaves the pair alone when the playhead is already in the named piece', () => {
        atTime(1500);
        reanchorPlayingToPlayhead(1, [PIECE_0, PIECE_1]);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 4, origin: 'accordion' });
        expect(get(stagedPlayheadWindow)).toEqual({ start: 2000, end: 3000 });
    });

    it('never moves the nav cursor — an edit must not change where ↑/↓ steps from', () => {
        const cursor = { uid: 'child', chapter: 1, index: 4, startMs: 2000, endMs: 3000 };
        accordionNavCursor.set({ ...cursor });
        atTime(2500);
        reanchorPlayingToPlayhead(1, [PIECE_0, PIECE_1]);
        expect(get(accordionNavCursor)).toEqual(cursor);
    });

    it('is inert when the playhead is outside every piece', () => {
        atTime(9000);
        reanchorPlayingToPlayhead(1, [PIECE_0, PIECE_1]);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 4, origin: 'accordion' });
    });

    it('is inert for a single-piece result', () => {
        atTime(2500);
        reanchorPlayingToPlayhead(1, [PIECE_0]);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 4, origin: 'accordion' });
    });

    it('is inert when nothing is playing', () => {
        playingSegmentIndex.set(null);
        atTime(2500);
        reanchorPlayingToPlayhead(1, [PIECE_0, PIECE_1]);
        expect(get(playingSegmentIndex)).toBeNull();
    });

    it('is inert for another chapter', () => {
        atTime(2500);
        reanchorPlayingToPlayhead(2, [PIECE_0, PIECE_1]);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 4, origin: 'accordion' });
    });
});
