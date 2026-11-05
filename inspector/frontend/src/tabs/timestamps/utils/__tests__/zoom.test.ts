import { get } from 'svelte/store';
import { afterEach,beforeEach, describe, expect, it, vi } from 'vitest';

import type { TsVerseData, TsWord } from '../../../../lib/types/ts-client';
import { TS_VIEW_MODES,viewMode } from '../../stores/display';
import { loopTarget } from '../../stores/playback';
import type { TsLoadedVerse } from '../../stores/verse';
import { loadedVerse } from '../../stores/verse';
import { tsZoom } from '../../stores/zoom';
import {
    TS_ZOOM_ANIMATE_MAX_MS,
    TS_ZOOM_ANIMATE_MIN_MS,
    TS_ZOOM_ANIMATE_MS,
    TS_ZOOM_ANIMATE_MS_PER_SEC,
} from '../constants';
import {
    _resetZoomModuleForTests,
    animateZoomOut,
    animateZoomTo,
    computeSweepDurationMs,
    computeZoomView,
    isTsZoomAnimating,
    setupZoomLifecycle,
    zoomToWord,
} from '../zoom';

// ---- Fixture helpers --------------------------------------------------------

function word(start: number, end: number, idx = 0): TsWord {
    return {
        location: `1:1:${idx + 1}`,
        text: `w${idx}`,
        start,
        end,
        phoneme_indices: [],
        letters: [],
    };
}

/**
 * Build a `TsLoadedVerse` whose slice is [0, fullSpanSec] with the given
 * words. Values we don't care about for zoom tests are stubbed with shape-
 * minimal defaults.
 */
function loadedVerseFixture(fullSpanSec: number, words: TsWord[]): TsLoadedVerse {
    const data: TsVerseData = {
        reciter: 'r',
        chapter: 1,
        verse_ref: '1:1',
        audio_url: 'http://audio/1.mp3',
        audio_category: 'by_ayah_audio',
        time_start_ms: 0,
        time_end_ms: fullSpanSec * 1000,
        intervals: [],
        words,
        native: [],
        wordReadings: [],
    };
    return { data, tsSegOffset: 0, tsSegEnd: fullSpanSec };
}

// ---- computeZoomView (pure math) --------------------------------------------

describe('computeZoomView', () => {
    it('centers an interior word with padding = word duration on each side', () => {
        // fullSpan 10s, word [4, 5] (dur 1). Expected width = 2, centered on 4.5 → [3.5, 5.5].
        const v = computeZoomView(4, 5, 10);
        expect(v).toEqual({ viewStart: 3.5, viewEnd: 5.5 });
    });

    it('clamps the left side to 0 and keeps the full 50% padding on the right', () => {
        // Word [0, 0.5], dur=0.5, pad=0.25. Natural window [-0.25, 0.75];
        // left clamp → [0, 0.75]. View narrower than 2 * dur by the amount
        // of padding the left side lost.
        const v = computeZoomView(0, 0.5, 10);
        expect(v).not.toBeNull();
        expect(v!.viewStart).toBe(0);
        expect(v!.viewEnd).toBeCloseTo(0.75, 9);
        // Right padding preserved at exactly 50% of word duration.
        expect(v!.viewEnd - 0.5).toBeCloseTo(0.25, 9);
    });

    it('clamps the right side to fullSpan and keeps the full 50% padding on the left', () => {
        // Word [9.8, 10], dur=0.2, pad=0.1. Natural window [9.7, 10.1];
        // right clamp → [9.7, 10].
        const v = computeZoomView(9.8, 10, 10);
        expect(v).not.toBeNull();
        expect(v!.viewEnd).toBe(10);
        expect(v!.viewStart).toBeCloseTo(9.7, 9);
        // Left padding preserved at exactly 50% of word duration.
        expect(9.8 - v!.viewStart).toBeCloseTo(0.1, 9);
    });

    it('single-side clamp does NOT collapse to full view when the other side has slack', () => {
        // Word [2, 7] in span 10: dur=5, pad=2.5 → natural [-0.5, 9.5].
        // Left clamp → [0, 9.5]. Old shift-preserve-width rule would have
        // returned null here (2*dur == fullSpan); new rule keeps the zoom.
        const v = computeZoomView(2, 7, 10);
        expect(v).not.toBeNull();
        expect(v!.viewStart).toBe(0);
        expect(v!.viewEnd).toBeCloseTo(9.5, 9);
    });

    it('returns null when the clamped window spans the entire slice', () => {
        // Word spans the whole slice → both sides clamp, width = fullSpan.
        expect(computeZoomView(0, 10, 10)).toBeNull();
        // Large word whose padding on both sides overruns the slice.
        expect(computeZoomView(1, 9, 10)).toBeNull(); // natural [-3, 13] → [0, 10].
    });

    it('returns null for zero / invalid inputs', () => {
        expect(computeZoomView(0, 0, 10)).toBeNull();
        expect(computeZoomView(5, 3, 10)).toBeNull();
        expect(computeZoomView(3, 5, 0)).toBeNull();
    });

    it('invariant: width is always exactly 2 * word_duration in the interior', () => {
        // Arbitrary interior word positions — width must not vary with location.
        for (const [s, e] of [[1, 2], [3, 3.5], [5, 6.2], [0.1, 0.15]]) {
            const v = computeZoomView(s!, e!, 20);
            expect(v).not.toBeNull();
            expect(v!.viewEnd - v!.viewStart).toBeCloseTo(2 * (e! - s!), 9);
        }
    });
});

