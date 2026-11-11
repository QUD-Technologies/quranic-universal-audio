/**
 * Reciter-task readable store: polls `/api/reciter-task/<slug>` on a
 * 30-second cadence while a subscriber is attached.
 *
 * The polling lifecycle is tied to the store's subscribe/unsubscribe count
 * (Svelte's `readable` start/stop semantics):
 * - First subscriber attaches → immediate fetch + start interval.
 * - Last subscriber detaches → clear interval (idle).
 *
 * One store instance per slug. The cache lives module-level so swapping
 * back to a previously-viewed reciter resumes from its last value while a
 * fresh fetch runs.
 */

import { type Readable,readable } from 'svelte/store';

import type { QualityStatus } from '../types/generated/schemas';
import { fetchJsonOrNull } from './index';

export type ReciterTaskState =
    | 'catalogued'
    | 'awaiting_alignment'
    | 'awaiting_review'
    | 'under_review'
    | 'released';

export type Visibility = 'public' | 'discarded';

export interface ReciterRow {
    slug: string;
    /**
     * Display name resolved server-side from the catalog
     * (`services/catalog.display_name`). Null when the slug isn't yet in the
     * catalog; user-facing surfaces fall back to slug only as a last resort.
     * Never expose slug directly to users — see `inspector/CLAUDE.md`.
     */
    name: string | null;
    state: ReciterTaskState;
    state_since: string;
    assignee_hf_id: string | null;
    assignee_login: string | null;
    assignee_since: string | null;
    marked_ready: boolean;
    visibility: Visibility;
    /** Maintainer's manual recitation quality rating; null = not rated. */
    quality_status?: QualityStatus | null;
    // Other fields exist on the backend row but the frontend doesn't need them.
    [k: string]: unknown;
}

export interface Predicates {
    can_claim: boolean;
    can_edit: boolean;
    can_edit_as_admin: boolean;
    can_edit_as_owner: boolean;
    can_mark_ready: boolean;
    /**
     * Reviewer holds `claim.mark_ready_skip_gates`. When true, clicking
     * Mark Ready POSTs directly with an empty body and skips the modal
     * (no checklist, no blocking-counts gate). Owners hold this by default;
     * other tiers only if the owner grants it via the Permissions tab.
     */
    can_skip_mark_ready_gates: boolean;
    can_unmark_ready: boolean;
    can_release: boolean;
    /** Holds `reciter.set_quality_status` and the row is published or marked ready. */
    can_set_quality_status: boolean;
    /** Holds `reciter.set_quality_status` and a rating exists (any state). */
    can_clear_quality_status: boolean;
}

export interface ReciterTask {
    row: ReciterRow;
    predicates: Predicates;
}

export const POLL_INTERVAL_MS = 30_000;

// Per-slug entry: the public Readable + a private push hook the
// ``refreshReciterTask`` helper uses to broadcast fresh data to live
// subscribers. The push hook is null while no one is subscribed (the
// readable's start callback hasn't run yet) — refreshReciterTask falls
// back to fire-and-forget in that case.
interface CacheEntry {
    store: Readable<ReciterTask | null>;
    push: ((v: ReciterTask | null) => void) | null;
}

const _stores = new Map<string, CacheEntry>();

export function getReciterTaskStore(slug: string): Readable<ReciterTask | null> {
    const existing = _stores.get(slug);
    if (existing) return existing.store;

    const entry: CacheEntry = { store: null as never, push: null };

    entry.store = readable<ReciterTask | null>(null, (set) => {
        let cancelled = false;
        entry.push = set;

        async function tick() {
            const data = await fetchJsonOrNull<ReciterTask>(
                `/api/reciter-task/${encodeURIComponent(slug)}`,
            );
            if (!cancelled) set(data);
        }

        void tick();
        const handle = setInterval(() => void tick(), POLL_INTERVAL_MS);

        return () => {
            cancelled = true;
            clearInterval(handle);
            entry.push = null;
            // Drop the cached store when no one's listening so a future
            // subscribe on the same slug runs a fresh initial fetch.
            _stores.delete(slug);
        };
    });

    _stores.set(slug, entry);
    return entry.store;
}

/**
 * Force a one-shot refresh, AND push the fresh value to any live
 * subscribers on the same slug. Use after a state-mutating action from
 * anywhere in the app — including cross-tab surfaces like the Admin
 * Reviews popover, where the segments-tab's task store would otherwise
 * stay stale until its next 30 s poll tick fires.
 */
export async function refreshReciterTask(slug: string): Promise<ReciterTask | null> {
    const data = await fetchJsonOrNull<ReciterTask>(
        `/api/reciter-task/${encodeURIComponent(slug)}`,
    );
    // Broadcast to live subscribers (if any) — otherwise this is a no-op
    // and the caller just consumes the returned value.
    const entry = _stores.get(slug);
    if (entry?.push) entry.push(data);
    return data;
}
