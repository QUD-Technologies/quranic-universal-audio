import { describe, expect, it } from 'vitest';

import {
    occasionIndexAt,
    resolveShuffleTick,
    shouldFireShuffle,
    speechSpan,
    type ShuffleTickOccasion,
} from '../shuffle-tick';

// Three occasions recited continuously → contiguous (each end == next start), so
// the playhead crosses a seam with no gap. This is the shape that triggered the
// short-verse leak.
const CONTIGUOUS: ShuffleTickOccasion[] = [
    { ref: '2:1', startMs: 0, endMs: 1000 },
    { ref: '2:2', startMs: 1000, endMs: 2000 },
    { ref: '2:3', startMs: 2000, endMs: 3000 },
];

const GUARD = 40;

describe('resolveShuffleTick', () => {
    it('fires against the auditioned occasion even when a skipped frame overshoots past its end', () => {
        // Auditioning 2:1 (ends at 1000). A starved rAF frame lands the playhead at
        // 1400 — already inside the contiguous next occasion. The fire must still
        // resolve against 2:1's end, NOT re-base onto 2:2 (which would leak it).
        const outcome = resolveShuffleTick({
            occasions: CONTIGUOUS,
            swapInFlight: false,
            ms: 1400,
            armed: true,
            focusEndMs: 1000,
            guardMs: GUARD,
            firedForCurrentFocus: false,
        });

        expect(outcome).toEqual({ kind: 'fire' });
    });

    it('fires within the early guard window before the exact occasion end', () => {
        const outcome = resolveShuffleTick({
            occasions: CONTIGUOUS,
            swapInFlight: false,
            ms: 970,
            armed: true,
            focusEndMs: 1000,
            guardMs: GUARD,
            firedForCurrentFocus: false,
        });

        expect(outcome).toEqual({ kind: 'fire' });
    });

    it('advances focus to the containing occasion index, without firing, before the guard window', () => {
        const outcome = resolveShuffleTick({
            occasions: CONTIGUOUS,
            swapInFlight: false,
            ms: 950,
            armed: true,
            focusEndMs: 1000,
            guardMs: GUARD,
            firedForCurrentFocus: false,
        });

        expect(outcome).toEqual({ kind: 'focus', idx: 0 });
    });

    it('does not re-fire for an occasion it already fired for; advances focus instead', () => {
        const outcome = resolveShuffleTick({
            occasions: CONTIGUOUS,
            swapInFlight: false,
            ms: 1400,
            armed: true,
            focusEndMs: 1000,
            guardMs: GUARD,
            firedForCurrentFocus: true,
        });

        expect(outcome).toEqual({ kind: 'focus', idx: 1 });
    });

    it('does not fire when shuffle is disarmed; tracks focus normally', () => {
        const outcome = resolveShuffleTick({
            occasions: CONTIGUOUS,
            swapInFlight: false,
            ms: 1400,
            armed: false,
            focusEndMs: 1000,
            guardMs: GUARD,
            firedForCurrentFocus: false,
        });

        expect(outcome).toEqual({ kind: 'focus', idx: 1 });
    });
});

describe('resolveShuffleTick — cross-source chapter swap window', () => {
    // After a cross-source jump, the shared player points at the NEW chapter (so
    // the playhead time is the new chapter's) while `occasions` still describes the
    // OLD chapter, until its data loads. Every frame in that window must hold:
    // firing again or focusing an old-chapter occasion is the double-fire / wrong-
    // verse-flash bug.
    it('holds (idle) when a frame would otherwise fire, even with the once-per-occasion guard disarmed', () => {
        const outcome = resolveShuffleTick({
            occasions: CONTIGUOUS, // OLD chapter
            swapInFlight: true,
            ms: 1400, // NEW-chapter time
            armed: true,
            focusEndMs: 1000,
            guardMs: GUARD,
            firedForCurrentFocus: false,
        });

        expect(outcome).toEqual({ kind: 'idle' });
    });

    it('holds (idle) instead of focusing an old-chapter occasion mid-swap', () => {
        const outcome = resolveShuffleTick({
            occasions: CONTIGUOUS,
            swapInFlight: true,
            ms: 500, // would normally focus index 0
            armed: false,
            focusEndMs: 1000,
            guardMs: GUARD,
            firedForCurrentFocus: true,
        });

        expect(outcome).toEqual({ kind: 'idle' });
    });

    it('resumes normal firing once the swap completes', () => {
        const outcome = resolveShuffleTick({
            occasions: CONTIGUOUS,
            swapInFlight: false,
            ms: 1400,
            armed: true,
            focusEndMs: 1000,
            guardMs: GUARD,
            firedForCurrentFocus: false,
        });

        expect(outcome).toEqual({ kind: 'fire' });
    });
});

describe('shouldFireShuffle', () => {
    it('treats the guard as a lower bound — an arbitrarily large overshoot still fires', () => {
        expect(
            shouldFireShuffle({
                armed: true,
                ms: 9999,
                focusEndMs: 1000,
                guardMs: GUARD,
                firedForCurrentFocus: false,
            }),
        ).toBe(true);
    });

    it('does not fire when nothing is loaded (null end)', () => {
        expect(
            shouldFireShuffle({
                armed: true,
                ms: 9999,
                focusEndMs: null,
                guardMs: GUARD,
                firedForCurrentFocus: false,
            }),
        ).toBe(false);
    });
});

describe('occasionIndexAt', () => {
    it('follows the verse being read inside a segment that reads two verses', () => {
        const shared: ShuffleTickOccasion[] = [
            { ref: '18:1', startMs: 5000, endMs: 15000, speechStartMs: 5200, speechEndMs: 9000 },
            { ref: '18:2', startMs: 5000, endMs: 15000, speechStartMs: 9000, speechEndMs: 14800 },
        ];

        expect(occasionIndexAt(shared, 6000)).toBe(0);
        expect(occasionIndexAt(shared, 9500)).toBe(1);
        expect(occasionIndexAt(shared, 14900)).toBe(1);
    });

    it('takes a by-surah verse speech span from its words', () => {
        const words = [{ start: 0.2, end: 1.5 }, { start: 1.5, end: 4 }];

        expect(speechSpan({ audio_category: 'by_surah_audio', time_start_ms: 5000, words }))
            .toEqual({ speechStartMs: 5200, speechEndMs: 9000 });
        expect(speechSpan({ audio_category: 'by_ayah_audio', time_start_ms: 0, words })).toEqual({});
    });

    it('returns the index of the occasion whose span contains the playhead', () => {
        expect(occasionIndexAt(CONTIGUOUS, 1500)).toBe(1);
    });

    it('returns the nearest preceding occasion index when the playhead sits in a gap', () => {
        const gapped: ShuffleTickOccasion[] = [
            { ref: '7:1', startMs: 0, endMs: 1000 },
            { ref: '7:2', startMs: 2000, endMs: 3000 },
        ];

        expect(occasionIndexAt(gapped, 1500)).toBe(0);
    });

    it('returns the first occasion index when the playhead precedes all spans', () => {
        const offset: ShuffleTickOccasion[] = [
            { ref: '9:1', startMs: 100, endMs: 1000 },
            { ref: '9:2', startMs: 1000, endMs: 2000 },
        ];

        expect(occasionIndexAt(offset, 50)).toBe(0);
    });

    it('returns -1 when there are no occasions', () => {
        expect(occasionIndexAt([], 100)).toBe(-1);
    });
});
