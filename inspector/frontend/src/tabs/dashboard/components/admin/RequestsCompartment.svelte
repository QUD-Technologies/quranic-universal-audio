<script lang="ts">
    /**
     * Requests compartment: status facets + a review queue. Clicking a pending
     * row expands an inline review panel (proposed-changes diff + requester
     * note + owner-only resolve actions). Maintainers are read-only; only owners
     * see Send back / Discard. "New request" awareness lives in the My
     * Notifications rail, so this tab carries no unviewed badge.
     */
    import {
        cancelAlign,
        discardRequest,
        fetchRequests,
        probeRequest,
        type RequestStatus,
        retryAlign,
        returnRequest,
        startAlign,
        type AlignDevice,
    } from '../../../../lib/api/admin-requests';
    import { rejectRequestHard, rejectRequestSoft } from '../../../../lib/api/requests';
    import { can } from '../../../../lib/stores/capabilities';
    import { isOwner } from '../../../../lib/stores/current-user';
    import AlignProgress from './AlignProgress.svelte';
    import type {
        AdminRequestRow,
        AdminRequestsResponse,
        ProbeResult,
    } from '../../../../lib/types/generated/schemas';
    import { vocabLabel } from '../../../../lib/i18n/vocab';
    import { titleCaseSlug } from '../../../../lib/utils/delivery-label';
    import { relativeTime } from '../../../../lib/utils/relative-time';
    import { visiblePoll } from '../../../../lib/utils/visible-poll';

    const INTAKE_KINDS = ['existing_reciter_new_combo', 'new_reciter'];
    function isIntake(row: AdminRequestRow): boolean {
        return INTAKE_KINDS.includes(row.kind);
    }
    function kindLabel(row: AdminRequestRow): string {
        if (row.kind === 'new_reciter') return 'New reciter';
        if (row.kind === 'existing_reciter_new_combo') return 'New combination';
        return '';
    }

    const STALE_DAYS = 7;

    let status = $state<RequestStatus>('open');
    let resp = $state<AdminRequestsResponse | null>(null);
    let loading = $state(true);
    let error = $state<string | null>(null);
    let expandedId = $state<string | null>(null);

    // Per-expanded-row resolve state (owner only).
    let reason = $state('');
    let busyId = $state<string | null>(null);
    let actionError = $state<string | null>(null);

    // Native align pipeline (slug rows): one in-flight call at a time.
    const canAlign = can('intake.align');
    let alignBusyId = $state<string | null>(null);
    //: Lane the next started run is sent to. GPU leases ZeroGPU per chapter and
    //: falls back to CPU when the quota runs out; CPU skips the quota entirely.
    const ALIGN_DEVICES: AlignDevice[] = ['GPU', 'CPU'];
    let alignDevice = $state<AlignDevice>('GPU');

    // Shared align budget (all maintainers together; the owner bypasses it).
    const quota = $derived(resp?.align_quota ?? null);
    const laneBlocked = $derived<Record<AlignDevice, boolean>>({
        GPU: !!quota && !quota.exempt && (quota.gpu_used ?? 0) >= (quota.gpu_limit ?? 0),
        CPU: !!quota && !quota.exempt && (quota.cpu_running ?? 0) >= (quota.cpu_limit ?? 0),
    });
    const MS_PER_MINUTE = 60_000;
    function untilLabel(iso: string | null | undefined): string {
        if (!iso) return '';
        const mins = Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / MS_PER_MINUTE));
        const h = Math.floor(mins / 60);
        return h > 0 ? `${h}h ${mins % 60}m` : `${mins}m`;
    }
    function laneTitle(dev: AlignDevice): string {
        if (dev === 'GPU') {
            return laneBlocked.GPU
                ? `Shared GPU budget spent — next run frees in ${untilLabel(quota?.gpu_resets_at)}`
                : 'ZeroGPU lease per chapter; falls back to CPU when the quota runs out';
        }
        return laneBlocked.CPU
            ? 'A CPU run is already going — one at a time for all maintainers'
            : 'The aligner Space CPU worker pool — slower, no GPU quota';
    }

    // Intake-only: reachability probe.
    let probeBusyId = $state<string | null>(null);
    let probeResults = $state<Record<string, ProbeResult[]>>({});

    function applyResult(r: AdminRequestsResponse): void {
        resp = r;
        loading = false;
        error = null;
    }

    // Fetch the active facet on mount + whenever it changes, and keep it warm
    // while the modal stays open. visiblePoll fetches immediately on start.
    $effect(() => {
        const s = status;
        loading = true;
        error = null;
        expandedId = null;
        const teardown = visiblePoll<AdminRequestsResponse>({
            intervalMs: 30_000,
            fetcher: (signal) => fetchRequests(s, signal),
            onResult: applyResult,
            onError: (e) => {
                loading = false;
                error = (e as Error).message ?? 'Failed to load requests';
            },
        });
        return () => teardown();
    });

    function toggle(row: AdminRequestRow): void {
        if (expandedId === row.id) {
            expandedId = null;
            return;
        }
        expandedId = row.id;
        reason = '';
        actionError = null;
    }

    async function resolve(row: AdminRequestRow, kind: 'soft' | 'hard'): Promise<void> {
        if (reason.trim().length < 10 || busyId) return;
        // Edit-requests resolve by slug through the state machine; slugless
        // intake rows resolve by id (pure status mutation).
        if (!isIntake(row) && !row.slug) return;
        busyId = row.id;
        actionError = null;
        try {
            const r = reason.trim();
            if (isIntake(row)) {
                if (kind === 'soft') await returnRequest(row.id, r);
                else await discardRequest(row.id, r);
            } else if (kind === 'soft') {
                await rejectRequestSoft(row.slug!, r);
            } else {
                await rejectRequestHard(row.slug!, r);
            }
            expandedId = null;
            reason = '';
            applyResult(await fetchRequests(status));
        } catch (e) {
            // Includes the resolved-while-open race (404 / InvalidTransition):
            // surface it, then refetch so the list reflects reality.
            actionError =
                (e as Error).message ?? 'This request may already be resolved.';
            try {
                applyResult(await fetchRequests(status));
            } catch {
                /* leave the error visible */
            }
        } finally {
            busyId = null;
        }
    }

    async function align(row: AdminRequestRow, action: 'start' | 'retry' | 'cancel'): Promise<void> {
        if (!row.slug || alignBusyId) return;
        alignBusyId = row.id;
        actionError = null;
        try {
            if (action === 'start') await startAlign(row.slug, 'Large', alignDevice);
            else if (action === 'retry') await retryAlign(row.slug);
            else await cancelAlign(row.slug);
            applyResult(await fetchRequests(status));
        } catch (e) {
            actionError = (e as Error).message ?? 'Align request failed.';
        } finally {
            alignBusyId = null;
        }
    }

    async function probe(row: AdminRequestRow): Promise<void> {
        if (probeBusyId) return;
        probeBusyId = row.id;
        actionError = null;
        try {
            const res = await probeRequest(row.id);
            probeResults[row.id] = res.results ?? [];
        } catch (e) {
            actionError = (e as Error).message ?? 'Probe failed.';
        } finally {
            probeBusyId = null;
        }
    }

    function probeFor(row: AdminRequestRow): ProbeResult[] {
        // Live probe result this session, else the cached one from payload.
        const live = probeResults[row.id];
        if (live) return live;
        const cached = row.probe as { results?: ProbeResult[] } | null | undefined;
        return cached?.results ?? [];
    }

    function sourceSummary(row: AdminRequestRow): string {
        const src = row.source as
            | { method?: string; links?: unknown[]; playlist_url?: string }
            | null
            | undefined;
        if (!src) return '';
        if (src.method === 'playlist') {
            let host = '';
            try {
                host = new URL(src.playlist_url ?? '').hostname.replace(/^www\./, '');
            } catch {
                host = '';
            }
            return host ? `Playlist · ${host}` : 'Playlist';
        }
        return `${src.links?.length ?? 0}/114 links`;
    }

    function displayName(row: AdminRequestRow): string {
        const en = (row.name_en ?? '').trim();
        if (en) return en;
        return row.slug ? titleCaseSlug(row.slug) : '—';
    }

    function comboLabel(row: AdminRequestRow): string {
        const r = vocabLabel('riwayah', row.riwayah);
        const s = vocabLabel('style', row.style);
        return [r, s].filter(Boolean).join(' · ');
    }

    function changeSummary(row: AdminRequestRow): string {
        const n = row.changes?.length ?? 0;
        if (n === 0) return 'no metadata changes';
        return `${n} proposed change${n === 1 ? '' : 's'}`;
    }

    function fmt(v: string | number | null | undefined): string {
        return v === null || v === undefined || v === '' ? '—' : String(v);
    }

    function isStale(row: AdminRequestRow): boolean {
        if (!row.submitted_at) return false;
        const ms = Date.now() - Date.parse(row.submitted_at);
        return ms > STALE_DAYS * 86_400_000;
    }

    const FACETS: { id: RequestStatus; label: string }[] = [
        { id: 'open', label: 'Open' },
        { id: 'accepted', label: 'Accepted' },
        { id: 'returned', label: 'Sent back' },
        { id: 'discarded', label: 'Discarded' },
    ];

    const rows = $derived(resp?.rows ?? []);
    const counts = $derived(
        resp?.counts ?? { open: 0, accepted: 0, returned: 0, discarded: 0 },
    );
    function facetCount(id: RequestStatus): number {
        return (counts as Record<string, number>)[id] ?? 0;
    }
