<script lang="ts">
    /**
     * Owner review of a slugless intake request: enumerate the submitted
     * source into files, choose which to include and check the proposed
     * catalog identity, save, then Align (mint the slug + start its run —
     * the aligner detects which surahs each file holds).
     */
    import {
        alignIntake,
        buildIntakePlan,
        fetchIntakePlan,
        saveIntakePlan,
        type AlignDevice,
    } from '../../../../../lib/api/admin-requests';
    import type {
        AlignQuota,
        IntakePlanView,
        PlanIdentity,
    } from '../../../../../lib/types/generated/schemas';
    import IntakeAlignCta from './IntakeAlignCta.svelte';
    import IntakeIdentityForm from './IntakeIdentityForm.svelte';
    import IntakePlanEntries from './IntakePlanEntries.svelte';
    import IntakePlanSummary from './IntakePlanSummary.svelte';
    import {
        cleanIdentity,
        computeCoverage,
        identityEqual,
        includesFrom,
        totalDuration,
    } from './plan-utils';

    interface Props {
        requestId: string;
        kind: string;
        canAlign: boolean;
        quota: AlignQuota | null;
        /** The intake was minted into a slug — refetch the request list. */
        onMinted: () => void;
    }
    let { requestId, kind, canAlign, quota, onMinted }: Props = $props();

    const POLL_MS = 3_000;
    const MINTED_NOTICE_MS = 1_500;

    let plan = $state<IntakePlanView | null>(null);
    let loading = $state(true);
    /** Bumped on every adopted server view so the identity form re-seeds. */
    let version = $state(0);
    let includes = $state<Record<string, boolean>>({});
    let identity = $state<PlanIdentity>({});
    let busy = $state<'build' | 'save' | 'align' | null>(null);
    let error = $state<string | null>(null);
    let notice = $state<string | null>(null);
    let alignFailed = $state(false);

    function adopt(view: IntakePlanView | null): void {
        plan = view;
        includes = includesFrom(view?.entries ?? []);
        identity = { ...(view?.identity ?? {}) };
        version++;
    }

    function message(e: unknown, fallback: string): string {
        return (e as Error)?.message || fallback;
    }

    // Initial load (and on request change).
    $effect(() => {
        const id = requestId;
        const ctrl = new AbortController();
        loading = true;
        error = null;
        fetchIntakePlan(id, ctrl.signal)
            .then(adopt)
            .catch((e) => {
                if (!ctrl.signal.aborted) error = message(e, 'Failed to load the plan.');
            })
            .finally(() => {
                if (!ctrl.signal.aborted) loading = false;
            });
        return () => ctrl.abort();
    });

    // Poll while the background enumeration runs.
    const enumerating = $derived(plan?.status === 'enumerating');
    $effect(() => {
        if (!enumerating) return;
        const id = requestId;
        const ctrl = new AbortController();
        const timer = setInterval(async () => {
            try {
                const view = await fetchIntakePlan(id, ctrl.signal);
                if (!ctrl.signal.aborted && view) adopt(view);
            } catch (e) {
                if (!ctrl.signal.aborted) error = message(e, 'Failed to refresh the plan.');
            }
        }, POLL_MS);
        return () => {
            clearInterval(timer);
            ctrl.abort();
        };
    });

    const entries = $derived(plan?.entries ?? []);
    const isLinks = $derived(plan?.host === 'links');
    const included = $derived(entries.filter((e) => includes[e.key]));
    const totalSec = $derived(totalDuration(included));
    const coverage = $derived(isLinks ? computeCoverage(included.map((e) => e.chapters ?? [])) : null);
    const dirty = $derived(
        !!plan &&
            (entries.some((e) => includes[e.key] !== (e.include ?? true)) ||
                !identityEqual(identity, plan.identity ?? {})),
    );
    const blockingErrors = $derived(plan?.errors ?? []);

    const blockedReason = $derived.by<string | null>(() => {
        if (dirty) return 'Save your edits first — Align uses the saved plan.';
        if (blockingErrors.length > 0) return 'Resolve the errors above before aligning.';
        return null;
    });

    async function build(): Promise<void> {
        if (busy) return;
        if (plan?.status === 'ready' && !confirm('Rebuild the plan from the source? Your edits are discarded.')) {
            return;
        }
        busy = 'build';
        error = null;
        notice = null;
        try {
            adopt(await buildIntakePlan(requestId));
        } catch (e) {
            error = message(e, 'Could not start enumerating the source.');
        } finally {
            busy = null;
        }
    }

    async function save(): Promise<void> {
        if (busy || !plan) return;
        busy = 'save';
        error = null;
        notice = null;
        try {
            const body = {
                entries: entries.map((e) => ({ key: e.key, include: !!includes[e.key] })),
                identity: cleanIdentity(identity),
            };
            adopt(await saveIntakePlan(requestId, body));
        } catch (e) {
            error = message(e, 'Saving the plan failed.');
        } finally {
            busy = null;
        }
    }

    async function align(device: AlignDevice): Promise<void> {
        if (busy || blockedReason) return;
        busy = 'align';
        error = null;
        notice = null;
        try {
            const r = await alignIntake(requestId, device);
            if (r.align_started === false || r.align_error) {
                alignFailed = true;
                notice = `Minted as ${r.slug}; alignment did not start: ${r.align_error ?? 'unknown reason'}. Use Align on the new row.`;
            } else {
                notice = `Minted ${r.slug} — alignment started.`;
                setTimeout(onMinted, MINTED_NOTICE_MS);
            }
        } catch (e) {
            error = message(e, 'Align failed.');
        } finally {
            busy = null;
        }
    }
