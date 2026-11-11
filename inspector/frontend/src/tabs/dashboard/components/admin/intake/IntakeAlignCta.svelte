<script lang="ts">
    /**
     * GPU/CPU lane toggle + Align button for a reviewed intake plan, with the
     * shared maintainer budget. Mirrors the slug-row Align CTA in
     * RequestsCompartment.
     */
    import type { AlignDevice } from '../../../../../lib/api/admin-requests';
    import type { AlignQuota } from '../../../../../lib/types/generated/schemas';

    interface Props {
        quota: AlignQuota | null;
        busy: boolean;
        /** Why Align is unavailable (unsaved edits, blocking errors, …); null = ready. */
        blockedReason: string | null;
        onAlign: (_device: AlignDevice) => void;
    }
    let { quota, busy, blockedReason, onAlign }: Props = $props();

    const ALIGN_DEVICES: AlignDevice[] = ['GPU', 'CPU'];
    const MS_PER_MINUTE = 60_000;
    let device = $state<AlignDevice>('GPU');

    const laneBlocked = $derived<Record<AlignDevice, boolean>>({
        GPU: !!quota && !quota.exempt && (quota.gpu_used ?? 0) >= (quota.gpu_limit ?? 0),
        CPU: !!quota && !quota.exempt && (quota.cpu_running ?? 0) >= (quota.cpu_limit ?? 0),
    });

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

    const disabled = $derived(busy || !!blockedReason || laneBlocked[device]);
</script>

<div class="align-block">
    <div class="align-cta">
        <span class="align-hint">
            {blockedReason ??
                'Adds the reciter to the catalog, then downloads the files, splits combined ones and aligns every chapter.'}
        </span>
        <div class="align-go">
            <div class="lane" role="group" aria-label="Alignment lane">
                {#each ALIGN_DEVICES as dev (dev)}
                    <button
                        type="button"
                        class="lane-opt"
                        class:on={device === dev}
                        class:blocked={laneBlocked[dev]}
                        aria-pressed={device === dev}
                        disabled={busy}
                        title={laneTitle(dev)}
                        onclick={() => (device = dev)}
                    >{dev}</button>
                {/each}
            </div>
            <button
                class="btn primary"
                {disabled}
                title={blockedReason ?? (laneBlocked[device] ? laneTitle(device) : undefined)}
                onclick={() => onAlign(device)}
            >{busy ? 'Starting…' : 'Align'}</button>
        </div>
    </div>
    {#if quota}
        <p class="align-quota">
            {#if quota.exempt}
                <span>No limits for you</span> ·
            {/if}
            <span class:spent={laneBlocked.GPU}>
                GPU {quota.gpu_used}/{quota.gpu_limit} in 24 h{#if quota.gpu_resets_at}
                    &nbsp;(next frees in {untilLabel(quota.gpu_resets_at)}){/if}
            </span>
            ·
            <span class:spent={laneBlocked.CPU}>CPU {quota.cpu_running}/{quota.cpu_limit} running</span>
            · shared by all maintainers
        </p>
        {#if laneBlocked[device]}
            <p class="align-blocked">{laneTitle(device)}.</p>
        {/if}
    {/if}
</div>

<style>
    .align-block { display: flex; flex-direction: column; gap: var(--s-2); }
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
    .btn { padding: 6px 14px; border-radius: var(--r-2); font: 500 var(--fs-meta)/1 var(--font-sans); border: 1px solid var(--border-default); background: transparent; color: var(--text-secondary); cursor: pointer; transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast); }
    .btn:hover { color: var(--text-primary); border-color: var(--border-strong); }
    .btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .btn.primary { color: var(--accent); border-color: var(--accent); }
    .btn.primary:hover:not(:disabled) { background: var(--accent-tint); }
</style>
