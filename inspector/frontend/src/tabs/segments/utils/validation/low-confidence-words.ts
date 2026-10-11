/**
 * Low Confidence v2 word marks — which words of a flagged segment to
 * highlight, and how strongly.
 *
 * Severity of a word is how far past its floor it falls: the larger of
 * `fit / WORD_FIT_FLOOR` and `sound_fit / SOUND_FIT_FLOOR` (missing scores
 * ignored). Intensity rescales severity between the reciter's least and most
 * severe flagged words, so the worst words of a delivery are strongest.
 * Words without scores sit at `UNSCORED_INTENSITY`. `lowConfidenceAlpha`
 * maps intensity to the highlight alpha, floored so the mildest word stays
 * visible.
 */

import type { SegValLowConfidenceV2Item, SegValLowConfidenceWord } from '../../../../lib/types/generated/schemas';

/** A word is poor below this fit (log-posterior deficit). */
export const WORD_FIT_FLOOR = -4.0;
/** A sound is poor below this fit (log-posterior deficit). */
export const SOUND_FIT_FLOOR = -10.0;
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

/** Severity of one flagged word, or null when it carries no scores. */
export function wordSeverity(word: SegValLowConfidenceWord): number | null {
    const parts: number[] = [];
    const fit = score(word.fit);
    const soundFit = score(word.sound_fit);
    if (fit !== null) parts.push(fit / WORD_FIT_FLOOR);
    if (soundFit !== null) parts.push(soundFit / SOUND_FIT_FLOOR);
    return parts.length ? Math.max(...parts) : null;
}

/** Marks for every flagged word of the reciter's Low Confidence v2 items. */
export function lowConfidenceWordMarks(items: SegValLowConfidenceV2Item[] | null | undefined): LowConfidenceWordMarks {
    const severities = (items ?? []).flatMap((item) => (item.words ?? []).map(wordSeverity))
        .filter((s): s is number => s !== null);
    const min = severities.length ? Math.min(...severities) : 0;
    const span = severities.length ? Math.max(...severities) - min : 0;
    const marks: LowConfidenceWordMarks = new Map();
    for (const item of items ?? []) {
        if (!item.segment_uid || !item.words?.length) continue;
        const byRef = new Map<string, LowConfidenceWordMark>();
        for (const word of item.words) {
            const severity = wordSeverity(word);
            const intensity = severity === null ? UNSCORED_INTENSITY : span > 0 ? (severity - min) / span : 1;
            byRef.set(word.ref, { intensity, fit: score(word.fit), soundFit: score(word.sound_fit) });
        }
        marks.set(item.segment_uid, byRef);
    }
    return marks;
}

/** Highlight alpha for a mark intensity in [0, 1]. */
export function lowConfidenceAlpha(intensity: number): number {
    return MIN_HIGHLIGHT_ALPHA + (1 - MIN_HIGHLIGHT_ALPHA) * Math.min(1, Math.max(0, intensity));
}