</script>

<section class="plan" aria-label="Intake plan">
    <h4>Intake plan</h4>

    {#if loading}
        <p class="muted">Loading plan…</p>
    {:else if !plan}
        <div class="row">
            <p class="muted">
                Lists the playlist / folder / links into files and proposes the catalog identity.
                Nothing is downloaded yet; surahs are detected from the audio when aligning.
            </p>
            <button class="btn primary" disabled={!!busy} onclick={build}>
                {busy === 'build' ? 'Starting…' : 'Build plan'}
            </button>
        </div>
    {:else if plan.status === 'enumerating'}
        <p class="muted progress">Listing the source…</p>
    {:else if plan.status === 'failed'}
        <div class="row">
            <p class="action-error">{plan.error ?? 'Enumerating the source failed.'}</p>
            <button class="btn" disabled={!!busy} onclick={build}>
                {busy === 'build' ? 'Starting…' : 'Rebuild'}
            </button>
        </div>
    {:else}
        <IntakePlanSummary
            {plan}
            fileCount={entries.length}
            includedCount={included.length}
            {totalSec}
            {coverage}
            rebuilding={busy === 'build'}
            disabled={!!busy}
            onRebuild={build}
        />
        {#key version}
            <IntakeIdentityForm
                bind:identity
                {kind}
                channelOptions={plan.channel_options ?? []}
                sourceOptions={plan.source_options ?? []}
                disabled={!!busy}
            />
        {/key}
        <IntakePlanEntries
            {entries}
            {includes}
            showChapters={isLinks}
            disabled={!!busy}
            onToggle={(key, on) => (includes[key] = on)}
        />

        <div class="foot">
            <span class="save-state">
                {#if dirty}
                    Unsaved edits
                {:else}
                    Saved
                {/if}
            </span>
            <button class="btn" disabled={!dirty || !!busy} onclick={save}>
                {busy === 'save' ? 'Saving…' : 'Save'}
            </button>
        </div>

        {#if canAlign}
            <IntakeAlignCta {quota} busy={busy === 'align'} {blockedReason} onAlign={align} />
        {:else}
            <p class="muted">Aligning needs the align permission.</p>
        {/if}
    {/if}

    {#if notice}
        <div class="row">
            <p class="notice" class:warn={alignFailed}>{notice}</p>
            {#if alignFailed}
                <button class="btn tiny" onclick={onMinted}>Refresh list</button>
            {/if}
        </div>
    {/if}
    {#if error}
        <p class="action-error">{error}</p>
    {/if}
</section>

<style>
    .plan { display: flex; flex-direction: column; gap: var(--s-3); margin-top: var(--s-4); padding-top: var(--s-3); border-top: 1px solid var(--border-quiet); }
    h4 { margin: 0; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 500; }
    .row { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }
    .muted { margin: 0; font-size: var(--fs-meta); color: var(--text-muted); line-height: var(--lh-normal); }
    .progress::after { content: ''; display: inline-block; width: 6px; height: 6px; margin-left: 6px; border-radius: 50%; background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
    @keyframes pulse { 50% { opacity: 0.25; } }
    .action-error { margin: 0; font-size: var(--fs-meta); color: var(--state-error-fg); }
    .notice { margin: 0; font-size: var(--fs-meta); color: var(--state-published-fg); line-height: var(--lh-normal); }
    .notice.warn { color: var(--state-warn-fg); }
    .foot { display: flex; align-items: center; justify-content: flex-end; gap: var(--s-3); }
    .save-state { font-size: var(--fs-meta); color: var(--text-faint); }
    .btn { padding: 6px 14px; border-radius: var(--r-2); font: 500 var(--fs-meta)/1 var(--font-sans); border: 1px solid var(--border-default); background: transparent; color: var(--text-secondary); cursor: pointer; flex-shrink: 0; transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast); }
    .btn:hover { color: var(--text-primary); border-color: var(--border-strong); }
    .btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .btn.primary { color: var(--accent); border-color: var(--accent); }
    .btn.primary:hover:not(:disabled) { background: var(--accent-tint); }
    .btn.tiny { padding: 3px 9px; font-size: 10.5px; }
    @media (prefers-reduced-motion: reduce) {
        .progress::after { animation: none; }
    }
</style>
