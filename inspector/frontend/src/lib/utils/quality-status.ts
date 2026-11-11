/**
 * Recitation quality status — the maintainer's manual rating of a delivery
 * (`delivery_states.quality_status`). Labels + hints resolve at call time so
 * they re-translate on a locale switch.
 */
import * as m from '$lib/paraglide/messages';

import type { QualityStatus } from '../types/generated/schemas';

/** Best → worst; also the filter-rail option order. */
export const QUALITY_ORDER: readonly QualityStatus[] = ['clean', 'almost_clean', 'needs_work'];

/** Rail tag for a delivery that was never rated. */
export const QUALITY_UNRATED = 'unrated';

export function qualityLabel(status: QualityStatus): string {
    if (status === 'clean') return m.common_quality_clean();
    if (status === 'almost_clean') return m.common_quality_almost_clean();
    return m.common_quality_needs_work();
}

export function qualityHint(status: QualityStatus): string {
    if (status === 'clean') return m.common_quality_clean_hint();
    if (status === 'almost_clean') return m.common_quality_almost_clean_hint();
    return m.common_quality_needs_work_hint();
}

/** Rail tag for a delivery's (possibly absent) status. */
export function qualityTag(status: QualityStatus | null | undefined): string {
    return status ?? QUALITY_UNRATED;
}

/** Rail pill label for a tag (a status or `unrated`). */
export function qualityTagLabel(tag: string): string {
    return (QUALITY_ORDER as readonly string[]).includes(tag)
        ? qualityLabel(tag as QualityStatus)
        : m.common_quality_unrated();
}
