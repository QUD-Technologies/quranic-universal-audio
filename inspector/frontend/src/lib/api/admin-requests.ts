/**
 * Admin dashboard → Requests-tab API client.
 *
 * Backed by ``/api/admin/requests`` (maintainer+) — see
 * ``inspector/routes/claims/requests.py``. Reject actions (owner-only) live in
 * ``lib/api/requests.ts`` (keyed by slug). "New request" awareness is surfaced
 * via the My Notifications rail, so there is no unviewed-count / view-mark here.
 */

import type {
    AdminRequestsResponse,
    AlignRunStatus,
    ProbeResponse,
} from '../types/generated/schemas';

export type RequestStatus = 'open' | 'accepted' | 'returned' | 'discarded';

const _JSON = { 'Content-Type': 'application/json' };

export async function fetchRequests(
    status: RequestStatus,
    signal?: AbortSignal,
): Promise<AdminRequestsResponse> {
    const res = await fetch(`/api/admin/requests?status=${status}`, { signal });
    if (!res.ok) throw new Error(`fetchRequests: HTTP ${res.status}`);
    return (await res.json()) as AdminRequestsResponse;
}

// ---- Intake (slugless new-combo / new-reciter) owner actions ----------------
//
// No accept action: a submission is directly ingest-actionable (aligning it via
// the offline pipeline is the acceptance). Owners can still probe / return /
// discard a pending submission.

async function _post(url: string, body?: unknown): Promise<Record<string, unknown>> {
    const res = await fetch(url, {
        method: 'POST',
        headers: _JSON,
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const json = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) {
        throw new Error((json.error as string) ?? `HTTP ${res.status}`);
    }
    return json;
}

/** Reachability-probe an intake request's audio source (owner-only). */
export async function probeRequest(id: string): Promise<ProbeResponse> {
    const json = await _post(`/api/admin/requests/${id}/probe`);
    return json as unknown as ProbeResponse;
}

/** Send an intake request back to the contributor (≥10-char reason). */
export async function returnRequest(id: string, reason: string): Promise<void> {
    await _post(`/api/admin/requests/${id}/return`, { reason });
}

/** Discard an intake request (≥10-char reason). */
export async function discardRequest(id: string, reason: string): Promise<void> {
    await _post(`/api/admin/requests/${id}/discard`, { reason });
}

// ---- Native align pipeline (slug rows awaiting alignment) -------------------
//
// One click runs acquire → align → sidecars → assemble on the existing Spaces;
// the row's ``align`` overlay (and this status call) carry the live progress.

export type AlignModelName = 'Base' | 'Large';
/** Lane the aligner Space runs the batch on. GPU falls back to CPU on quota exhaustion. */
export type AlignDevice = 'GPU' | 'CPU';

/** Start the native align pipeline for a catalogued slug. */
export async function startAlign(
    slug: string,
    modelName: AlignModelName = 'Large',
    device: AlignDevice = 'GPU',
): Promise<AlignRunStatus> {
    const json = await _post(`/api/admin/reciter/${encodeURIComponent(slug)}/align`, {
        model_name: modelName,
        device,
    });
    return json as unknown as AlignRunStatus;
}

/** Resume a failed run from the stage it failed at. */
export async function retryAlign(slug: string): Promise<AlignRunStatus> {
    const json = await _post(`/api/admin/reciter/${encodeURIComponent(slug)}/align/retry`);
    return json as unknown as AlignRunStatus;
}

/** Stop a running (or abandon a failed) run. */
export async function cancelAlign(slug: string): Promise<AlignRunStatus> {
    const json = await _post(`/api/admin/reciter/${encodeURIComponent(slug)}/align/cancel`);
    return json as unknown as AlignRunStatus;
}
