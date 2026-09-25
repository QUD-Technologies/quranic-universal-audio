<script lang="ts">
    /**
     * Unified Jobs compartment — every job kind in one place.
     *
     * Polls ``GET /api/admin/jobs`` (running + historical across all kinds and
     * reciters, newest-first), filterable by kind / status / reciter. Running
     * jobs are pinned on top. Clicking any row opens the shared
     * ``JobDetailDrawer`` (record, settings, logs, members, Open on HF, Cancel).
     */
    import { fetchJobs } from '../../../../../lib/api/admin-jobs';
    import type { JobRecord } from '../../../../../lib/types/generated/schemas';
    import { relativeTime } from '../../../../../lib/utils/relative-time';
    import { visiblePoll } from '../../../../../lib/utils/visible-poll';
    import JobDetailDrawer from './JobDetailDrawer.svelte';

    const KIND_LABELS: Record<string, string> = {
        timestamps: 'Timestamps',
        hf_publish: 'HF publish',
        hf_publish_batch: 'HF batch',
        cut_release: 'GH cut',
        refresh_catalog: 'Catalog refresh',
        acquire_audio: 'Acquire audio',
        split_audio: 'Split audio',
    };
    const KIND_FILTERS = ['all', ...Object.keys(KIND_LABELS)] as const;
    const STATUS_FILTERS = ['all', 'running', 'succeeded', 'failed'] as const;

    let jobs = $state<JobRecord[]>([]);
    let loading = $state(true);
    let error = $state<string | null>(null);
    let kindFilter = $state<string>('all');
    let statusFilter = $state<string>('all');
    let search = $state('');
    let selected = $state<JobRecord | null>(null);

    function applyResult(list: JobRecord[]): void {
        jobs = list;
        loading = false;
        error = null;
    }

    $effect(() => {
        const teardown = visiblePoll<JobRecord[]>({
            intervalMs: 10_000,
            fetcher: async (signal) => (await fetchJobs(signal)).jobs ?? [],
            onResult: applyResult,
            onError: (e) => {
                loading = false;
                error = e instanceof Error ? e.message : 'Failed to load jobs';
            },
        });
        return teardown;
    });

    async function refreshNow(): Promise<void> {
        try {
            jobs = (await fetchJobs()).jobs ?? [];
        } catch {
            /* next poll retries */
        }
    }

    const filtered = $derived.by(() => {
        const q = search.trim().toLowerCase();
        const rows = jobs.filter((j) => {
            if (kindFilter !== 'all' && j.kind !== kindFilter) return false;
            if (statusFilter !== 'all' && (j.status ?? 'running') !== statusFilter) return false;
            if (q && !(j.slug ?? '').toLowerCase().includes(q) && !j.job_id.toLowerCase().includes(q))
                return false;
            return true;
        });
        // Running pinned on top; the server already sorts newest-first within.
        const running = rows.filter((j) => (j.status ?? 'running') === 'running');
        const rest = rows.filter((j) => (j.status ?? 'running') !== 'running');
        return [...running, ...rest];
    });

    const runningCount = $derived(jobs.filter((j) => (j.status ?? 'running') === 'running').length);

    function statusTone(s: string | null | undefined): 'ok' | 'fail' | 'run' {
        if (s === 'succeeded') return 'ok';
        if (s === 'failed') return 'fail';
        return 'run';
    }
</script>

