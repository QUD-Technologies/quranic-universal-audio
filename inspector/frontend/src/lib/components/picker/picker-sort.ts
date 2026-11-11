/**
 * Combination-picker row ordering + the per-row "aligned" date.
 *
 * `default` keeps the catalog order. The aligned sorts order by
 * `delivery.aligned_at` (when alignment last finished); rows never aligned
 * here (no date) always sink to the end, whichever direction is picked.
 */
import { formatShortDate } from '$lib/i18n/format';

import type { PublicDelivery } from '../../types/generated/schemas';

export type PickerSort = 'default' | 'aligned_desc' | 'aligned_asc';

/** Stable sort of rows by their delivery's `aligned_at`; returns a new array. */
export function sortByAligned<T extends { delivery: Pick<PublicDelivery, 'aligned_at'> }>(
    rows: readonly T[],
    sort: PickerSort,
): T[] {
    if (sort === 'default') return [...rows];
    const dir = sort === 'aligned_desc' ? -1 : 1;
    return [...rows].sort((a, b) => {
        const x = a.delivery.aligned_at ?? null;
        const y = b.delivery.aligned_at ?? null;
        if (x === y) return 0;
        if (x === null) return 1;
        if (y === null) return -1;
        return x < y ? -dir : dir;
    });
}

/** The row's aligned date as a short locale date; '' when never aligned. */
export function alignedDateLabel(iso: string | null | undefined, locale?: string): string {
    return formatShortDate(iso, locale);
}