// ---- computeSweepDurationMs -------------------------------------------------

describe('computeSweepDurationMs', () => {
    it('returns MIN when from == to (zero distance)', () => {
        const v = { viewStart: 3, viewEnd: 5 };
        expect(computeSweepDurationMs(v, v)).toBe(TS_ZOOM_ANIMATE_MIN_MS);
    });

    it('scales linearly with center-distance in the middle range', () => {
        // A 4-second center shift at 35 ms/s = 140 ms base contribution;
        // plus the MIN floor of 180 → 180. But pick something larger:
        // 20s of distance → 20 * 35 = 700 ms; plus MIN 180 → 880 ms; clamped at MAX 900.
        const from = { viewStart: 0, viewEnd: 2 }; // center 1
        const to   = { viewStart: 20, viewEnd: 22 }; // center 21 → distance 20
        const d = computeSweepDurationMs(from, to);
        expect(d).toBeGreaterThan(TS_ZOOM_ANIMATE_MIN_MS);
        expect(d).toBeLessThanOrEqual(TS_ZOOM_ANIMATE_MAX_MS);
        // Exact: 180 + 20*35 = 880.
        expect(d).toBeCloseTo(TS_ZOOM_ANIMATE_MIN_MS + 20 * TS_ZOOM_ANIMATE_MS_PER_SEC, 6);
    });

    it('caps at MAX for very long jumps', () => {
        const from = { viewStart: 0, viewEnd: 1 };
        const to   = { viewStart: 1000, viewEnd: 1001 }; // huge distance
        expect(computeSweepDurationMs(from, to)).toBe(TS_ZOOM_ANIMATE_MAX_MS);
    });

    it('longer jump produces a longer (or equal) duration than shorter jump', () => {
        const origin = { viewStart: 0, viewEnd: 2 };
        const near = { viewStart: 3, viewEnd: 5 };     // center distance ~4s
        const far  = { viewStart: 15, viewEnd: 17 };    // center distance ~16s
        expect(computeSweepDurationMs(origin, far))
            .toBeGreaterThan(computeSweepDurationMs(origin, near));
    });
});

// ---- zoomToWord + animateZoomTo --------------------------------------------

describe('zoomToWord', () => {
    beforeEach(() => {
        _resetZoomModuleForTests();
        tsZoom.set(null);
        loopTarget.set(null);
        loadedVerse.set(loadedVerseFixture(10, [word(4, 5, 0)]));
    });

    it('commits the computed view synchronously (no animation)', () => {
        zoomToWord(4, 5);
        expect(get(tsZoom)).toEqual({ viewStart: 3.5, viewEnd: 5.5 });
    });

    it('clears tsZoom when the clamped window covers the whole slice', () => {
        tsZoom.set({ viewStart: 2, viewEnd: 5 });
        // Word dominates the slice: natural [-4, 14] clamps to [0, 10].
        zoomToWord(1, 9);
        expect(get(tsZoom)).toBeNull();
    });
});