<div class="jc">
    <div class="jc-bar">
        <div class="chips">
            {#each KIND_FILTERS as k (k)}
                <button class="chip" class:active={kindFilter === k} onclick={() => (kindFilter = k)}>
                    {k === 'all' ? 'All kinds' : KIND_LABELS[k]}
                </button>
            {/each}
        </div>
        <div class="chips">
            {#each STATUS_FILTERS as s (s)}
                <button class="chip" class:active={statusFilter === s} onclick={() => (statusFilter = s)}>
                    {s === 'all' ? 'Any status' : s}
                </button>
            {/each}
        </div>
        <input class="search" type="text" placeholder="Filter by reciter / job id…" bind:value={search} />
        <span class="tally">{runningCount} running · {jobs.length} total</span>
    </div>

    {#if loading}
        <div class="muted">Loading jobs…</div>
    {:else if error}
        <div class="err">{error}</div>
    {:else if filtered.length === 0}
        <div class="muted">No jobs match these filters.</div>
    {:else}
        <ul class="rows">
            {#each filtered as j (j.job_id)}
                <button class="row" type="button" onclick={() => (selected = j)}>
                    <span class="badge {statusTone(j.status)}">{j.status ?? 'running'}</span>
                    <span class="kind">{KIND_LABELS[j.kind ?? 'timestamps'] ?? j.kind}</span>
                    <span class="who mono">{j.slug ?? 'Global'}</span>
                    <span class="jid mono">{j.job_id.slice(0, 10)}</span>
                    {#if j.member_count}<span class="members">{j.member_count} members</span>{/if}
                    <span class="when">{j.started_at ? relativeTime(j.started_at) : '—'}</span>
                </button>
            {/each}
        </ul>
    {/if}
</div>

{#if selected}
    <JobDetailDrawer
        kind={selected.kind ?? 'timestamps'}
        jobId={selected.job_id}
        slug={selected.slug}
        onClose={() => (selected = null)}
        onChanged={refreshNow}
    />
{/if}

<style>
    .jc { display: flex; flex-direction: column; gap: var(--s-3); padding: var(--s-2) 0; }
    .jc-bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2) var(--s-3); }
    .chips { display: inline-flex; gap: 3px; flex-wrap: wrap; }
    .chip {
        font-size: 11px; color: var(--text-muted);
        background: transparent; border: 1px solid var(--border-quiet);
        border-radius: 999px; padding: 2px 10px; cursor: pointer;
        transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast);
    }
    .chip:hover { color: var(--text-secondary); }
    .chip.active { color: var(--accent-strong); border-color: var(--accent-tint); background: var(--accent-tint-soft); }
    .search {
        flex: 1; min-width: 160px;
        font: inherit; font-size: 12px;
        padding: 4px 10px; color: var(--text-primary);
        background: var(--canvas-inset); border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
    }
    .tally { font-family: var(--font-mono); font-size: 10.5px; color: var(--text-faint); white-space: nowrap; }

    .muted { font-size: var(--fs-meta); color: var(--text-faint); padding: var(--s-3) 0; }
    .err { color: var(--state-error-fg); padding: var(--s-2) 0; }

    .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
    .row {
        display: grid;
        grid-template-columns: 72px 96px 1fr 110px auto max-content;
        align-items: center; gap: var(--s-3);
        width: 100%; text-align: left;
        background: transparent; border: 1px solid transparent; border-radius: var(--r-1);
        padding: 5px 8px; font: inherit; cursor: pointer;
    }
    .row:hover { background: var(--panel); }
    .kind { font-size: 11px; color: var(--text-secondary); }
    .who { font-size: 11px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .jid { font-size: 10.5px; color: var(--text-faint); }
    .members { font-size: 10px; color: var(--text-faint); }
    .mono { font-family: var(--font-mono); }
    .when {
        font-family: var(--font-mono); font-size: 10px; color: var(--text-faint);
        font-variant-numeric: tabular-nums; white-space: nowrap;
    }
    .badge {
        font-family: var(--font-mono); font-size: 9.5px; text-transform: uppercase;
        letter-spacing: 0.04em; padding: 2px 7px; border-radius: 999px;
        background: var(--panel-2); color: var(--text-secondary); text-align: center;
    }
    .badge.ok { color: var(--state-published-fg); background: var(--state-published-bg); }
    .badge.fail { color: var(--state-error-fg); background: var(--state-error-bg); }
    .badge.run { color: var(--accent-strong); background: var(--accent-tint-soft); }
</style>
