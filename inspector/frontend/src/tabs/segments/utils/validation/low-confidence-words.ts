/**
 * Low Confidence v2 word marks — which words of a flagged segment to
 * highlight, and how strongly.
 *
 * Intensity is the word's server `severity`: its floor ratio rescaled across
 * every scored word of the reciter's whole sidecar (see `load_probe_v2`), so
 * ignoring or resolving items never shifts it. Words without scores sit at
 * `UNSCORED_INTENSITY`. `lowConfidenceAlpha` maps intensity to the highlight
 * alpha, floored so the mildest word stays visible.
 */

import type { SegValLowConfidenceV2Item } from '../../../../lib/types/generated/schemas';

/** Intensity of a flagged word the sidecar carries no scores for. */
export const UNSCORED_INTENSITY = 0.5;
/** Highlight alpha of the reciter's least severe flagged word. */
export const MIN_HIGHLIGHT_ALPHA = 0.3;

export interface LowConfidenceWordMark {
    /** 0 = least severe flagged word of the reciter, 1 = most severe. */
    intensity: number;
    fit: number | null;
    soundFit: number | null;
}

/** `segment_uid -> word ref -> mark`. */
export type LowConfidenceWordMarks = Map<string, Map<string, LowConfidenceWordMark>>;

function score(value: number | null | undefined): number | null {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Marks for the flagged words of the given Low Confidence v2 items. */
export function lowConfidenceWordMarks(items: SegValLowConfidenceV2Item[] | null | undefined): LowConfidenceWordMarks {
    const marks: LowConfidenceWordMarks = new Map();
    for (const item of items ?? []) {
        if (!item.segment_uid || !item.words?.length) continue;
        const byRef = new Map<string, LowConfidenceWordMark>();
        for (const word of item.words) {
            byRef.set(word.ref, {
                intensity: score(word.severity) ?? UNSCORED_INTENSITY,
                fit: score(word.fit),
                soundFit: score(word.sound_fit),
            });
        }
        marks.set(item.segment_uid, byRef);
    }
    return marks;
}

/** Highlight alpha for a mark intensity in [0, 1]. */
export function lowConfidenceAlpha(intensity: number): number {
    return MIN_HIGHLIGHT_ALPHA + (1 - MIN_HIGHLIGHT_ALPHA) * Math.min(1, Math.max(0, intensity));
}
