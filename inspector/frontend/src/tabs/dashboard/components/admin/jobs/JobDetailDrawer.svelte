<script lang="ts">
    /**
     * Unified job detail drawer — one record, every kind.
     *
     * Fetches ``GET /api/admin/jobs/detail`` for the clicked job and renders the
     * summary + kind-specific extras: TS settings + live log tail; batch / cut
     * ``members`` table; release ``version`` / ``external_uri``. Offers
     * "Open on HF" (the job page) and Cancel when the job is still running.
     */
    import Modal from '../../../../../lib/components/Modal.svelte';
    import { cancelReleaseJob, fetchJobDetail } from '../../../../../lib/api/admin-jobs';
    import type { JobRecord } from '../../../../../lib/types/generated/schemas';
    import { relativeTime } from '../../../../../lib/utils/relative-time';

    interface Props {
        kind: string;
        jobId: string;
        slug?: string | null;
        onClose: () => void;
        onChanged?: () => void;
    }
    let { kind, jobId, slug = null, onClose, onChanged }: Props = $props();

    const KIND_LABELS: Record<string, string> = {
        timestamps: 'Timestamps',
        hf_publish: 'HF publish',
        hf_publish_batch: 'HF publish (batch)',
        cut_release: 'GH release cut',
        refresh_catalog: 'Catalog refresh',
    };

    let rec = $state<JobRecord | null>(null);
    let loading = $state(true);
    let error = $state<string | null>(null);
    let canceling = $state(false);

    $effect(() => {
        const k = kind;
        const id = jobId;
        const s = slug;
        loading = true;
        error = null;
        const ctrl = new AbortController();
        void (async () => {
            try {
                rec = await fetchJobDetail(k, id, s, ctrl.signal);
            } catch (e) {
                if (!ctrl.signal.aborted) error = e instanceof Error ? e.message : 'Failed to load job';
            } finally {
                if (!ctrl.signal.aborted) loading = false;
            }
        })();
        return () => ctrl.abort();
    });

    function statusTone(s: string | null | undefined): 'ok' | 'fail' | 'run' {
        if (s === 'succeeded') return 'ok';
        if (s === 'failed') return 'fail';
        return 'run';
    }

    function fmtWhen(iso: string | null | undefined): string {
        if (!iso) return '—';
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
    }

    async function onCancel(): Promise<void> {
        if (canceling || !rec) return;
        if (!window.confirm('Cancel this job? In-progress work is lost.')) return;
        canceling = true;
        try {
            await cancelReleaseJob(jobId);
            onChanged?.();
            onClose();
        } catch (e) {
            error = e instanceof Error ? e.message : 'Cancel failed';
        } finally {
            canceling = false;
        }
    }
</script>

