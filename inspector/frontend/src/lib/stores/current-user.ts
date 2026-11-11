/**
 * Current-user identity store.
 *
 * Loaded on app boot from `/api/me`. Replaced after sign-in/sign-out and
 * after any state-mutating claim action that touches the blocking claim.
 *
 * Shape is stable for anonymous (all fields null) so consumers can read
 * the same schema regardless of auth state.
 */

import { derived, get, writable } from 'svelte/store';

export type Role = 'contributor' | 'maintainer' | 'owner' | null;

export interface CurrentUser {
    login: string | null;
    hf_user_id: string | null;
    role: Role;
    /**
     * The *blocking* claim slug — the open, not-yet-marked-ready claim that
     * prevents the user from claiming another (null once they mark it ready or
     * hold none). Owners are exempt from the one-at-a-time rule. This is the
     * field every one-claim gate keys off.
     */
    active_claim: string | null;
    /** All slugs currently under_review and assigned to this user (a marked-ready
     *  reciter stays here). Owners may hold multiple. */
    active_claims: string[];
    /**
     * True when the backend is running with the dev-mode auth bypass
     * (`INSPECTOR_DEV_MODE=1`). Only ever true locally — never on the
     * deployed HF Space. Toggles the in-app role switcher UI.
     */
    dev_mode: boolean;
    /**
     * Resolved capability ids the caller currently holds (registry defaults ⊕
     * owner overrides), recomputed fresh per request by the backend. Drives
     * capability-aware UI gating via `lib/stores/capabilities.ts`. Anonymous
     * still holds the anon-eligible view capabilities the owner left on.
     */
    capabilities: string[];
    /**
     * Validation-accordion guide *view keys* this user has opened (see
     * `tabs/segments/guides/registry.ts`). Drives the cyan unread border on
     * each `?` and the first-edit onboarding gate. Global per user, durable,
     * write-once. Empty for anonymous.
     */
    guides_read: string[];
    /**
     * Count of active, unseen per-user notifications — drives the "My
     * Notifications" rail badge on first load (the rail itself refetches and
     * marks-seen on open). 0 for anonymous.
     */
    notifications_unread: number;
}

const _ANON: CurrentUser = {
    login: null,
    hf_user_id: null,
    role: null,
    active_claim: null,
    active_claims: [],
    dev_mode: false,
    capabilities: [],
    guides_read: [],
    notifications_unread: 0,
};

export const currentUser = writable<CurrentUser>(_ANON);

/** True iff the user is signed in (has a verified HF identity). */
export function isSignedIn(u: CurrentUser): boolean {
    return u.hf_user_id !== null;
}

/**
 * Global role-tier derived stores driven by ``currentUser`` (not the
 * reciter-scoped ``editingMode`` store in ``editing-mode.ts``). These are
 * the right hooks for dashboard-level UI gating: admin notification rail,
 * owner-only delete affordances, etc.
 *
 * - ``isAdmin`` — maintainer OR owner. Mirrors ``services/permissions.is_maintainer``.
 * - ``isOwner`` — owner only. Mirrors ``services/permissions.is_owner``.
 */
export const isAdmin = derived(
    currentUser,
    (u) => u.role === 'maintainer' || u.role === 'owner',
);

export const isOwner = derived(currentUser, (u) => u.role === 'owner');

/** Backoff before each retry after a transient `/api/me` failure. After the
 *  last one we stop; the next tab-focus re-check tries again. */
export const ME_RETRY_DELAYS_MS = [2_000, 8_000, 30_000] as const;

let _retryTimer: ReturnType<typeof setTimeout> | null = null;

/** The `/api/me` body when the server answered authoritatively, else `null`.
 *  Authoritative = 2xx + JSON + an object carrying `hf_user_id` (anonymous is a
 *  200 with `hf_user_id: null`, never a 401). A paused/booting Space's HTML
 *  page, a Cloudflare error page, a 5xx, a network error or a bad body are all
 *  transient — they say nothing about the session. */
async function _fetchMe(): Promise<CurrentUser | null> {
    try {
        const res = await fetch('/api/me', { credentials: 'same-origin' });
        if (!res.ok || !(res.headers.get('content-type') ?? '').includes('application/json')) {
            return null;
        }
        const body: unknown = await res.json();
        if (body === null || typeof body !== 'object' || !('hf_user_id' in body)) return null;
        return body as CurrentUser;
    } catch {
        return null;
    }
}

function _clearRetry(): void {
    if (_retryTimer !== null) {
        clearTimeout(_retryTimer);
        _retryTimer = null;
    }
}

async function _load(attempt: number): Promise<CurrentUser> {
    _clearRetry();
    const me = await _fetchMe();
    if (me !== null) {
        currentUser.set(me);
        return me;
    }
    // Transient failure: keep whoever we had (a woken laptop's first focus
    // fetch, or one landing during a Space restart, must not sign the UI out).
    if (attempt < ME_RETRY_DELAYS_MS.length) {
        _retryTimer = setTimeout(() => void _load(attempt + 1), ME_RETRY_DELAYS_MS[attempt]);
    }
    return get(currentUser);
}

/** Fetch /api/me and replace the store — but only on an authoritative answer.
 *  On a transient failure the current value is kept and a bounded retry is
 *  scheduled (a newer call supersedes it). Returns the store's value. */
export function loadCurrentUser(): Promise<CurrentUser> {
    return _load(0);
}

/** Force the store back to anonymous. Use after explicit logout. */
export function resetCurrentUser(): void {
    _clearRetry();
    currentUser.set(_ANON);
}

/**
 * Optimistically record a guide *view key* as read (after a successful
 * `POST /api/guides/viewed`). Idempotent + signed-in only — anonymous has no
 * server-side row, so we never mutate the anon store. Pass the already-collapsed
 * view key (use `guideViewKey` from the guides registry). Updating the store
 * clears the unread border and may lift the edit gate without a `/api/me`
 * round-trip.
 */
export function markGuideReadLocally(viewKey: string): void {
    currentUser.update((u) => {
        if (u.hf_user_id === null || u.guides_read.includes(viewKey)) return u;
        return { ...u, guides_read: [...u.guides_read, viewKey] };
    });
}
