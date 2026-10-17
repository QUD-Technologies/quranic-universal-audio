<script lang="ts">
    /**
     * Align a requested delivery from the reciter modal. Starts the same native
     * align run as the Requests tab (``align_runs`` is server-side, so both
     * views show one run); the full stage-by-stage progress stays in Requests,
     * this cell shows a one-line summary while the run is live.
     */
    import { onDestroy } from 'svelte';
    import {
        fetchAlignStatus,
        retryAlign,
        startAlign,
        type AlignDevice,
    } from '../../../lib/api/admin-requests';
    import * as m from '../../../lib/paraglide/messages';
    import { can } from '../../../lib/stores/capabilities';
    import type { AlignRunStatus } from '../../../lib/types/generated/schemas';
    import { visiblePoll } from '../../../lib/utils/visible-poll';

    let { slug }: { slug: string } = $props();

    const POLL_MS = 15_000;
    const ACTIVE = new Set(['pending', 'running']);
    const DEVICES: AlignDevice[] = ['GPU', 'CPU'];
    const STAGE_LABEL: Record<AlignRunStatus['stage'], () => string> = {
        acquire: m.dashboard_align_stage_acquire,
        align: m.dashboard_align_stage_align,
        sidecars: m.dashboard_align_stage_sidecars,
        assemble: m.dashboard_align_stage_assemble,
        done: m.dashboard_align_stage_assemble,
    };

    const canAlign = can('intake.align');
    let run = $state<AlignRunStatus | null>(null);
    let loaded = $state(false);
    let busy = $state(false);
    let error = $state<string | null>(null);
    let stopPoll: (() => void) | null = null;

    const active = $derived(!!run && ACTIVE.has(run.status));
    const failed = $derived(run?.status === 'failed');

    /** Poll the run's status; stops by itself once the run is no longer live. */
    function poll(): void {
        stopPoll?.();
        stopPoll = visiblePoll<AlignRunStatus | null>({
            intervalMs: POLL_MS,
            fetcher: (signal) => fetchAlignStatus(slug, signal),
            onResult: (r) => {
                run = r;
                loaded = true;
                if (!r || !ACTIVE.has(r.status)) {
                    stopPoll?.();
                    stopPoll = null;
                }
            },
            onError: (e) => {
                loaded = true;
                error = (e as Error).message;
            },
        });
    }

    $effect(() => {
        if ($canAlign) poll();
    });
    onDestroy(() => stopPoll?.());

    async function act(e: MouseEvent, fn: () => Promise<AlignRunStatus>): Promise<void> {
        e.stopPropagation();
        if (busy) return;
        busy = true;
        error = null;
        try {
            run = await fn();
            poll();
        } catch (err) {
            error = (err as Error).message || m.dashboard_align_error_fallback();
        } finally {
            busy = false;
        }
    }

    function summary(r: AlignRunStatus): string {
        const stage = STAGE_LABEL[r.stage]();
        const total = r.chapters_total ?? 0;
        return r.stage === 'align' && total
            ? m.dashboard_align_running_chapters({ stage, done: r.chapters_done ?? 0, total })
            : m.dashboard_align_running({ stage });
    }
</script>

{#if $canAlign && loaded}
    <span class="align-ctl">
        {#if active && run}
            <span class="running" title={m.dashboard_align_running_title()}>{summary(run)}</span>
        {:else if failed}
            <span class="failed" title={run?.last_error ?? ''}>{m.dashboard_align_failed()}</span>
            <button
                type="button"
                class="lane"
                disabled={busy}
                onclick={(e) => act(e, () => retryAlign(slug))}
            >{m.dashboard_align_retry()}</button>
        {:else}
            {#each DEVICES as dev (dev)}
                <button
                    type="button"
                    class="lane"
                    disabled={busy}
                    title={dev === 'GPU' ? m.dashboard_align_gpu_title() : m.dashboard_align_cpu_title()}
                    onclick={(e) => act(e, () => startAlign(slug, 'Large', dev))}
                >{dev === 'GPU' ? m.dashboard_align_gpu() : m.dashboard_align_cpu()}</button>
            {/each}
        {/if}
        {#if error}<span class="err" role="alert" title={error}>{m.dashboard_align_error_short()}</span>{/if}
    </span>
{/if}

<style>
    .align-ctl {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        font-size: 11px;
    }
    .lane {
        background: transparent;
        border: 1px solid var(--border-default);
        color: var(--text-secondary);
        border-radius: 999px;
        padding: 2px 8px;
        font-size: 11px;
        cursor: pointer;
    }
    .lane:hover:not(:disabled) {
        border-color: var(--accent);
        color: var(--accent);
    }
    .lane:disabled {
        opacity: 0.55;
        cursor: wait;
    }
    .running {
        color: var(--text-muted);
        white-space: nowrap;
    }
    .failed,
    .err {
        color: var(--state-error-fg);
    }
</style>
