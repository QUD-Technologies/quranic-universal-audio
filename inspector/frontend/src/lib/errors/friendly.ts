/**
 * Translates a backend error envelope into plain, user-facing copy.
 *
 * The backend returns `{ error, code?, context?, details? }`. `error` is
 * developer-grammar prose we must NOT show a user (it leaks internal state
 * tokens / capability IDs). Instead we map the stable `code` to friendly copy,
 * falling back to a status-based message, then a generic one. The raw
 * `body.error` is kept only for `console.error` / telemetry — never surfaced.
 *
 * Keep the codes here in lockstep with `services/errors.py::Codes`.
 */

import * as m from '$lib/paraglide/messages';

export interface ApiErrorBody {
    error?: string;
    code?: string;
    context?: Record<string, unknown> | null;
    details?: Record<string, unknown> | null;
    // Claim-conflict (409) carries its own fields; handled by claims-client,
    // intentionally NOT mapped here.
    existing_claim?: string;
}

type CopyEntry = (() => string) | ((ctx: Record<string, unknown> | null | undefined) => string);

function stateLabel(ctx: Record<string, unknown> | null | undefined): string | null {
    const label = ctx?.state_label;
    return typeof label === 'string' && label ? label : null;
}

const CODE_COPY: Record<string, CopyEntry> = {
    // Auth / permission
    AUTH_REQUIRED: m.errors_auth_required,
    FORBIDDEN_CAPABILITY: m.errors_forbidden_capability,
    NOT_AUTHORIZED: m.errors_not_authorized,

    // Edit-lock gates
    NOT_EDITABLE_STATE: (ctx) => {
        const s = stateLabel(ctx);
        return s ? m.errors_not_editable_state_with_label({ state_label: s }) : m.errors_not_editable_state();
    },
    MARKED_READY_FROZEN: m.errors_marked_ready_frozen,
    VISIBILITY_BLOCKED: m.errors_visibility_blocked,
    NOT_CLAIM_HOLDER: m.errors_not_claim_holder,

    // State machine
    STATE_PRECONDITION: (ctx) => {
        const s = stateLabel(ctx);
        return s ? m.errors_state_precondition_with_label({ state_label: s }) : m.errors_state_precondition();
    },
    RELEASE_BLOCKED_MARKED_READY: m.errors_release_blocked_marked_ready,
    REASON_REQUIRED: (ctx) => {
        const n = ctx?.min_chars;
        return typeof n === 'number'
            ? m.errors_reason_required_with_min({ min_chars: n })
            : m.errors_reason_required();
    },

    // Mark-ready family
    MARK_READY_CHECKLIST: m.errors_mark_ready_checklist,
    MARK_READY_BLOCKING_COUNTS: m.errors_mark_ready_blocking_counts,
    MARK_READY_PAYLOAD: m.errors_mark_ready_payload,
    MARK_READY_NO_SEGMENTS: m.errors_mark_ready_no_segments,
    QUALITY_STATUS_PAYLOAD: m.errors_quality_status_payload,

    // Admin / structural
    LAST_OWNER: m.errors_last_owner,
    UNKNOWN_RECITER: m.errors_unknown_reciter,

    // Storage
    READ_ONLY: m.errors_read_only,

    // Editions (multi-riwayah)
    RIWAYAH_MISMATCH: m.errors_riwayah_mismatch,
    UNSUPPORTED_RIWAYAH: m.errors_unsupported_riwayah,
    EDITIONS_UNAVAILABLE: m.errors_editions_unavailable,
    REF_NOT_IN_EDITION: m.errors_ref_not_in_edition,
};

const STATUS_FALLBACK: Record<number, () => string> = {
    401: m.errors_status_401,
    403: m.errors_status_403,
    404: m.errors_status_404,
    409: m.errors_status_409,
    503: m.errors_status_503,
    500: m.errors_status_500,
};

/**
 * Friendly user-facing message for a failed API response.
 * Fallback chain: `code` → HTTP status → generic. Never returns `body.error`.
 */
export function friendlyError(body: ApiErrorBody | undefined, status: number): string {
    const code = body?.code;
    const entry = code ? CODE_COPY[code] : undefined;
    if (entry !== undefined) {
        return entry(body?.context);
    }
    return STATUS_FALLBACK[status]?.() ?? m.errors_generic_fallback();
}
