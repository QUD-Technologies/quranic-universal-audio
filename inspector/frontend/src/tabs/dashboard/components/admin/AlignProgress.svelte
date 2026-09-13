<script lang="ts">
    /**
     * Native align pipeline card for one slug row in the Requests queue.
     * Renders the four stage chips + the live detail the backend keeps for
     * the running stage, and the Retry / Cancel controls. The parent owns
     * the calls; this component only reports intent via callback props.
     */
    import type { AlignRunStatus } from '../../../../lib/types/generated/schemas';
    import { relativeTime } from '../../../../lib/utils/relative-time';

    interface Props {
        run: AlignRunStatus;
        busy?: boolean;
        onRetry: () => void;
        onCancel: () => void;
    }
    let { run, busy = false, onRetry, onCancel }: Props = $props();

    const STAGES = [
        { key: 'acquire', label: 'Acquire audio' },
        { key: 'align', label: 'Align' },
        { key: 'sidecars', label: 'Sidecars' },
        { key: 'assemble', label: 'Assemble' },
    ] as const;

    const stageIndex = $derived(
        run.stage === 'done' ? STAGES.length : STAGES.findIndex((s) => s.key === run.stage),
    );
    const active = $derived(run.status === 'pending' || run.status === 'running');

    function chipState(i: number): 'done' | 'current' | 'todo' {
        if (i < stageIndex) return 'done';
        if (i === stageIndex) return run.status === 'succeeded' ? 'done' : 'current';
        return 'todo';
    }

    function detailLine(): string {
        const d = (run.detail ?? {}) as Record<string, unknown>;
        if (run.stage === 'acquire') {
            return d.job_status ? `HF job ${String(d.job_status)}` : 'launching HF job…';
        }
        if (run.stage === 'align') {
            const ch = d.chapter ? `chapter ${String(d.chapter)}` : '';
            const st = d.aligner_stage ? String(d.aligner_stage).replace(/_/g, ' ') : '';
            // The lane rides in the head meta, not here.
            return [ch, st].filter(Boolean).join(' ') || 'starting…';
        }
        if (run.stage === 'sidecars') {
            const st = d.sidecar_stage ? String(d.sidecar_stage).replace(/_/g, ' ') : 'submitting';
            return st;
        }
        if (run.stage === 'assemble') return 'building artifacts…';
        return '';
    }
</script>

<div class="align" class:failed={run.status === 'failed'} class:done={run.status === 'succeeded'}>
    <div class="align-head">
        <span class="align-title">
            {#if run.status === 'succeeded'}Aligned
            {:else if run.status === 'failed'}Alignment failed
            {:else if run.status === 'canceled'}Alignment canceled
            {:else}Aligning
            {/if}
            <span class="align-meta">
                · {run.model_name ?? 'Large'} · {String(run.detail?.device ?? run.device ?? 'GPU')} · attempt {run.attempt}
                {#if run.started_at}· started {relativeTime(run.started_at)}{/if}
            </span>
        </span>
        {#if run.status === 'failed'}
            <div class="align-actions">
                <button class="btn tiny" disabled={busy} onclick={onRetry}>Retry</button>
                <button class="btn tiny danger" disabled={busy} onclick={onCancel}>Cancel run</button>
            </div>
        {:else if active}
            <div class="align-actions">
                <button class="btn tiny danger" disabled={busy} onclick={onCancel}>Cancel</button>
            </div>
        {/if}
    </div>

    <ol class="stages">
        {#each STAGES as s, i (s.key)}
            <li class="stage {chipState(i)}">
                <span class="dot"></span>
                <span class="stage-label">{s.label}</span>
                {#if s.key === 'align' && chipState(i) !== 'todo'}
                    <span class="stage-count">{run.chapters_done}/{run.chapters_total}</span>
                {/if}
            </li>
        {/each}
    </ol>

    {#if active}
        <p class="align-detail">{detailLine()}</p>
    {/if}
    {#if run.acquire_job_url && run.stage === 'acquire'}
        <a class="job-link" href={run.acquire_job_url} target="_blank" rel="noopener noreferrer">HF job</a>
    {/if}
    {#if run.last_error}
        <p class="align-error">{run.last_error}</p>
    {/if}
</div>

<style>
    .align { display: flex; flex-direction: column; gap: var(--s-2); padding: var(--s-3); border: 1px solid var(--border-quiet); border-radius: var(--r-2); background: var(--canvas-inset); }
    .align.failed { border-color: var(--state-error-fg); }
    .align.done { border-color: var(--state-published-fg); }
    .align-head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }
    .align-title { font-size: var(--fs-meta); font-weight: 600; color: var(--text-primary); }
    .align-meta { font-weight: 400; color: var(--text-faint); }
    .align-actions { display: inline-flex; gap: var(--s-2); }
    .stages { display: flex; flex-wrap: wrap; gap: var(--s-3); margin: 0; padding: 0; list-style: none; }
    .stage { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-faint); }
    .stage .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border-default); }
    .stage.done { color: var(--text-secondary); }
    .stage.done .dot { background: var(--state-published-fg); }
    .stage.current { color: var(--text-primary); font-weight: 600; }
    .stage.current .dot { background: var(--accent); animation: pulse 1.4s ease-in-out infinite; }
    .stage-count { font-weight: 400; color: var(--text-faint); font-variant-numeric: tabular-nums; }
    .align-detail { margin: 0; font-size: 11px; color: var(--text-secondary); }
    .job-link { font-size: 11px; color: var(--accent); }
    .align-error { margin: 0; font-size: 11px; color: var(--state-error-fg); white-space: pre-wrap; word-break: break-word; }
    .btn { padding: 6px 14px; border-radius: var(--r-2); font: 500 var(--fs-meta)/1 var(--font-sans); border: 1px solid var(--border-default); background: transparent; color: var(--text-secondary); cursor: pointer; }
    .btn:hover { color: var(--text-primary); border-color: var(--border-strong); }
    .btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .btn.tiny { padding: 3px 9px; font-size: 10.5px; }
    .btn.danger { color: var(--state-error-fg); }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
</style>
