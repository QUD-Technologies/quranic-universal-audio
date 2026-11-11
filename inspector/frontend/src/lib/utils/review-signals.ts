/**
 * Reciter-picker review signal — a delivery's open Low Confidence items
 * (under the default threshold, plus open Low Confidence v2 items), from
 * `GET /api/seg/review-signals`. One combined signal: the picker shows a
 * single pill and a single filter option for both. Labels resolve at call
 * time so they re-translate on a locale switch.
 */
import * as m from '$lib/paraglide/messages';

import type { SegReviewSignal } from '../types/generated/schemas';

export const REVIEW_LOW = 'low_confidence';
export const REVIEW_NONE = 'none';
/** Filter-rail option order. */
export const REVIEW_TAGS: readonly string[] = [REVIEW_LOW, REVIEW_NONE];

/** Open Low Confidence items of both kinds. */
export function lowConfidenceTotal(signal: SegReviewSignal | undefined): number {
    return (signal?.low_confidence ?? 0) + (signal?.low_confidence_v2 ?? 0);
}

/** Rail tag for a delivery. */
export function reviewTags(signal: SegReviewSignal | undefined): string[] {
    return [lowConfidenceTotal(signal) > 0 ? REVIEW_LOW : REVIEW_NONE];
}

/** Rail pill label for a tag. */
export function reviewTagLabel(tag: string): string {
    return tag === REVIEW_LOW ? m.common_review_low_confidence() : m.common_review_none();
}
