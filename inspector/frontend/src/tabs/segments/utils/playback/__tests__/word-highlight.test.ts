import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const clock = vi.hoisted(() => ({ latencyMs: 0 }));
vi.mock('../../../../../lib/playback/audio-graph', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    displayTimeMs: (time: number) => Math.max(0, time - clock.latencyMs),
}));

import type { Segment } from '../../../../../lib/types/view-models';
import { segAllData, selectedReciter } from '../../../stores/chapter';
import { activeWordCursor, playingSegmentIndex, segPort } from '../../../stores/playback';
import { clearWordTimes, wordTimes } from '../../../stores/word-times';
import { drawActivePlayhead } from '../playback';

const AUDIO = 'http://x/ch1.mp3';
const words = [
    { location: '1:1:1', start_ms: 1000, end_ms: 1500 },
    { location: '1:1:2', start_ms: 1600, end_ms: 2000 },
    { location: '1:1:3', start_ms: 2400, end_ms: 3000 },
];
const segment = {
    segment_uid: 'timed', chapter: 1, index: 0,
    time_start: 900, time_end: 3200,
    matched_ref: '1:1:1-1:1:3', audio_url: AUDIO,
} as Segment;

function tick(rawMs: number, latencyMs = 0): number {
    clock.latencyMs = latencyMs;
    segPort.element!.currentTime = segPort.toClipMs(rawMs) / 1000;
    drawActivePlayhead();
    return get(activeWordCursor)?.wordIndex ?? -1;
}

beforeEach(() => {
    clock.latencyMs = 0;
    selectedReciter.set('');
    segAllData.set({ segments: [segment] } as never);
    wordTimes.set({ '1': { timed: { start_ms: 900, end_ms: 3200, words } } });
    segPort.attachElement(document.createElement('audio'));
    segPort.setSource({ audioUrl: AUDIO, reciter: 'fixture', vbr: false });
    playingSegmentIndex.set({ chapter: 1, index: 0 });
    activeWordCursor.set(null);
});

afterEach(() => {
    segPort.attachElement(null);
    segPort.setSource(null);
    segAllData.set(null);
    playingSegmentIndex.set(null);
    activeWordCursor.set(null);
    clearWordTimes();
});

describe('segment word highlight through the playback driver', () => {
    it('never steps backward when output latency changes during forward playback', () => {
        const observed = [tick(1450), tick(1620), tick(1640, 300), tick(1660)];
        expect(observed).toEqual([0, 1, 1, 1]);
    });

    it('holds across inter-word silence and the trailing silence', () => {
        expect([1000, 1500, 1599, 1600, 2000, 2399, 2400, 3100].map(t => tick(t)))
            .toEqual([0, 0, 0, 1, 1, 1, 2, 2]);
    });

    it('resets on a short backward seek into the gap before the active word', () => {
        expect(tick(1620)).toBe(1);
        segPort.seek(1550);
        drawActivePlayhead();
        expect(get(activeWordCursor)?.wordIndex).toBe(0);
    });

    it('selects the preceding word when seeking forward into silence', () => {
        expect(tick(1100)).toBe(0);
        segPort.seek(2200);
        drawActivePlayhead();
        expect(get(activeWordCursor)?.wordIndex).toBe(1);
    });

    it('clears before the first word after a replay seek', () => {
        expect(tick(1620)).toBe(1);
        segPort.seek(900);
        drawActivePlayhead();
        expect(get(activeWordCursor)).toBeNull();
    });

    it('resets when a clip changes even for the same segment pair', () => {
        expect(tick(2450)).toBe(2);
        segPort.setSource({ audioUrl: AUDIO, reciter: 'fixture', vbr: true });
        segPort.loadCovering(900, 3200);
        expect(tick(2200)).toBe(1);
    });

    it('resets for a native media seek', () => {
        expect(tick(1620)).toBe(1);
        segPort.element!.currentTime = 1.55;
        segPort.element!.dispatchEvent(new Event('seeking'));
        drawActivePlayhead();
        expect(get(activeWordCursor)?.wordIndex).toBe(0);
    });

    it('resets when the active segment changes', () => {
        expect(tick(2450)).toBe(2);
        const other = { ...segment, index: 1, segment_uid: 'other' };
        segAllData.set({ segments: [segment, other] } as never);
        wordTimes.update(all => ({ '1': { ...all['1'], other: { start_ms: 900, end_ms: 3200, words } } }));
        playingSegmentIndex.set({ chapter: 1, index: 1 });
        expect(tick(1100)).toBe(0);
    });

    it('recomputes when stored timing data is replaced or removed', () => {
        expect(tick(2450)).toBe(2);
        const retimed = words.map(w => ({ ...w, start_ms: w.start_ms + 800, end_ms: w.end_ms + 800 }));
        wordTimes.set({ '1': { timed: { start_ms: 900, end_ms: 3200, words: retimed } } });
        expect(tick(2450)).toBe(1);
        clearWordTimes();
        expect(tick(2450)).toBe(-1);
    });
});
