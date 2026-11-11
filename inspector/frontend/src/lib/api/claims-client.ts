/**
 * Claim flow client. Wraps the claim-family POST endpoints and translates the
 * backend's status code envelope into UI side effects:
 *
 *   - 200 → resolves with the new row.
 *   - 401 → open SignInModal with session-expired context; rejects.
 *   - 403 → toast with the error body; rejects.
 *   - 409 → toast "Unclaim {other} first" (or generic conflict); rejects.
 *   - other → generic error toast; rejects.
 *
 * Callers can ``await`` and react on success; failure cases have already
 * surfaced UX (toast/modal) so the caller usually doesn't need to do more.
 */

import * as m from '$lib/paraglide/messages';
import { friendlyError } from '../errors/friendly';
import { SIGN_IN_MESSAGES } from '../sign-in-messages';
import { loadCurrentUser } from '../stores/current-user';
import { openSignInModal } from '../stores/sign-in-modal';
import { pushToast } from '../stores/toast';
import type { MarkReadyRequest, QualityStatus } from '../types/generated/schemas';
import type { ReciterRow } from './reciter-task';

type RouteName = 'claim' | 'release' | 'mark-ready' | 'unmark-ready' | 'quality-status';

interface ErrorBody {
    error?: string;
    existing_claim?: string;
    /** Display name for the existing claim's reciter; falls back to slug only
     * as a last resort (e.g. catalog hasn't loaded server-side). Server-side
     * source: `services/catalog.display_name`. */
    existing_claim_name?: string | null;
    /** Display name for the reciter the user was trying to claim. */
    target_name?: string | null;
    /** Structured server-side detail for a 400. The mark-ready route uses this
     * to carry the offending category counts or unchecked checklist keys; the
     * caller surfaces a richer message instead of the generic ``error``. */
    details?: Record<string, unknown> | null;
}

async function _post(
    route: RouteName,
    slug: string,
    body?: Record<string, unknown>,
): Promise<ReciterRow> {
    const res = await fetch(`/api/${route}/${encodeURIComponent(slug)}`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.ok) {
        const row = (await res.json()) as ReciterRow;
        // The active_claim field in /api/me derives from state — refresh
        // the local store so the rest of the UI sees the right value.
        void loadCurrentUser();
        return row;
    }
    let errBody: ErrorBody = {};
    try {
        errBody = (await res.json()) as ErrorBody;
    } catch {
        /* swallow */
    }

    if (res.status === 401) {
        openSignInModal(null, SIGN_IN_MESSAGES.claimExpired);
    } else if (res.status === 409 && errBody.existing_claim) {
        const existing = errBody.existing_claim_name || errBody.existing_claim;
        const target = errBody.target_name || slug;
        pushToast({
            kind: 'warn',
            text: m.common_claim_unclaim_first_toast({ existing, target }),
            ttl: 6000,
        });
    } else {
        pushToast({
            kind: 'error',
            text: friendlyError(errBody, res.status),
            ttl: 5000,
        });
    }
    throw new Error(errBody.error || `${route} failed: ${res.status}`);
}

export const claim = (slug: string) => _post('claim', slug);
export const release = (slug: string) => _post('release', slug);
export const markReady = (slug: string, payload: MarkReadyRequest) =>
    _post('mark-ready', slug, payload as unknown as Record<string, unknown>);
/**
 * Owner-bypass mark-ready: POST with an empty body. The server checks
 * the caller holds `claim.mark_ready_skip_gates` and, if so, skips the
 * checklist validation + blocking-counts gate, stamping `bypass_used`
 * onto the persisted submission. Call this only when the corresponding
 * predicate `can_skip_mark_ready_gates` is true; otherwise the server
 * will reject with a 400 (Pydantic validation failure on empty body).
 */
export const markReadyBypass = (slug: string) => _post('mark-ready', slug, {});
export const unmarkReady = (slug: string) => _post('unmark-ready', slug);
/** Set (a status) or clear (`null`) the delivery's recitation quality rating. */
export const setQualityStatus = (slug: string, status: QualityStatus | null) =>
    _post('quality-status', slug, { quality_status: status });
