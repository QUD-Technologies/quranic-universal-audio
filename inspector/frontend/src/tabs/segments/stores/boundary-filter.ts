/**
 * Cross-verse boundary filter — which of Unset · Wasl · Waqf the accordion
 * shows. Multi-select; default = Unset only (the work queue). Persisted
 * globally like `valSortPrefs`.
 */

import { writable } from 'svelte/store';

import { LS_KEYS } from '../../../lib/utils/constants';
import { BOUNDARY_STATES, type BoundaryState } from '../utils/validation/boundary-state';

const KEY = LS_KEYS.SEG_VAL_BOUNDARY;
const DEFAULT: readonly BoundaryState[] = ['unset'];

function load(): Set<BoundaryState> {
    if (typeof localStorage === 'undefined') return new Set(DEFAULT);
    try {
        const raw = localStorage.getItem(KEY);
        if (!raw) return new Set(DEFAULT);
        const parsed = JSON.parse(raw) as unknown;
        if (!Array.isArray(parsed)) return new Set(DEFAULT);
        const valid = parsed.filter((v): v is BoundaryState => BOUNDARY_STATES.includes(v as BoundaryState));
        return new Set(valid.length ? valid : DEFAULT);
    } catch {
        return new Set(DEFAULT);
    }
}

function persist(s: Set<BoundaryState>): void {
    if (typeof localStorage === 'undefined') return;
    try {
        localStorage.setItem(KEY, JSON.stringify([...s]));
    } catch {
        /* quota / private-mode — non-fatal */
    }
}

export const valBoundaryFilter = writable<Set<BoundaryState>>(load());
valBoundaryFilter.subscribe(persist);

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
