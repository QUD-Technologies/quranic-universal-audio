/**
 * Admin Releases-tab API client (v2 dataset/release tracks).
 *
 * - fetchReleasesStatus    per-recitation status grid (TS / HF / GH badges)
 * - fetchReleasePreview    dry-run diff + auto-version + CHANGELOG preview
 * - publishHfBatch         launch ONE job publishing a batch of slugs
 * - cancelReleaseJob       cancel an in-flight release job by id
 * - cutRelease             launch the global GH release cut job
 * - refreshHfCatalog       rebuild only the HF dataset ``mushafs`` catalog
 *
 */

import type {
    AdminCutReleaseRequest,
    AdminGhReleaseMember,
    AdminInFlightJob,
    AdminLastBatch,
    AdminLatestGhRelease,
    AdminLaunchResponse,
    AdminPublishError,
    AdminReleasePreviewResponse,
    AdminReleasePreviewRow,
    AdminReleaseRow,
    AdminReleaseStatusRow,
    AdminReleasesStatusResponse,
    AdminReleasesSummary,
    AutomationConfig,
    AutomationResponse,
    AutomationStateRow,
    SuggestedAction,
} from '../types/generated/schemas';

export type ReleaseRow = AdminReleaseRow;
export type GhReleaseMember = AdminGhReleaseMember;
export type ReleaseStatusRow = AdminReleaseStatusRow;
export type LatestGhRelease = AdminLatestGhRelease;
export type ReleasesSummary = AdminReleasesSummary;
export type InFlightJob = AdminInFlightJob;
export type LastBatch = AdminLastBatch;
export type PublishError = AdminPublishError;
export type ReleasesStatusResponse = AdminReleasesStatusResponse;
export type ReleasePreviewRow = AdminReleasePreviewRow;
export type ReleasePreviewResponse = AdminReleasePreviewResponse;
export type LaunchResponse = AdminLaunchResponse;
export type CutReleaseBody = AdminCutReleaseRequest;
export type StaleSuggestion = SuggestedAction;
export type AutomationConfigBody = AutomationConfig;
export type AutomationPayload = AutomationResponse;
export type AutomationState = AutomationStateRow;

async function _unwrap<T>(resp: Response): Promise<T> {
    if (resp.ok) return (await resp.json()) as T;
    let msg = `HTTP ${resp.status}`;
    try {
        const body = (await resp.json()) as { error?: string };
        if (body?.error) msg = body.error;
    } catch {
        /* non-JSON body — keep status fallback */
    }
    throw new Error(msg);
}

export async function fetchReleasesStatus(signal?: AbortSignal): Promise<ReleasesStatusResponse> {
    const resp = await fetch('/api/admin/releases/status', { signal });
    return _unwrap<ReleasesStatusResponse>(resp);
}

export async function fetchReleasePreview(signal?: AbortSignal): Promise<ReleasePreviewResponse> {
    const resp = await fetch('/api/admin/release-preview', { signal });
    return _unwrap<ReleasePreviewResponse>(resp);
}

/** Clip-edge knobs applied to BOTH release channels (HF publish + GH cut). */
export interface ReleaseSettings {
    pad_start: number;
    pad_end: number;
    min_gap: number;
}

/** Launch ONE HF Job that publishes a batch of slugs. A single slug is just a
 *  batch of one. Returns the launched job id; throws the server error verbatim
 *  (e.g. the 409 "a cut_release is in flight" / "a job is already running").
 *  Optional ``settings`` overrides the server-side pad/gap defaults. */
export async function publishHfBatch(slugs: string[], settings?: ReleaseSettings): Promise<LaunchResponse> {
    const resp = await fetch('/api/admin/publish-hf-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slugs, ...(settings ? { settings } : {}) }),
    });
    return _unwrap<LaunchResponse>(resp);
}

/** Cancel an in-flight release job (publish / batch / cut / timestamps) by id.
 *  Hard-kills the HF Job container; the next status poll drops the row. */
export async function cancelReleaseJob(jobId: string): Promise<{ job_id: string; canceled: boolean }> {
    const resp = await fetch(
        `/api/admin/release-jobs/${encodeURIComponent(jobId)}/cancel`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' } },
    );
    return _unwrap<{ job_id: string; canceled: boolean }>(resp);
}

/** Launch the global GH cut job. Owner-only via ``release.cut_gh``.
 *  ``expected_version_at_preview`` carries the version the preview computed;
 *  the server 409s if it drifted (forces a re-preview). */
export async function cutRelease(body: CutReleaseBody): Promise<LaunchResponse> {
    const resp = await fetch('/api/admin/cut-release', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    return _unwrap<LaunchResponse>(resp);
}

/** Launch the global HF dataset catalog refresh (the ``mushafs`` subset only).
 *  Rebuilds the dataset catalog config + card from the live catalog with no
 *  audio re-slice — the cheap remediation for ``catalog_edit`` HF staleness.
 *  Gated by ``release.publish_hf``. Throws the server 409 verbatim when a
 *  refresh / batch is already in flight. */
export async function refreshHfCatalog(): Promise<LaunchResponse> {
    const resp = await fetch('/api/admin/refresh-hf-catalog', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    });
    return _unwrap<LaunchResponse>(resp);
}

/** Map an actionable ``SuggestedAction.action`` code to its launcher. Keeps the
 *  reason→action wiring on the FE to this one table (the label / kind / gate all
 *  come from the server-resolved suggestion). ``republish_hf`` has no direct
 *  launcher here — it routes through the select-and-publish batch flow. */
export const SUGGESTION_LAUNCHERS: Record<string, () => Promise<LaunchResponse>> = {
    refresh_hf_catalog: refreshHfCatalog,
};

/** Owner-only: the automation config + per-automation runtime state + the
 *  auto-computed next GH version. Gated by ``release.manage_automation`` (403
 *  for non-owners — the FE only mounts the section for owners). */
export async function fetchAutomation(signal?: AbortSignal): Promise<AutomationPayload> {
    const resp = await fetch('/api/admin/releases/automation', { signal });
    return _unwrap<AutomationPayload>(resp);
}

/** Owner-only: replace the whole automation config. Returns the fresh payload
 *  (config + state + next_version) so the caller re-renders from the server. */
export async function saveAutomation(config: AutomationConfigBody): Promise<AutomationPayload> {
    const resp = await fetch('/api/admin/releases/automation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
    });
    return _unwrap<AutomationPayload>(resp);
}
