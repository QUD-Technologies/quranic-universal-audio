/**
 * Pure per-frame decision for the Timestamps tab's auto-random ("shuffle")
 * boundary, split out of `TimestampsTab`'s rAF tick so the contiguous-seam
 * overshoot race is unit-testable without rAF / audio / store plumbing.
 *
 * The tab focuses one OCCASION at a time (a contiguous recitation of a verse; a
 * re-recited verse yields several occasions). Occasions recited continuously are
 * contiguous (one's end == the next's start), so the playhead crosses a seam with
 * no gap. Each frame the tick must decide whether to FIRE the shuffle jump (the
 * auditioned occasion just ended) BEFORE it advances the visual focus — otherwise
 * a frame that overshoots the seam (heavy repaint right after a jump starves rAF,
 * most visible when the just-loaded occasion is short) would re-base the boundary
 * onto the NEXT occasion and leak it in full. Resolving the fire decision first,
 * against the auditioned occasion's captured end, bounds the leak to a single frame.
 *
 * A cross-source jump also opens a multi-frame window where the shared player
 * already points at the NEW chapter (so the playhead time is the new chapter's)
 * but the occasion list still describes the OLD chapter, until its data loads.
 * `resolveShuffleTick` reports `idle` for that window so the caller freezes both
 * focus and firing instead of interpreting the new time against stale occasions
 * (which would focus a wrong old occasion and double-fire the jump).
 */

export interface ShuffleTickOccasion {
    ref: string;
    startMs: number;
    endMs: number;
    /** The verse's own words (chapter-absolute ms): narrower than the segment span
     *  when one segment reads several verses. */
    speechStartMs?: number;
    speechEndMs?: number;
}

/** A by-surah verse's own speech span (chapter-absolute ms) from its words,
 *  which are anchored at `time_start_ms`; empty for by-ayah audio or no words. */
export function speechSpan(data: {
    audio_category: string;
    time_start_ms: number;
    words: { start: number; end: number }[];
}): { speechStartMs?: number; speechEndMs?: number } {
    if (data.audio_category !== 'by_surah_audio' || data.words.length === 0) return {};
    return {
        speechStartMs: data.time_start_ms + Math.min(...data.words.map((w) => w.start)) * 1000,
        speechEndMs: data.time_start_ms + Math.max(...data.words.map((w) => w.end)) * 1000,
    };
}

export type ShuffleTickOutcome =
    | { kind: 'idle' }
    | { kind: 'fire' }
    | { kind: 'focus'; idx: number };

export interface ShuffleFireArgs {
    /** Shuffle is engaged on the active tab and not overridden by a region loop. */
    armed: boolean;
    /** Playhead, chapter-absolute ms. */
    ms: number;
    /** End of the occasion currently being auditioned (chapter-absolute ms), or
     *  null when nothing is loaded. */
    focusEndMs: number | null;
    /** Fire this many ms before the exact end so the jump lands without a beat of
     *  the next occasion leaking in. */
    guardMs: number;
    /** True once the jump has already fired for the occasion in focus
     *  (once-per-occasion guard). */
    firedForCurrentFocus: boolean;
}

/** The end-of-occasion fire predicate: armed, the auditioned occasion's end is
 *  known, it hasn't already fired for that occasion, and the playhead has reached
 *  within `guardMs` of — or PAST — its end. The `>=` makes the guard a LOWER
 *  bound, so an overshoot (a skipped frame) still fires; that is the property the
 *  leak bug violated by re-measuring against the next occasion after focus had
 *  advanced. */
export function shouldFireShuffle(args: ShuffleFireArgs): boolean {
    const { armed, ms, focusEndMs, guardMs, firedForCurrentFocus } = args;
    if (!armed || focusEndMs === null || firedForCurrentFocus) return false;
    return ms >= focusEndMs - guardMs;
}

/** The index of the occasion to focus for playhead `ms`: the one whose span
 *  contains it, else the nearest preceding, else the first. Returns -1 when there
 *  are no occasions. */
export function occasionIndexAt(occasions: ShuffleTickOccasion[], ms: number): number {
    if (occasions.length === 0) return -1;
    let hit = 0;
    for (let i = 0; i < occasions.length; i++) {
        const o = occasions[i]!;
        const start = o.speechStartMs ?? o.startMs;
        if (ms >= start && ms < (o.speechEndMs ?? o.endMs)) return i;
        if (start <= ms) hit = i; // nearest preceding
    }
    return hit;
}

/** Combined tick decision: hold everything while a chapter is mid-swap, else
 *  fire the shuffle jump if the auditioned occasion ended, otherwise report which
 *  occasion index to focus. The swap check comes first because `occasions` and
 *  the playhead belong to different chapters during the swap window, so neither
 *  firing nor focusing can be trusted. Firing is resolved BEFORE focus so an
 *  overshoot past a contiguous seam still measures against the auditioned
 *  occasion, never the next one. */
export function resolveShuffleTick(
    args: ShuffleFireArgs & { occasions: ShuffleTickOccasion[]; swapInFlight: boolean },
): ShuffleTickOutcome {
    if (args.swapInFlight) return { kind: 'idle' };
    if (shouldFireShuffle(args)) return { kind: 'fire' };
    return { kind: 'focus', idx: occasionIndexAt(args.occasions, args.ms) };
}