</script>

<div class="reqs">
    <div class="reqs-toolbar">
        <div class="status-filters">
            {#each FACETS as f (f.id)}
                <button
                    class="chip"
                    class:active={status === f.id}
                    class:empty={facetCount(f.id) === 0}
                    onclick={() => (status = f.id)}
                >
                    {f.label}
                    <span class="c-count">{facetCount(f.id)}</span>
                </button>
            {/each}
        </div>
        <span class="toolbar-spacer"></span>
        <span class="toolbar-total">{rows.length} {status === 'open' ? 'open' : 'shown'}</span>
    </div>

    {#if loading}
        <div class="state">Loading…</div>
    {:else if error}
        <div class="state error">{error}</div>
    {:else if rows.length === 0}
        <div class="state empty">
            <div class="glyph">⌖</div>
            <h4>{status === 'open' ? 'No open requests' : 'Nothing here'}</h4>
            <p>
                {status === 'open'
                    ? 'When a contributor requests a reciter combination, it lands here for review.'
                    : 'No requests with this status yet.'}
            </p>
        </div>
    {:else}
        <ol class="queue">
            {#each rows as row (row.id)}
                <li class="req" class:open={expandedId === row.id}>
                    <button
                        class="req-head"
                        aria-expanded={expandedId === row.id}
                        onclick={() => toggle(row)}
                    >
                        <span class="identity">
                            <span class="id-name">
                                <span class="name-en">{displayName(row)}</span>
                                {#if row.name_ar}
                                    <span class="name-ar" dir="rtl">{row.name_ar}</span>
                                {/if}
                                {#if isIntake(row)}
                                    <span class="kind-badge" class:nr={row.kind === 'new_reciter'}>
                                        {kindLabel(row)}
                                    </span>
                                {/if}
                            </span>
                            <span class="id-meta">
                                <span class="combo">{comboLabel(row)}</span>
                                {#if isIntake(row)}
                                    <span class="sep">·</span>
                                    <span class="src-sum">{sourceSummary(row)}</span>
                                {:else}
                                    <span class="sep">·</span>
                                    <span class="changes" class:none={(row.changes?.length ?? 0) === 0}>
                                        {changeSummary(row)}
                                    </span>
                                {/if}
                            </span>
                        </span>

                        <span class="req-meta">
                            {#if row.auto_claim}
                                <span class="req-tag self" title="Requester will be auto-assigned as reviewer when alignment completes">self-review</span>
                            {/if}
                            <span class="requester" class:redacted={!row.requester_login}>
                                {row.requester_login ? `@${row.requester_login}` : (row.requester_role ?? 'contributor')}
                            </span>
                            {#if status === 'open'}
                                <time class="req-age" class:stale={isStale(row)} datetime={row.submitted_at}>
                                    {relativeTime(row.submitted_at)}
                                </time>
                                <span class="chev">▸</span>
                            {:else}
                                <span class="outcome {status}">
                                    {status === 'accepted' ? 'Accepted' : status === 'returned' ? 'Sent back' : 'Discarded'}
                                </span>
                            {/if}
                        </span>
                    </button>

                    {#if expandedId === row.id}
                        <div class="review">
                            <div class="review-grid">
                                <div>
                                    <h4>Proposed changes</h4>
                                    {#if (row.changes?.length ?? 0) === 0}
                                        <p class="no-changes">No metadata changes — alignment request only.</p>
                                    {:else}
                                        <dl class="diff">
                                            {#each row.changes ?? [] as c (c.field)}
                                                <div class="diff-row" class:rtl={c.field === 'name_ar'}>
                                                    <dt>{c.label}</dt>
                                                    <dd>
                                                        <span class="from">{fmt(c.from)}</span>
                                                        <span class="arrow">→</span>
                                                        <span class="to">{fmt(c.to)}</span>
                                                    </dd>
                                                </div>
                                            {/each}
                                        </dl>
                                    {/if}

                                    {#if isIntake(row)}
                                        {@const src = (row.source ?? {}) as { method?: string; links?: { chapter: number; url: string }[]; playlist_url?: string }}
                                        {@const probed = probeFor(row)}
                                        {@const probedByUrl = new Map(probed.map((p) => [p.url, p]))}
                                        <div class="source">
                                            <div class="source-head">
                                                <h4>Audio source</h4>
                                                {#if $isOwner && status === 'open'}
                                                    <button
                                                        class="btn tiny"
                                                        disabled={probeBusyId === row.id}
                                                        onclick={() => probe(row)}
                                                    >{probeBusyId === row.id ? 'Probing…' : 'Probe reachability'}</button>
                                                {/if}
                                            </div>
                                            {#if src.method === 'playlist'}
                                                <a class="playlist-link" href={src.playlist_url} target="_blank" rel="noopener noreferrer">
                                                    {src.playlist_url}
                                                </a>
                                                {#if probed[0]}
                                                    {@const p = probed[0]}
                                                    <span class="reach {p.reachable ? 'ok' : 'bad'}">
                                                        {p.reachable ? `reachable (${p.status})` : `unreachable${p.status ? ` (${p.status})` : ''}`}
                                                    </span>
                                                {/if}
                                            {:else}
                                                <ul class="links">
                                                    {#each src.links ?? [] as ln (ln.chapter)}
                                                        {@const p = probedByUrl.get(ln.url)}
                                                        <li>
                                                            <span class="ln-num">{String(ln.chapter).padStart(3, '0')}</span>
                                                            <a class="ln-url" href={ln.url} target="_blank" rel="noopener noreferrer">{ln.url}</a>
                                                            {#if p}
                                                                <span class="ln-reach {p.reachable ? 'ok' : 'bad'}" title={p.status ? `HTTP ${p.status}` : 'unreachable'}>
                                                                    {p.reachable ? '✓' : '✗'}
                                                                </span>
                                                            {:else}
                                                                <span class="ln-reach none">–</span>
                                                            {/if}
                                                        </li>
                                                    {/each}
                                                </ul>
                                            {/if}
                                        </div>
                                    {/if}
                                </div>

                                <div class="review-side">
                                    <p class="submitted-by">
                                        {#if row.requester_login}
                                            Submitted by <strong>@{row.requester_login}</strong>
                                        {:else}
                                            Submitted by <strong>a {row.requester_role ?? 'contributor'}</strong>
                                        {/if}
                                        {#if row.submitted_at}· {relativeTime(row.submitted_at)}{/if}
                                    </p>

                                    {#if row.comments}
                                        <p class="note">
                                            <span class="note-label">Requester note</span>
                                            {row.comments}
                                        </p>
                                    {/if}

                                    {#if row.conflict}
                                        <p class="notice">
                                            {#if isIntake(row)}
                                                {row.name_en ?? 'This reciter'} already has a delivery with
                                                this riwayah · style. Ingesting creates another.
                                            {:else}
                                                Another delivery of {row.name_en ?? 'this reciter'} already uses
                                                this riwayah · style. The edit still applies on acceptance.
                                            {/if}
                                        </p>
                                    {/if}

                                    {#if status === 'open' && !isIntake(row) && row.slug && $canAlign}
                                        <div class="align-block">
                                            {#if row.align}
                                                <AlignProgress
                                                    run={row.align}
                                                    busy={alignBusyId === row.id}
                                                    onRetry={() => align(row, 'retry')}
                                                    onCancel={() => align(row, 'cancel')}
                                                />
                                            {:else}
                                                <div class="align-cta">
                                                    <span class="align-hint">
                                                        Acquire the audio, align every chapter on the aligner Space,
                                                        compute the sidecars and publish for review.
                                                    </span>
                                                    <div class="align-go">
                                                        <div
                                                            class="lane"
                                                            role="group"
                                                            aria-label="Alignment lane"
                                                        >
                                                            {#each ALIGN_DEVICES as dev (dev)}
                                                                <button
                                                                    type="button"
                                                                    class="lane-opt"
                                                                    class:on={alignDevice === dev}
                                                                    aria-pressed={alignDevice === dev}
                                                                    disabled={alignBusyId === row.id}
                                                                    class:blocked={laneBlocked[dev]}
                                                                    title={laneTitle(dev)}
                                                                    onclick={() => (alignDevice = dev)}
                                                                >{dev}</button>
                                                            {/each}
                                                        </div>
                                                        <button
                                                            class="btn primary"
                                                            disabled={alignBusyId === row.id || laneBlocked[alignDevice]}
                                                            title={laneBlocked[alignDevice] ? laneTitle(alignDevice) : undefined}
                                                            onclick={() => align(row, 'start')}
                                                        >{alignBusyId === row.id ? 'Starting…' : 'Align'}</button>
                                                    </div>
                                                </div>
                                            {/if}
                                            {#if quota && !row.align}
                                                <p class="align-quota">
                                                    {#if quota.exempt}
                                                        <span>No limits for you</span> ·
                                                    {/if}
                                                    <span class:spent={laneBlocked.GPU}>
                                                        GPU {quota.gpu_used}/{quota.gpu_limit} in 24 h{#if quota.gpu_resets_at}
                                                            &nbsp;(next frees in {untilLabel(quota.gpu_resets_at)}){/if}
                                                    </span>
                                                    ·
                                                    <span class:spent={laneBlocked.CPU}>
                                                        CPU {quota.cpu_running}/{quota.cpu_limit} running
                                                    </span>
                                                    · shared by all maintainers
                                                </p>
                                                {#if laneBlocked[alignDevice]}
                                                    <p class="align-blocked">{laneTitle(alignDevice)}.</p>
                                                {/if}
                                            {/if}
                                            {#if actionError && alignBusyId === null}
                                                <p class="action-error">{actionError}</p>
                                            {/if}
                                        </div>
                                    {/if}

                                    {#if status === 'open'}
                                        {#if $isOwner}
                                            <div class="resolve">
                                                {#if isIntake(row)}
                                                    <p class="ingest-note">
                                                        Ingested offline by the alignment pipeline — aligning
                                                        this submission is the acceptance; no action needed here.
                                                        Send back or discard below to reject it.
                                                    </p>
                                                {/if}
                                                <textarea
                                                    bind:value={reason}
                                                    rows="2"
                                                    maxlength="500"
                                                    placeholder="Reason (≥10 chars) — recorded in the audit log"
                                                ></textarea>
                                                {#if actionError}
                                                    <p class="action-error">{actionError}</p>
                                                {/if}
                                                <div class="resolve-foot">
                                                    <span class="resolve-hint">Required for send back / discard.</span>
                                                    <div class="resolve-actions">
                                                        <button
                                                            class="btn"
                                                            disabled={reason.trim().length < 10 || busyId === row.id}
                                                            onclick={() => resolve(row, 'soft')}
                                                        >Send back</button>
                                                        <button
                                                            class="btn danger"
                                                            disabled={reason.trim().length < 10 || busyId === row.id}
                                                            onclick={() => resolve(row, 'hard')}
                                                        >Discard</button>
                                                    </div>
                                                </div>
                                            </div>
                                        {:else}
                                            <p class="ro-note">Resolving a request is owner-only.</p>
                                        {/if}
                                    {:else}
                                        <div class="resolution">
                                            <span class="res-by">
                                                {#if row.resolved_by_login}
                                                    {status === 'accepted' ? 'Accepted' : status === 'returned' ? 'Sent back' : 'Discarded'}
                                                    by <strong>@{row.resolved_by_login}</strong>
                                                {:else if status === 'accepted'}
                                                    Accepted by the alignment pipeline
                                                {:else}
                                                    {status === 'returned' ? 'Sent back' : 'Discarded'}
                                                    by <strong>a {row.resolved_by_role ?? 'maintainer'}</strong>
                                                {/if}
                                                {#if row.resolved_at}· {relativeTime(row.resolved_at)}{/if}
                                            </span>
                                            {#if row.resolution_reason}
                                                <span class="res-reason">— {row.resolution_reason}</span>
                                            {/if}
                                        </div>
                                    {/if}
                                </div>
                            </div>
                        </div>
                    {/if}
                </li>
            {/each}
        </ol>
    {/if}
</div>

<style>
    .reqs { display: flex; flex-direction: column; height: 100%; min-height: 0; }

    .state { padding: var(--s-12) 0; text-align: center; color: var(--text-muted); font-size: var(--fs-meta); }
    .state.error { color: var(--state-error-fg); }
    .state.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: var(--s-2); }
    .state.empty .glyph { font-size: 26px; opacity: 0.5; }
    .state.empty h4 { margin: 0; font-size: var(--fs-row); font-weight: 500; color: var(--text-secondary); }
    .state.empty p { margin: 0; font-size: var(--fs-meta); color: var(--text-faint); max-width: 44ch; }

    .reqs-toolbar {
        display: flex; align-items: center; gap: var(--s-4);
        padding: var(--s-4) var(--s-5);
        border-bottom: 1px solid var(--border-quiet);
        flex-shrink: 0;
    }
    .status-filters { display: inline-flex; gap: var(--s-2); }
    .chip {
        display: inline-flex; align-items: center; gap: var(--s-2);
        height: 28px; padding: 0 var(--s-3);
        background: transparent; border: 1px solid var(--border-quiet);
        border-radius: 999px; color: var(--text-secondary); font-size: var(--fs-meta);
        cursor: pointer;
        transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
    }
    .chip:hover { border-color: var(--border-strong); color: var(--text-primary); }
    .chip .c-count { font-family: var(--font-mono); font-size: 10.5px; font-variant-numeric: tabular-nums; color: var(--text-faint); }
    .chip.active { background: var(--accent-tint); border-color: var(--accent); color: var(--accent); }
    .chip.active .c-count { color: var(--accent); }
    .chip.empty { opacity: 0.45; }
    .toolbar-spacer { flex: 1; }
    .toolbar-total { font-size: var(--fs-meta); color: var(--text-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }

    .queue { list-style: none; margin: 0; padding: 0; overflow: auto; flex: 1; min-height: 0; }
    .req { border-bottom: 1px solid var(--border-quiet); }
    .req:last-child { border-bottom: none; }

    .req-head {
        display: flex;
        align-items: center;
        gap: var(--s-5);
        width: 100%;
        padding: var(--s-3) var(--s-5);
        text-align: left;
        background: transparent; border: 0; cursor: pointer;
        transition: background var(--t-fast);
    }
    .req-head:hover { background: var(--panel); }
    .req.open > .req-head { background: var(--panel); }

    /* identity grows; meta cluster keeps its intrinsic width on the right.
       Two zones, so left edges and right edges align row-to-row without a
       rigid column grid stranding content in the middle. */
    .identity { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
    .id-name { display: flex; align-items: baseline; gap: var(--s-2); min-width: 0; }
    /* Latin leads at row-text size + primary; Arabic trails as a muted
     * inline detail. Matches the Reviews tab convention. */
    .name-en { font-size: var(--fs-row); color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .name-ar { font-size: 13px; color: var(--text-muted); line-height: 1.3; white-space: nowrap; unicode-bidi: isolate; }

    .id-meta { display: flex; align-items: baseline; gap: var(--s-2); font-size: var(--fs-meta); min-width: 0; }
    .id-meta .combo { color: var(--text-secondary); white-space: nowrap; }
    .id-meta .sep { color: var(--text-faint); }
    .id-meta .changes { color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .id-meta .changes.none { color: var(--text-faint); font-style: italic; }
    .id-meta .src-sum { color: var(--text-muted); white-space: nowrap; font-family: var(--font-mono); font-size: 11px; }

    .kind-badge {
        flex-shrink: 0;
        padding: 1px 7px; border-radius: 999px;
        font-size: 10px; font-weight: 500; letter-spacing: 0.02em;
        color: var(--accent); background: var(--accent-tint);
        white-space: nowrap;
    }
    .kind-badge.nr { color: var(--state-published-fg); background: var(--state-published-bg); }

    .req-meta { flex-shrink: 0; display: inline-flex; align-items: center; gap: var(--s-3); justify-content: flex-end; white-space: nowrap; }
    .req-tag { display: inline-flex; align-items: center; gap: 5px; padding: 1px 8px; border-radius: 999px; font-size: 10px; font-weight: 500; color: var(--text-secondary); background: oklch(0.62 0.02 268 / 0.16); }
    .req-tag.self { color: var(--accent); background: var(--accent-tint); }
    .req-tag.self::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: var(--accent); }
    .requester { font-size: var(--fs-meta); color: var(--text-secondary); }
    .requester.redacted { color: var(--text-faint); font-style: italic; }
    .req-age { font-size: 10.5px; color: var(--text-faint); font-variant-numeric: tabular-nums; }
    .req-age.stale { color: var(--state-error-fg); }
    .chev { color: var(--text-faint); font-size: 11px; transition: transform var(--t-base) var(--ease-out-quart); }
    .req.open .chev { transform: rotate(90deg); color: var(--text-muted); }

    .outcome { display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 500; }
    .outcome::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
    .outcome.accepted { color: var(--state-published-fg); background: var(--state-published-bg); }
    .outcome.returned { color: var(--state-error-fg); background: oklch(0.86 0.13 75 / 0.14); }
    .outcome.returned::before { border-radius: 1px; }
    .outcome.discarded { color: var(--state-discarded-fg); background: var(--state-discarded-bg); }

    .review {
        padding: var(--s-2) var(--s-5) var(--s-5);
        background: var(--panel);
        border-top: 1px solid var(--border-quiet);
        animation: review-in var(--t-base) var(--ease-out-quart);
    }
    @keyframes review-in {
        from { opacity: 0; transform: translateY(-4px); }
        to { opacity: 1; transform: none; }
    }
    .review-grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: var(--s-6); align-items: start; }
    .review h4 { margin: 0 0 var(--s-3); font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 500; }

    .diff { margin: 0; }
    .diff-row { display: grid; grid-template-columns: 120px 1fr; gap: var(--s-3); align-items: baseline; padding: var(--s-2) 0; border-bottom: 1px solid var(--border-quiet); }
    .diff-row:last-child { border-bottom: none; }
    .diff-row dt { color: var(--text-muted); font-size: var(--fs-meta); }
    .diff-row dd { margin: 0; font-size: var(--fs-body); }
    .diff-row .from { color: var(--text-faint); text-decoration: line-through; }
    .diff-row .arrow { margin: 0 var(--s-2); color: var(--text-faint); }
    .diff-row .to { color: var(--text-primary); }
    .diff-row.rtl .to { direction: rtl; unicode-bidi: isolate; }
    .no-changes { font-size: var(--fs-meta); color: var(--text-faint); font-style: italic; padding: var(--s-2) 0; }

    /* intake audio source */
    .source { margin-top: var(--s-4); }
    .source-head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); margin-bottom: var(--s-2); }
    .source-head h4 { margin: 0; }
    .playlist-link { font-family: var(--font-mono); font-size: 11.5px; color: var(--accent); word-break: break-all; }
    .reach { display: inline-block; margin-top: 4px; font-size: 10.5px; }
    .reach.ok { color: var(--state-published-fg); }
    .reach.bad { color: var(--state-error-fg); }
    .links {
        list-style: none; margin: 0; padding: 4px;
        max-height: 220px; overflow-y: auto;
        background: var(--canvas-inset); border: 1px solid var(--border-quiet); border-radius: var(--r-2);
        display: flex; flex-direction: column; gap: 1px;
    }
    .links li { display: grid; grid-template-columns: 30px 1fr 16px; align-items: center; gap: var(--s-2); padding: 2px 4px; }
    .ln-num { font-family: var(--font-mono); font-size: 10px; color: var(--text-faint); text-align: right; }
    .ln-url { font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ln-url:hover { color: var(--accent); }
    .ln-reach { font-size: 11px; text-align: center; font-weight: 600; }
    .ln-reach.ok { color: var(--state-published-fg); }
    .ln-reach.bad { color: var(--state-error-fg); }
    .ln-reach.none { color: var(--text-faint); font-weight: 400; }

    .btn.tiny { padding: 3px 9px; font-size: 10.5px; }
    .ingest-note {
        margin: 0 0 var(--s-2);
        padding: var(--s-2) var(--s-3);
        background: var(--accent-tint);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        font-size: var(--fs-meta);
        line-height: var(--lh-normal);
        color: var(--text-secondary);
    }

    .review-side { display: flex; flex-direction: column; gap: var(--s-4); }
    .submitted-by { margin: 0; font-size: var(--fs-meta); color: var(--text-muted); }
    .submitted-by strong { color: var(--text-secondary); font-weight: 500; }
    .note { margin: 0; padding: var(--s-3); background: var(--canvas-inset); border: 1px solid var(--border-quiet); border-radius: var(--r-2); font-size: var(--fs-body); color: var(--text-secondary); line-height: var(--lh-normal); }
    .note .note-label { display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-faint); margin-bottom: 4px; }
    .align-block { display: flex; flex-direction: column; gap: var(--s-2); padding-top: var(--s-3); border-top: 1px solid var(--border-quiet); }
    .align-cta { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }
    .align-hint { font-size: var(--fs-meta); color: var(--text-muted); line-height: var(--lh-normal); }
    .align-go { display: flex; align-items: center; gap: var(--s-2); flex-shrink: 0; }
    .lane { display: inline-flex; border: 1px solid var(--border-quiet); border-radius: var(--r-2); overflow: hidden; }
    .lane-opt {
        padding: 4px var(--s-2); background: transparent; border: 0; cursor: pointer;
        font-size: var(--fs-meta); font-family: inherit; color: var(--text-muted);
    }
    .lane-opt + .lane-opt { border-left: 1px solid var(--border-quiet); }
    .lane-opt:hover:not(:disabled) { color: var(--text-secondary); }
    .lane-opt.on { background: var(--canvas-inset); color: var(--accent); }
    .lane-opt:disabled { opacity: 0.5; cursor: default; }
    .lane-opt.blocked { text-decoration: line-through; }
    .align-quota { margin: 0; font-size: var(--fs-meta); color: var(--text-faint); font-variant-numeric: tabular-nums; }
    .align-quota .spent { color: var(--state-error-fg); }
    .align-blocked { margin: 0; font-size: var(--fs-meta); color: var(--text-muted); }
    .btn.primary { color: var(--accent); border-color: var(--accent); }
    .btn.primary:hover { background: var(--accent-soft, transparent); }
    .notice { margin: 0; padding: var(--s-3); background: var(--state-requested-bg); color: var(--state-requested-fg); border-radius: var(--r-2); font-size: var(--fs-meta); line-height: var(--lh-normal); }
    .ro-note { margin: 0; font-size: var(--fs-meta); color: var(--text-faint); font-style: italic; }

    .resolve { display: flex; flex-direction: column; gap: var(--s-2); padding-top: var(--s-3); border-top: 1px solid var(--border-quiet); }
    .resolve textarea { width: 100%; resize: vertical; min-height: 52px; padding: var(--s-2) var(--s-3); background: var(--canvas-inset); border: 1px solid var(--border-default); border-radius: var(--r-2); color: var(--text-primary); font: var(--fs-body)/var(--lh-normal) var(--font-sans); }
    .resolve textarea:focus { outline: none; border-color: var(--accent); }
    .resolve textarea::placeholder { color: var(--text-faint); }
    .action-error { margin: 0; font-size: var(--fs-meta); color: var(--state-error-fg); }
    .resolve-foot { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }
    .resolve-hint { font-size: 10.5px; color: var(--text-faint); }
    .resolve-actions { display: inline-flex; gap: var(--s-2); }
    .btn { padding: 6px 14px; border-radius: var(--r-2); font: 500 var(--fs-meta)/1 var(--font-sans); border: 1px solid var(--border-default); background: transparent; color: var(--text-secondary); cursor: pointer; transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast); }
    .btn:hover { color: var(--text-primary); border-color: var(--border-strong); }
    .btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .btn.danger { color: var(--state-error-fg); border-color: oklch(0.86 0.13 75 / 0.4); }
    .btn.danger:hover { background: oklch(0.86 0.13 75 / 0.12); border-color: var(--state-error-fg); }

    .resolution { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s-2); padding: var(--s-3); background: var(--canvas-inset); border: 1px solid var(--border-quiet); border-radius: var(--r-2); font-size: var(--fs-meta); color: var(--text-muted); line-height: var(--lh-normal); }
    .resolution strong { color: var(--text-secondary); font-weight: 500; }
    .resolution .res-reason { color: var(--text-secondary); }

    @media (prefers-reduced-motion: reduce) {
        .review { animation: none; }
        .chev { transition: none; }
    }
</style>
