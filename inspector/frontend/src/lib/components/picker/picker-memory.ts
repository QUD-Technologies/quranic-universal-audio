/**
 * Remembers the combination picker's bucket tab, rail filters and sort for the
 * rest of the page session, so reopening the modal lands on the same view.
 * Search text is deliberately not remembered.
 */
import type { PublicBucket } from '../../types/public-bucket';
import type { PickerSort } from './picker-sort';

export interface PickerMemory {
    bucket: PublicBucket | null;
    filters: Record<string, string[]>;
    sort: PickerSort;
}

let remembered: PickerMemory | null = null;

export function recallPickerState(): PickerMemory | null {
    return remembered;
}

export function rememberPickerState(
    bucket: PublicBucket | null,
    filters: Record<string, Set<string>>,
    sort: PickerSort,
): void {
    remembered = {
        bucket,
        filters: Object.fromEntries(Object.entries(filters).map(([k, v]) => [k, [...v]])),
        sort,
    };
}
