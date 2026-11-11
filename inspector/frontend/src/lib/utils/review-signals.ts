/**
 * Reciter-picker review signals — open Low Confidence (under the default
 * threshold) and Low Confidence v2 items per delivery, from
 * `GET /api/seg/review-signals`. Labels resolve at call time so they
 * re-translate on a locale switch.
 */
import * as m from '$lib/paraglide/messages';

import type { SegReviewSignal } from '../types/generated/schemas';

export const REVIEW_LOW = 'low_confidence';
export const REVIEW_LOW_V2 = 'low_confidence_v2';
export const REVIEW_NONE = 'none';
/** Filter-rail option order. */
export const REVIEW_TAGS: readonly string[] = [REVIEW_LOW, REVIEW_LOW_V2, REVIEW_NONE];

/** Rail tags for a delivery: one per signal with open items, else `none`. */
export function reviewTags(signal: SegReviewSignal | undefined): string[] {
    const tags: string[] = [];
    if ((signal?.low_confidence ?? 0) > 0) tags.push(REVIEW_LOW);
    if ((signal?.low_confidence_v2 ?? 0) > 0) tags.push(REVIEW_LOW_V2);
    return tags.length > 0 ? tags : [REVIEW_NONE];
}

/** Rail pill label for a tag. */
export function reviewTagLabel(tag: string): string {
    if (tag === REVIEW_LOW) return m.common_review_low_confidence();
    if (tag === REVIEW_LOW_V2) return m.common_review_low_confidence_v2();
    return m.common_review_none();
}
