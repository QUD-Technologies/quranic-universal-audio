/**
 * Combination-picker row ordering + the per-row "aligned" date.
 *
 * `default` keeps the catalog order. The aligned sorts order by
 * `delivery.aligned_at` (when alignment last finished); rows never aligned
 * here (no date) always sink to the end, whichever direction is picked. The
 * review sorts put the most open Low Confidence items (both kinds) or the most
 * open flags first; rows with none keep their catalog order at the end.
 */
import { formatShortDate } from '$lib/i18n/format';

import type { PublicDelivery, SegReviewSignal } from '../../types/generated/schemas';
import { lowConfidenceTotal } from '../../utils/review-signals';

export type PickerSort = 'default' | 'aligned_desc' | 'aligned_asc' | 'low_conf_desc' | 'flags_desc';

type Row = { delivery: Pick<PublicDelivery, 'aligned_at' | 'slug'> };

function byAligned(a: Row, b: Row, dir: number): number {
    const x = a.delivery.aligned_at ?? null;
    const y = b.delivery.aligned_at ?? null;
    if (x === y) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return x < y ? -dir : dir;
}

/** Stable sort of picker rows; returns a new array. `signalOf` gives a row's
 *  review counts (absent = nothing open). */
export function sortPickerRows<T extends Row>(
    rows: readonly T[],
    sort: PickerSort,
    signalOf: (slug: string) => SegReviewSignal | undefined = () => undefined,
): T[] {
    if (sort === 'default') return [...rows];
    if (sort === 'aligned_desc' || sort === 'aligned_asc') {
        const dir = sort === 'aligned_desc' ? -1 : 1;
        return [...rows].sort((a, b) => byAligned(a, b, dir));
    }
    const count = (r: T): number => {
        const s = signalOf(r.delivery.slug);
        return sort === 'flags_desc' ? (s?.flagged ?? 0) : lowConfidenceTotal(s);
    };
    return [...rows].sort((a, b) => count(b) - count(a));
}

/** The row's aligned date as a short locale date; '' when never aligned. */
export function alignedDateLabel(iso: string | null | undefined, locale?: string): string {
    return formatShortDate(iso, locale);
}
