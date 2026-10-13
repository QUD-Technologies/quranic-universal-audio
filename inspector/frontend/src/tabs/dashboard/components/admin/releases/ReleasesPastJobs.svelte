<script lang="ts">
    /**
     * Past timestamps jobs for one reciter — history list + live log tail.
     *
     * Lifted from the retired Reviews timestamps drawer. Shows the persisted
     * job records (newest first); clicking one loads its full logs read-only.
     * When a job is in flight (``activeJobId`` from a just-launched gen/regen,
     * or a running record), polls ``fetchJobStatus`` (Page-Visibility-aware)
     * and streams the log tail; on terminal success it asks the compartment to
     * refetch so the row re-buckets.
     */
    import {
        cancelJob,
        fetchJobRecord,
        fetchJobStatus,
        fetchTsJobRecords,
    } from '../../../../../lib/api/admin-reviews';
    import { releasesStore } from '../../../../../lib/stores/releases.svelte';
    import type { TsJobRecord } from '../../../../../lib/types/generated/schemas';
    import { visiblePoll } from '../../../../../lib/utils/visible-poll';

    interface Props {
        slug: string;
        /** Job id to poll live (set right after a launch); null otherwise. */
        activeJobId?: string | null;
    }
    let { slug, activeJobId = null }: Props = $props();

    let records = $state<TsJobRecord[]>([]);
    let loading = $state(true);
    let selectedId = $state<string | null>(null);
    let viewLogs = $state<string[]>([]);
    let liveStatus = $state<string | null>(null);
    let canceling = $state(false);

    const TERMINAL = new Set(['succeeded', 'completed', 'failed', 'error', 'timed-out', 'canceled']);

    async function loadHistory(): Promise<void> {
        try {
            records = await fetchTsJobRecords(slug);
        } catch {
            records = [];
        } finally {
            loading = false;
        }
    }

    // Load the history on mount / slug change.
    $effect(() => {
        slug;
        loading = true;
        void loadHistory();
    });

    // Default the open record to the active (launched) job, else the newest.
    $effect(() => {
        if (selectedId === null) {
            selectedId = activeJobId ?? records[0]?.job_id ?? null;
        }
    });

    // Live poll while the selected job is the active one and not yet terminal.
    $effect(() => {
        const jobId = activeJobId;
        if (!jobId) return;
        let stop = false;
        const teardown = visiblePoll<{ status: string; logs: string[] }>({
            intervalMs: 2500,
            fetcher: async (signal) => {
                const s = await fetchJobStatus(slug, jobId, signal);
                return { status: s.status, logs: s.logs };
            },
            onResult: ({ status, logs }) => {
                if (stop) return;
                liveStatus = status;
                if (selectedId === jobId) viewLogs = logs;
                if (TERMINAL.has(status)) {
                    stop = true;
                    teardown();
                    void loadHistory();
                    // Success re-buckets the row (released / stale-stamped) —
                    // ask the compartment to refetch.
                    releasesStore.requestRefresh();
                }
            },
            onError: () => {},
        });
        return () => { stop = true; teardown(); };
    });

    async function openRecord(rec: TsJobRecord): Promise<void> {
        selectedId = rec.job_id;
        liveStatus = null;
        if (rec.job_id === activeJobId) {
            viewLogs = rec.logs ?? [];
            return;
        }
        const full = await fetchJobRecord(slug, rec.job_id);
        viewLogs = full?.logs ?? rec.logs ?? [];
    }

    async function onCancel(jobId: string): Promise<void> {
        if (canceling) return;
        if (!window.confirm('Cancel this job? In-progress work is lost.')) return;
        canceling = true;
        try {
            await cancelJob(slug, jobId);
            await loadHistory();
            releasesStore.requestRefresh();
        } catch {
            /* surfaced by the next poll / refetch */
        } finally {
            canceling = false;
        }
    }

    function statusTone(s: string | null | undefined): 'ok' | 'fail' | 'run' {
        if (!s) return 'run';
        if (s === 'succeeded' || s === 'completed') return 'ok';
        if (TERMINAL.has(s)) return 'fail';
        return 'run';
    }

    function fmtWhen(iso: string | null | undefined): string {
        if (!iso) return '';
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? '' : d.toLocaleString();
    }
</script>

