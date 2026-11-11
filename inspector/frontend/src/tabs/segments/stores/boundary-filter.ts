/**
 * Boundary filter — which of Unset · Wasl · Waqf the Cross-verse and Low
 * Confidence Waqf accordions show. Multi-select; every open of either
 * accordion starts again from the default, Unset only (the work queue).
 */

import { writable } from 'svelte/store';

import type { BoundaryState } from '../utils/validation/boundary-state';

const DEFAULT: readonly BoundaryState[] = ['unset'];

export const valBoundaryFilter = writable<Set<BoundaryState>>(new Set(DEFAULT));

/** Back to the default selection — called whenever a boundary accordion opens. */
export function resetBoundaryFilter(): void {
    valBoundaryFilter.set(new Set(DEFAULT));
}

/** Toggle one state; the last remaining state cannot be switched off. */
export function toggleBoundaryState(state: BoundaryState): void {
    valBoundaryFilter.update((cur) => {
        const next = new Set(cur);
        if (next.has(state)) {
            if (next.size === 1) return cur;
            next.delete(state);
        } else {
            next.add(state);
        }
        return next;
    });
}