<Modal open size="wide" title="Job detail" on:close={onClose}>
    <div slot="header" class="jd-head">
        <h2 class="jd-title">{KIND_LABELS[kind] ?? kind}</h2>
        {#if rec}
            <span class="badge {statusTone(rec.status)}">{rec.status}</span>
        {/if}
    </div>

    {#if loading}
        <div class="muted">Loading job…</div>
    {:else if error}
        <div class="err">{error}</div>
    {:else if rec}
        <div class="jd-body">
            <dl class="kv">
                <dt>Job id</dt>
                <dd class="mono">{rec.job_id}</dd>
                {#if rec.slug}
                    <dt>Reciter</dt>
                    <dd class="mono">{rec.slug}</dd>
                {:else}
                    <dt>Scope</dt>
                    <dd>Global</dd>
                {/if}
                <dt>Started</dt>
                <dd>{fmtWhen(rec.started_at)}{rec.started_at ? ` · ${relativeTime(rec.started_at)}` : ''}</dd>
                <dt>Ended</dt>
                <dd>{fmtWhen(rec.ended_at)}</dd>
                {#if rec.launched_by}
                    <dt>Launched by</dt>
                    <dd class="mono">{rec.launched_by}</dd>
                {/if}
                {#if rec.version}
                    <dt>Version</dt>
                    <dd class="mono">{rec.version}</dd>
                {/if}
                {#if rec.error}
                    <dt>Error</dt>
                    <dd class="err-text">{rec.error}</dd>
                {/if}
            </dl>

            <div class="jd-actions">
                {#if rec.url}
                    <a class="hf-link" href={rec.url} target="_blank" rel="noopener noreferrer">Open on HF ↗</a>
                {/if}
                {#if rec.external_uri}
                    <a class="hf-link" href={rec.external_uri} target="_blank" rel="noopener noreferrer">Open output ↗</a>
                {/if}
                {#if rec.status === 'running'}
                    <button class="cancel" type="button" disabled={canceling} onclick={onCancel}>
                        {canceling ? 'Cancelling…' : 'Cancel job'}
                    </button>
                {/if}
            </div>

            {#if rec.settings}
                <div class="section">
                    <h3>Settings</h3>
                    <div class="mono small">
                        {#if rec.settings.chapters?.length}chapters [{rec.settings.chapters.join(', ')}]{:else}full reciter{/if}
                    </div>
                </div>
            {/if}

            {#if rec.members && rec.members.length}
                <div class="section">
                    <h3>Members ({rec.members.length})</h3>
                    <ul class="members">
                        {#each rec.members as m (m.slug)}
                            <li>
                                <span class="badge {statusTone(m.status)}">{m.status ?? '—'}</span>
                                <span class="mono">{m.slug}</span>
                                {#if m.change_kind}<span class="small faint">{m.change_kind}</span>{/if}
                                {#if m.version}<span class="small faint mono">{m.version}</span>{/if}
                                {#if m.external_uri}
                                    <a class="hf-link small" href={m.external_uri} target="_blank" rel="noopener noreferrer">↗</a>
                                {/if}
                                {#if m.error}<span class="small err-text">{m.error}</span>{/if}
                            </li>
                        {/each}
                    </ul>
                </div>
            {/if}

            {#if rec.logs && rec.logs.length}
                <div class="section">
                    <h3>Logs{rec.log_truncated ? ' (truncated)' : ''}</h3>
                    <pre class="logs">{rec.logs.join('\n')}</pre>
                </div>
            {/if}
        </div>
    {/if}
</Modal>

<style>
    .jd-head { display: flex; align-items: center; gap: var(--s-3); flex: 1; min-width: 0; }
    .jd-title { font-size: var(--fs-h3); font-weight: 500; color: var(--text-primary); margin: 0; white-space: nowrap; }
    .jd-body { display: flex; flex-direction: column; gap: var(--s-4); padding: var(--s-2) 0; }
    .muted { font-size: var(--fs-meta); color: var(--text-faint); padding: var(--s-3) 0; }
    .err { color: var(--state-error-fg); padding: var(--s-2) 0; }
    .err-text { color: var(--state-error-fg); }

    .kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px var(--s-4); margin: 0; }
    .kv dt { color: var(--text-faint); font-size: var(--fs-meta); }
    .kv dd { margin: 0; color: var(--text-secondary); font-size: var(--fs-body); }
    .mono { font-family: var(--font-mono); }
    .small { font-size: 10.5px; }
    .faint { color: var(--text-faint); }

    .jd-actions { display: flex; align-items: center; gap: var(--s-3); }
    .hf-link {
        font-family: var(--font-mono); font-size: 10.5px; font-weight: 600;
        color: var(--accent-strong); text-decoration: none;
    }
    .hf-link:hover { text-decoration: underline; }
    .cancel {
        margin-left: auto;
        font-family: var(--font-mono); font-size: 10.5px; font-weight: 600;
        background: transparent; color: var(--state-error-fg);
        border: 1px solid oklch(0.86 0.130 75 / 0.4); border-radius: var(--r-1);
        padding: 3px 10px; cursor: pointer;
    }
    .cancel:hover:not(:disabled) { background: var(--state-error-bg); }
    .cancel:disabled { opacity: 0.6; cursor: not-allowed; }

    .section { display: flex; flex-direction: column; gap: var(--s-2); }
    .section h3 { font-size: var(--fs-body); font-weight: 500; color: var(--text-primary); margin: 0; }
    .members { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
    .members li { display: flex; align-items: center; gap: var(--s-2); font-size: 11px; color: var(--text-secondary); }

    .badge {
        font-family: var(--font-mono); font-size: 9.5px; text-transform: uppercase;
        letter-spacing: 0.04em; padding: 2px 7px; border-radius: 999px;
        background: var(--panel-2); color: var(--text-secondary);
    }
    .badge.ok { color: var(--state-published-fg); background: var(--state-published-bg); }
    .badge.fail { color: var(--state-error-fg); background: var(--state-error-bg); }
    .badge.run { color: var(--accent-strong); background: var(--accent-tint-soft); }

    .logs {
        margin: 0; max-height: 320px; overflow: auto;
        background: var(--canvas-inset); border: 1px solid var(--border-quiet);
        border-radius: var(--r-1); padding: var(--s-2) var(--s-3);
        font-family: var(--font-mono); font-size: 11px; line-height: 1.5;
        color: var(--text-secondary); white-space: pre-wrap; word-break: break-word;
    }
</style>