<div class="jobs">
    {#if loading}
        <div class="muted">Loading job history…</div>
    {:else if records.length === 0}
        <div class="muted">No timestamps jobs have run for this reciter yet.</div>
    {:else}
        <ul class="hist">
            {#each records as rec (rec.job_id)}
                {@const tone = statusTone(rec.job_id === activeJobId ? liveStatus ?? rec.status : rec.status)}
                <li>
                    <button
                        class="hist-row"
                        class:active={selectedId === rec.job_id}
                        type="button"
                        onclick={() => openRecord(rec)}
                    >
                        <span class="badge {tone}">{rec.job_id === activeJobId ? liveStatus ?? rec.status : rec.status}</span>
                        <span class="job-id">{rec.job_id.slice(0, 8)}</span>
                        <span class="meta">{rec.settings?.chapters?.length ? `chapters [${rec.settings.chapters.join(', ')}]` : 'full reciter'}</span>
                        <span class="when">{fmtWhen(rec.started_at)}</span>
                    </button>
                    {#if rec.job_id === activeJobId && statusTone(liveStatus) === 'run'}
                        <button class="cancel" type="button" disabled={canceling}
                            onclick={() => onCancel(rec.job_id)}>
                            {canceling ? 'Cancelling…' : 'Cancel'}
                        </button>
                    {/if}
                </li>
            {/each}
        </ul>

        {#if selectedId}
            {@const sel = records.find((r) => r.job_id === selectedId)}
            <div class="log-pane">
                {#if sel?.url}
                    <a class="hf-link" href={sel.url} target="_blank" rel="noopener noreferrer">Open on HF ↗</a>
                {/if}
                <pre class="logs">{viewLogs.length ? viewLogs.join('\n') : 'No logs.'}</pre>
            </div>
        {/if}
    {/if}
</div>

<style>
    .jobs { display: flex; flex-direction: column; gap: var(--s-2); padding: var(--s-2) var(--s-1); }
    .muted { font-size: var(--fs-meta); color: var(--text-faint); padding: var(--s-2) 0; }
    .hist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
    .hist li { display: flex; align-items: center; gap: var(--s-2); }
    .hist-row {
        flex: 1;
        display: inline-flex;
        align-items: center;
        gap: var(--s-3);
        background: transparent;
        border: 1px solid transparent;
        border-radius: var(--r-1);
        padding: 4px 8px;
        font: inherit;
        cursor: pointer;
        text-align: left;
    }
    .hist-row:hover { background: var(--panel); }
    .hist-row.active { border-color: var(--accent-tint); background: var(--panel); }
    .badge {
        font-family: var(--font-mono);
        font-size: 9.5px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        padding: 2px 7px;
        border-radius: 999px;
        background: var(--panel-2);
        color: var(--text-secondary);
    }
    .badge.ok { color: var(--state-published-fg); background: var(--state-published-bg); }
    .badge.fail { color: var(--state-error-fg); background: var(--state-error-bg); }
    .badge.run { color: var(--accent-strong); background: var(--accent-tint-soft); }
    .job-id { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }
    .meta { font-family: var(--font-mono); font-size: 10.5px; color: var(--text-faint); }
    .when {
        margin-left: auto;
        font-family: var(--font-mono);
        font-size: 10px;
        color: var(--text-faint);
        font-variant-numeric: tabular-nums;
    }
    .cancel {
        font-family: var(--font-mono);
        font-size: 10px;
        font-weight: 600;
        background: transparent;
        color: var(--state-error-fg);
        border: 1px solid oklch(0.86 0.130 75 / 0.4);
        border-radius: var(--r-1);
        padding: 2px 8px;
        cursor: pointer;
    }
    .cancel:hover:not(:disabled) { background: var(--state-error-bg); }
    .cancel:disabled { opacity: 0.6; cursor: not-allowed; }

    .log-pane { display: flex; flex-direction: column; gap: 4px; }
    .hf-link {
        align-self: flex-start;
        font-family: var(--font-mono);
        font-size: 10px;
        font-weight: 600;
        color: var(--accent-strong);
        text-decoration: none;
    }
    .hf-link:hover { text-decoration: underline; }
    .logs {
        margin: 0;
        max-height: 320px;
        overflow: auto;
        background: var(--canvas-inset);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        padding: var(--s-2) var(--s-3);
        font-family: var(--font-mono);
        font-size: 11px;
        line-height: 1.5;
        color: var(--text-secondary);
        white-space: pre-wrap;
        word-break: break-word;
    }
</style>