describe('animateZoomTo with fake rAF', () => {
    /** Collected rAF callbacks — we pump them manually. */
    let rafQueue: Array<(t: number) => void>;
    let rafCounter: number;
    let perfNow: number;

    beforeEach(() => {
        _resetZoomModuleForTests();
        tsZoom.set(null);
        loopTarget.set(null);
        loadedVerse.set(loadedVerseFixture(10, [word(4, 5, 0)]));

        rafQueue = [];
        rafCounter = 0;
        perfNow = 1000;
        vi.stubGlobal('requestAnimationFrame', (cb: (t: number) => void) => {
            rafCounter += 1;
            rafQueue.push(cb);
            return rafCounter;
        });
        vi.stubGlobal('cancelAnimationFrame', (id: number) => {
            // Simulate: drop callback at that id (track via index). Simpler: mark it
            // cancelled by index — but our zoom code stores only the active id, so a
            // cancel effectively means "don't keep running this chain." We emulate by
            // clearing the queue when the active id is cancelled; the test pumps what
            // remains, so a cancelled animation stops producing new frames.
            void id;
            rafQueue = [];
        });
        vi.stubGlobal('performance', { now: () => perfNow });
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    function pump(deltaMs: number): void {
        perfNow += deltaMs;
        const q = rafQueue;
        rafQueue = [];
        for (const cb of q) cb(perfNow);
    }

    it('snaps immediately when target is null', () => {
        tsZoom.set({ viewStart: 1, viewEnd: 3 });
        animateZoomTo(null, TS_ZOOM_ANIMATE_MS);
        expect(get(tsZoom)).toBeNull();
        expect(isTsZoomAnimating()).toBe(false);
    });

    it('snaps when duration <= 0', () => {
        animateZoomTo({ viewStart: 2, viewEnd: 4 }, 0);
        expect(get(tsZoom)).toEqual({ viewStart: 2, viewEnd: 4 });
        expect(isTsZoomAnimating()).toBe(false);
    });

    it('animates from [0, fullSpan] when tsZoom is null and lands exactly at target', () => {
        animateZoomTo({ viewStart: 3.5, viewEnd: 5.5 }, 200);
        expect(isTsZoomAnimating()).toBe(true);
        // First frame at t=0ms: ease=0, view should still equal from-view.
        pump(0);
        const f0 = get(tsZoom)!;
        expect(f0.viewStart).toBeCloseTo(0, 6);
        expect(f0.viewEnd).toBeCloseTo(10, 6);
        // Partial progress at 100ms → somewhere between from and to.
        pump(100);
        const mid = get(tsZoom)!;
        expect(mid.viewStart).toBeGreaterThan(0);
        expect(mid.viewStart).toBeLessThan(3.5);
        expect(mid.viewEnd).toBeGreaterThan(5.5);
        expect(mid.viewEnd).toBeLessThan(10);
        // Past duration → exact target, animating flag cleared.
        pump(200);
        expect(get(tsZoom)).toEqual({ viewStart: 3.5, viewEnd: 5.5 });
        expect(isTsZoomAnimating()).toBe(false);
    });

    it('skips the frame loop when from == to within epsilon', () => {
        tsZoom.set({ viewStart: 3.5, viewEnd: 5.5 });
        animateZoomTo({ viewStart: 3.5, viewEnd: 5.5 }, 200);
        // Should have committed without queueing a frame.
        expect(isTsZoomAnimating()).toBe(false);
        expect(rafQueue).toHaveLength(0);
    });

    it('cancels an in-flight tween when a new one starts', () => {
        animateZoomTo({ viewStart: 0, viewEnd: 2 }, 200);
        expect(isTsZoomAnimating()).toBe(true);
        pump(50); // one frame of the first tween
        // Start a new tween → previous cancelled, new one in flight.
        animateZoomTo({ viewStart: 5, viewEnd: 7 }, 200);
        expect(isTsZoomAnimating()).toBe(true);
        // Finish the new tween.
        pump(0);
        pump(300);
        expect(get(tsZoom)).toEqual({ viewStart: 5, viewEnd: 7 });
        expect(isTsZoomAnimating()).toBe(false);
    });

    it('animateZoomOut tweens current view → [0, fullSpan] then sets tsZoom to null', () => {
        tsZoom.set({ viewStart: 3.5, viewEnd: 5.5 });
        animateZoomOut(200);
        expect(isTsZoomAnimating()).toBe(true);
        // Pump the initial frame: tsZoom is still close to the starting window.
        pump(0);
        const f0 = get(tsZoom)!;
        expect(f0.viewStart).toBeCloseTo(3.5, 4);
        expect(f0.viewEnd).toBeCloseTo(5.5, 4);

        // Halfway: view has widened toward full but hasn't reached it yet.
        pump(100);
        const mid = get(tsZoom);
        expect(mid).not.toBeNull();
        expect(mid!.viewStart).toBeLessThan(3.5);
        expect(mid!.viewEnd).toBeGreaterThan(5.5);
        expect(mid!.viewStart).toBeGreaterThan(0);
        expect(mid!.viewEnd).toBeLessThan(10);

        // End of tween: tsZoom finalises to null.
        pump(200);
        expect(get(tsZoom)).toBeNull();
        expect(isTsZoomAnimating()).toBe(false);
    });

    it('animateZoomOut is a no-op when tsZoom is already null', () => {
        tsZoom.set(null);
        animateZoomOut(200);
        expect(get(tsZoom)).toBeNull();
        expect(isTsZoomAnimating()).toBe(false);
        expect(rafQueue).toHaveLength(0);
    });

    it('animateZoomOut with duration 0 snaps straight to null', () => {
        tsZoom.set({ viewStart: 3.5, viewEnd: 5.5 });
        animateZoomOut(0);
        expect(get(tsZoom)).toBeNull();
        expect(isTsZoomAnimating()).toBe(false);
    });
});

// ---- setupZoomLifecycle -----------------------------------------------------

describe('setupZoomLifecycle', () => {
    /**
     * Same rAF stub as above, but we prefer `durationMs=0` paths in lifecycle
     * tests by asserting post-tween state — we just pump enough time to
     * unambiguously complete the tween.
     */
    let rafQueue: Array<(t: number) => void>;
    let perfNow: number;

    beforeEach(() => {
        _resetZoomModuleForTests();
        tsZoom.set(null);
        loopTarget.set(null);
        viewMode.set(TS_VIEW_MODES.ANALYSIS);
        loadedVerse.set(
            loadedVerseFixture(10, [
                word(1, 2, 0), // 0: dur 1 → width 2
                word(4, 5, 1), // 1: dur 1 → width 2
                word(6, 8, 2), // 2: dur 2 → width 4
            ]),
        );

        rafQueue = [];
        perfNow = 1000;
        vi.stubGlobal('requestAnimationFrame', (cb: (t: number) => void) => {
            rafQueue.push(cb);
            return rafQueue.length;
        });
        vi.stubGlobal('cancelAnimationFrame', () => {
            rafQueue = [];
        });
        vi.stubGlobal('performance', { now: () => perfNow });
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    function finishTween(): void {
        // Pump past the longest possible sweep duration. Lifecycle sweeps
        // scale with center-distance; on our 10s fixture the max is bounded
        // well below TS_ZOOM_ANIMATE_MAX_MS, but we use the cap as a safe
        // upper bound.
        perfNow += TS_ZOOM_ANIMATE_MAX_MS + 50;
        while (rafQueue.length) {
            const q = rafQueue;
            rafQueue = [];
            for (const cb of q) cb(perfNow);
        }
    }

    it('entry: null → target animates to zoomViewFor(target.wordIndex)', () => {
        setupZoomLifecycle();
        loopTarget.set({ kind: 'word', wordIndex: 1, startSec: 4, endSec: 5 });
        finishTween();
        // word 1: [4, 5] → width 2, centered on 4.5 → [3.5, 5.5].
        expect(get(tsZoom)).toEqual({ viewStart: 3.5, viewEnd: 5.5 });
    });

    it('switch: word A → word B animates to zoomViewFor(B) (width resets to 2 * dur(B))', () => {
        setupZoomLifecycle();
        loopTarget.set({ kind: 'word', wordIndex: 0, startSec: 1, endSec: 2 });
        finishTween();
        expect(get(tsZoom)).toEqual({ viewStart: 0.5, viewEnd: 2.5 }); // width 2
        loopTarget.set({ kind: 'word', wordIndex: 2, startSec: 6, endSec: 8 });
        finishTween();
        // word 2: [6, 8] → width 4, centered on 7 → [5, 9].
        expect(get(tsZoom)).toEqual({ viewStart: 5, viewEnd: 9 });
    });

    it('exit: target → null animates out to full view, THEN clears tsZoom', () => {
        setupZoomLifecycle();
        loopTarget.set({ kind: 'word', wordIndex: 1, startSec: 4, endSec: 5 });
        finishTween();
        const zoomed = get(tsZoom);
        expect(zoomed).toEqual({ viewStart: 3.5, viewEnd: 5.5 });

        loopTarget.set(null);
        // Mid-animation: tsZoom is NOT null yet — it's widening toward full.
        expect(isTsZoomAnimating()).toBe(true);
        expect(get(tsZoom)).not.toBeNull();

        // Partway through: bounds should be moving toward [0, fullSpan] (10)
        // but not yet there.
        // Pump one frame at t=0, then advance ~halfway.
        perfNow += 16;
        if (rafQueue.length) {
            const q1 = rafQueue;
            rafQueue = [];
            for (const cb of q1) cb(perfNow);
        }
        const mid = get(tsZoom);
        expect(mid).not.toBeNull();
        expect(mid!.viewStart).toBeLessThanOrEqual(zoomed!.viewStart);
        expect(mid!.viewEnd).toBeGreaterThanOrEqual(zoomed!.viewEnd);

        // Complete the tween — tsZoom finalises to null.
        finishTween();
        expect(get(tsZoom)).toBeNull();
        expect(isTsZoomAnimating()).toBe(false);
    });

    it('invariant: exit + re-enter on word B produces SAME width as switch from A → B', () => {
        setupZoomLifecycle();
        // Path 1 — switch A → B.
        loopTarget.set({ kind: 'word', wordIndex: 0, startSec: 1, endSec: 2 });
        finishTween();
        loopTarget.set({ kind: 'word', wordIndex: 1, startSec: 4, endSec: 5 });
        finishTween();
        const switchView = get(tsZoom);

        // Path 2 — exit, re-enter on B.
        loopTarget.set(null);
        loopTarget.set({ kind: 'word', wordIndex: 1, startSec: 4, endSec: 5 });
        finishTween();
        const reEnterView = get(tsZoom);

        expect(reEnterView).toEqual(switchView);
        // And specifically 2 * dur(B) = 2.
        expect(reEnterView!.viewEnd - reEnterView!.viewStart).toBeCloseTo(2, 9);
    });

    it('drill within same word (word A → letter in A): no zoom change', () => {
        setupZoomLifecycle();
        loopTarget.set({ kind: 'word', wordIndex: 1, startSec: 4, endSec: 5 });
        finishTween();
        const before = get(tsZoom);
        loopTarget.set({
            kind: 'letter',
            wordIndex: 1,
            childIndex: 0,
            startSec: 4.1,
            endSec: 4.3,
        });
        // No new rAF should have been queued; zoom stays exactly.
        expect(get(tsZoom)).toEqual(before);
    });

    it('animation-view entries are ignored (no zoom)', () => {
        viewMode.set(TS_VIEW_MODES.ANIMATION);
        setupZoomLifecycle();
        loopTarget.set({ kind: 'word', wordIndex: 1, startSec: 4, endSec: 5 });
        finishTween();
        expect(get(tsZoom)).toBeNull();
    });

    it('sweep duration scales with jump distance (far jump runs longer)', () => {
        // Build a fixture with three words at very different positions so
        // fromCenter→toCenter distances differ materially.
        loadedVerse.set(
            loadedVerseFixture(60, [
                word(0, 1, 0),    // near start
                word(2, 3, 1),    // close neighbour of word 0
                word(50, 51, 2),  // far away
            ]),
        );
        setupZoomLifecycle();

        // Enter loop on word 0 → sweep from full view [0, 60].
        loopTarget.set({ kind: 'word', wordIndex: 0, startSec: 0, endSec: 1 });
        // Count frames the tween queues before completion.
        const frameCount = (): number => {
            let n = 0;
            while (rafQueue.length) {
                n += 1;
                perfNow += 16;
                const q = rafQueue;
                rafQueue = [];
                for (const cb of q) cb(perfNow);
            }
            return n;
        };
        const entryFrames = frameCount();

        // Switch to near neighbour (word 1) — small center delta.
        loopTarget.set({ kind: 'word', wordIndex: 1, startSec: 2, endSec: 3 });
        const nearFrames = frameCount();

        // Switch to far word (word 2) — much larger center delta.
        loopTarget.set({ kind: 'word', wordIndex: 2, startSec: 50, endSec: 51 });
        const farFrames = frameCount();

        // Each animation actually ran some frames.
        expect(entryFrames).toBeGreaterThan(0);
        expect(nearFrames).toBeGreaterThan(0);
        expect(farFrames).toBeGreaterThan(0);
        // The far jump spent more frames than the near jump — proxy for "took longer".
        expect(farFrames).toBeGreaterThan(nearFrames);
    });

    it('chapter change snaps tsZoom to null', () => {
        setupZoomLifecycle();
        loopTarget.set({ kind: 'word', wordIndex: 1, startSec: 4, endSec: 5 });
        finishTween();
        expect(get(tsZoom)).not.toBeNull();
        // Change chapter → chapter-change path clears the zoom. (by_surah audio
        // is one file per chapter, so this is the navigation that resets.)
        const cur = get(loadedVerse)!;
        loadedVerse.set({
            ...cur,
            data: { ...cur.data, chapter: cur.data.chapter + 1 },
        });
        expect(get(tsZoom)).toBeNull();
    });
});
