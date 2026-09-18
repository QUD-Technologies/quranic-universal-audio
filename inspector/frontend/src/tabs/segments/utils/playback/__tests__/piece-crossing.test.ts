/**
 * Playing across a card's contiguous pieces: the pair — and with it the row
 * highlight and the waveform cursor — must follow the playhead from piece to
 * piece.
 *
 * The regression: a card's pieces are contiguous, so a group play crosses from
 * one into the next with no new `play()`. Nothing moved the pair across that
 * crossing for an accordion play — `onSegTimeUpdate` bails whenever the port
 * isn't playing the *active* chapter (accordion cards routinely mount rows
 * from another chapter) and otherwise only scans the main list's filtered
 * slice, which need not contain the card's rows. So the highlight and cursor
 * stayed on the piece that started the play while the audio ran on into the
 * next one.
 */
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { Segment } from '../../../../../lib/types/view-models';
import { segAllData, segCurrentIdx } from '../../../stores/chapter';
import { accordionNavCursor, playingSegmentIndex } from '../../../stores/playback';
import { followGroupPlayhead } from '../playback';

const AUDIO = 'http://x/ch1.mp3';

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
        audio_url: AUDIO,
    } as unknown as Segment;
}

/** The two pieces of a labelled cross-verse split, and the group they span. */
const PIECE_0 = piece(4, 1000, 2000, 'parent');
const PIECE_1 = piece(5, 2000, 3000, 'child');
const GROUP_END = 3000;

beforeEach(() => {
    segAllData.set({
        segments: [PIECE_0, PIECE_1],
        audio_by_chapter: { '1': AUDIO },
    } as never);
    // Playing piece 0 of the group, as a play on its row leaves things.
    playingSegmentIndex.set({ chapter: 1, index: 4, origin: 'accordion' });
    segCurrentIdx.set(4);
    accordionNavCursor.set({ uid: 'parent', chapter: 1, index: 4, startMs: 1000, endMs: 2000 });
});

afterEach(() => {
    segAllData.set(null);
    playingSegmentIndex.set(null);
    accordionNavCursor.set(null);
    segCurrentIdx.set(-1);
});

describe('followGroupPlayhead', () => {
    it('moves the pair onto piece 2 once the playhead crosses into it', () => {
        followGroupPlayhead(2500, GROUP_END);
        expect(get(playingSegmentIndex)).toEqual({ chapter: 1, index: 5, origin: 'accordion' });
    });

    it('moves segCurrentIdx in lockstep, so updateSegHighlight does not snap back', () => {
        followGroupPlayhead(2500, GROUP_END);
        expect(get(segCurrentIdx)).toBe(5);
    });

    it('carries the nav cursor onto piece 2, so ↑/↓ steps from what is playing', () => {
        followGroupPlayhead(2500, GROUP_END);
        expect(get(accordionNavCursor)).toEqual({
            uid: 'child',
            chapter: 1,
            index: 5,
            startMs: 2000,
            endMs: 3000,
        });
    });

    it('holds everything while the playhead is still inside the first piece', () => {
        followGroupPlayhead(1500, GROUP_END);
        expect(get(playingSegmentIndex)?.index).toBe(4);
        expect(get(segCurrentIdx)).toBe(4);
        expect(get(accordionNavCursor)?.uid).toBe('parent');
    });

    it('is inert for a single-segment play (no group bound)', () => {
        followGroupPlayhead(2500, null);
        expect(get(playingSegmentIndex)?.index).toBe(4);
        expect(get(segCurrentIdx)).toBe(4);
    });

    it('is inert past the end of every piece', () => {
        followGroupPlayhead(3500, GROUP_END);
        expect(get(playingSegmentIndex)?.index).toBe(4);
    });

    it('is inert with nothing playing', () => {
        playingSegmentIndex.set(null);
        followGroupPlayhead(2500, GROUP_END);
        expect(get(playingSegmentIndex)).toBeNull();
    });

    it('follows backwards too, when the user scrubs into an earlier piece', () => {
        playingSegmentIndex.set({ chapter: 1, index: 5, origin: 'accordion' });
        segCurrentIdx.set(5);
        followGroupPlayhead(1500, GROUP_END);
        expect(get(playingSegmentIndex)?.index).toBe(4);
        expect(get(segCurrentIdx)).toBe(4);
    });
});
