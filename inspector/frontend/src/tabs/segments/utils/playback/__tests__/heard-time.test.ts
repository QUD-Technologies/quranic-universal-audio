/**
 * Group play across a card's pieces with real output latency: the row
 * highlight, the nav cursor and the waveform cursor must all cross a piece
 * edge on the HEARD clock.
 *
 * The regression: the cursor drew at `currentTime - outputLatency`, but the
 * pair followed the raw clock, so on a high-latency output (Bluetooth, some
 * Windows stacks) the highlight jumped into the next piece while the previous
 * one was still audible, the cursor pinned at the new piece's left edge.
 * Starting a play on a later staged piece subtracted the latency from its
 * start and lit the piece BEFORE it (and moved the nav cursor there) until the
 * audio caught up.
 */
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const LATENCY_MS = 200;

vi.mock('../../../../../lib/playback/audio-graph', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    displayTimeMs: (t: number) => Math.max(0, t - LATENCY_MS),
}));

const rangeTicks: Array<(ms: number) => void> = [];
vi.mock('../../../../../lib/playback/audio-range', () => ({
    AudioRange: class {
        constructor(opts: { onTick: (ms: number) => void }) {
            rangeTicks.push(opts.onTick);
        }
        start(): void {}
        attach(): void {}
        dispose(): void {}
        setRange(): void {}
    },
}));

vi.mock('../../waveform/utils', () => ({ _fetchPeaksForClick: () => Promise.resolve() }));

import type { Segment } from '../../../../../lib/types/view-models';
import { segAllData, segCurrentIdx, selectedReciter } from '../../../stores/chapter';
import {
    accordionNavCursor,
    playingSegmentIndex,
    segPort,
    stagedPlayheadWindow,
} from '../../../stores/playback';
import { heardTimeMs } from '../heard-time';
import { playFromSegment } from '../playback';
import { clearRowRegistry, registerRow } from '../row-registry';

const AUDIO = 'http://x/ch1.mp3';

function seg(index: number, start: number, end: number, uid: string): Segment {
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

/** Two committed pieces of one card (real segments) and a staged parent. */
const PIECE_0 = seg(4, 1000, 2000, 'p0');
const PIECE_1 = seg(5, 2000, 3000, 'p1');
const PARENT = seg(7, 5000, 7000, 'parent');
const STAGED_0 = { ...PARENT, time_end: 6000, _staged: true } as Segment;
const STAGED_1 = { ...PARENT, time_start: 6000, segment_uid: 'child', _staged: true } as Segment;

function lastTick(ms: number): void {
    rangeTicks[rangeTicks.length - 1]!(ms);
}

beforeEach(() => {
    rangeTicks.length = 0;
    selectedReciter.set('');
    segAllData.set({
        segments: [PIECE_0, PIECE_1, PARENT],
        audio_by_chapter: { '1': AUDIO },
    } as never);
    segPort.attachElement(document.createElement('audio'));
});

afterEach(() => {
    clearRowRegistry();
    segPort.attachElement(null);
    segAllData.set(null);
    playingSegmentIndex.set(null);
    accordionNavCursor.set(null);
    stagedPlayheadWindow.set(null);
    segCurrentIdx.set(-1);
});

describe('heardTimeMs', () => {
    it('trails the raw clock by the output latency', () => {
        expect(heardTimeMs(2500, 1000)).toBe(2300);
    });

    it('holds at the play start until the audio catches up', () => {
        expect(heardTimeMs(2050, 2000)).toBe(2000);
    });

    it('does not pin a seek back past the play start', () => {
        expect(heardTimeMs(1500, 2000)).toBe(1300);
    });
});

describe('group play across real pieces', () => {
    beforeEach(() => {
        playFromSegment(4, 1, undefined, { isAccordionPlay: true, groupEndMs: 3000 });
    });

    it('keeps the pair on the first piece while it is still audible', () => {
        lastTick(2100); // raw past the edge, heard at 1900
        expect(get(playingSegmentIndex)?.index).toBe(4);
        expect(get(accordionNavCursor)?.uid).toBe('p0');
    });

    it('moves the pair once the piece edge is heard', () => {
        lastTick(2250);
        expect(get(playingSegmentIndex)?.index).toBe(5);
        expect(get(segCurrentIdx)).toBe(5);
        expect(get(accordionNavCursor)?.uid).toBe('p1');
    });
});

describe('play started on a later staged piece', () => {
    beforeEach(() => {
        const row = document.createElement('div');
        const canvas = document.createElement('canvas');
        registerRow(1, 7, row, canvas, Symbol('s0'), 'accordion', STAGED_0);
        registerRow(1, 7, row, canvas, Symbol('s1'), 'accordion', STAGED_1);
        playFromSegment(7, 1, 6000, {
            isAccordionPlay: true,
            piece: { uid: 'child', startMs: 6000, endMs: 7000 },
            groupEndMs: 7000,
        });
    });

    it('lights the piece being played, not the one before it', () => {
        lastTick(6000);
        expect(get(stagedPlayheadWindow)).toEqual({ start: 6000, end: 7000 });
        expect(get(accordionNavCursor)?.uid).toBe('child');
    });
});
