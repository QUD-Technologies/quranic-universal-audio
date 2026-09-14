<script lang="ts">
    /**
     * Owner-only "Timestamps generation" settings card — the single shared
     * source for the TS-gen knobs (beam/model/workers/batch_size/
     * download_workers/padding/method) that every generation surface reads:
     * the manual launch form, the auto-gen / stale-regen automations, and the
     * HF job through them.
     *
     * One collapsible card mirroring the Automation card: a header with a
     * disclosure, the main beam field + a model dropdown, and an Advanced
     * collapsible for the rest. Edits are local; a single explicit Save persists
     * the whole defaults blob (the server echoes the fresh blob back). Mounted
     * only for holders of ``release.manage_automation``.
     */
    import { onMount } from 'svelte';

    import {
        type TsGenerationDefaultsBody,
        fetchTsGenerationDefaults,
        saveTsGenerationDefaults,
    } from '../../../../../lib/api/admin-releases';
    import { fetchAlignerModels, type AlignerModel } from '../../../../../lib/api/admin-reviews';

    // Selectable acoustic models (the store catalog) for the default-model picker.
    let alignerModels = $state<AlignerModel[]>([]);
    $effect(() => {
        fetchAlignerModels()
            .then((ms) => (alignerModels = ms))
            .catch(() => {});
    });

    // The codegen marks every defaulted field optional. The server always sends a
    // complete blob; we normalize into a fully-required local shape on load so the
    // editor bindings stay type-safe and the dirty check is apples-to-apples.
    interface Draft {
        beam: number;
        aligner_model: string | null;
        workers: number | null;
        batch_size: number | null;
        download_workers: number | null;
        padding: string | null;
        method: string | null;
    }

    function normalize(d: TsGenerationDefaultsBody): Draft {
        return {
            beam: d.beam ?? 50,
            aligner_model: d.aligner_model ?? null,
            workers: d.workers ?? null,
            batch_size: d.batch_size ?? null,
            download_workers: d.download_workers ?? null,
            padding: d.padding ?? null,
            method: d.method ?? null,
        };
    }

    /** Strip empty strings back to null so blank Advanced inputs cede to the
     *  job's own DEFAULT_* rather than persisting an empty override. */
    function toBody(d: Draft): TsGenerationDefaultsBody {
        return {
            beam: d.beam,
            aligner_model: d.aligner_model || null,
            workers: d.workers ?? null,
            batch_size: d.batch_size ?? null,
            download_workers: d.download_workers ?? null,
            padding: d.padding?.trim() || null,
            method: d.method?.trim() || null,
        };
    }

    let draft = $state<Draft | null>(null);
    let baseline = $state<Draft | null>(null);
    let loading = $state(true);
    let error = $state<string | null>(null);
    let saving = $state(false);
    let saveError = $state<string | null>(null);
    let open = $state(false);
    let advancedOpen = $state(false);

    const dirty = $derived(
        !!draft && !!baseline && JSON.stringify(draft) !== JSON.stringify(baseline),
    );

    onMount(load);

    function adopt(d: TsGenerationDefaultsBody): void {
        const norm = normalize(d);
        baseline = norm;
        draft = structuredClone(norm);
    }

    async function load(): Promise<void> {
        loading = true;
        error = null;
        try {
            adopt(await fetchTsGenerationDefaults());
        } catch (e) {
            error = (e as Error).message ?? 'Failed to load settings';
        } finally {
            loading = false;
        }
    }

    async function save(): Promise<void> {
        if (!draft || saving) return;
        saving = true;
        saveError = null;
        try {
            adopt(await saveTsGenerationDefaults(toBody(draft)));
        } catch (e) {
            saveError = (e as Error).message ?? 'Save failed';
        } finally {
            saving = false;
        }
    }

    function discard(): void {
        if (baseline) draft = structuredClone(baseline);
        saveError = null;
    }
</script>

{#if loading}
    <div class="ts-card" aria-busy="true">
        <div class="head static">
            <span class="title">Timestamps generation</span><span class="sub">Loading…</span>
        </div>
    </div>
{:else if error}
    <div class="ts-card">
        <div class="head static">
            <span class="title">Timestamps generation</span>
            <span class="sub err" role="alert">{error}</span>
            <button class="retry" type="button" onclick={load}>Retry</button>
        </div>
    </div>
{:else if draft}
    <section class="ts-card" class:open>
        <button class="head" type="button" aria-expanded={open} onclick={() => (open = !open)}>
            <svg class="chev" class:open viewBox="0 0 16 16" aria-hidden="true">
                <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" />
            </svg>
            <span class="title">Timestamps generation</span>
            <span class="sub">Shared defaults · beam {draft.beam}</span>
            {#if dirty}<span class="dirty-dot" title="Unsaved changes" aria-label="Unsaved changes"></span>{/if}
            <span class="spacer"></span>
            <span class="hint">Owner-only</span>
        </button>

        <div class="body-wrap" class:open>
            <div class="body-inner" inert={!open}>
                <div class="row">
                    <p class="desc">
                        The default knobs for every timestamps run — the manual launch form, the
                        auto-generate and stale-regenerate automations, and the alignment job. A
                        form value or per-automation override still wins; anything left unset falls
                        back to these.
                    </p>
                    <div class="line">
                        <label class="field">
                            <span class="lbl">beam</span>
                            <input type="number" min="1" bind:value={draft.beam} />
                        </label>
                        {#if alignerModels.length > 1}
                            <label class="field wide">
                                <span class="lbl">model</span>
                                <select bind:value={draft.aligner_model}>
                                    <option value={null}>store default</option>
                                    {#each alignerModels as m (m.id)}
                                        <option value={m.id}>
                                            {m.label}{m.default ? ' (default)' : ''}
                                        </option>
                                    {/each}
                                </select>
                            </label>
                        {/if}
                        <button
                            class="adv-toggle"
                            type="button"
                            onclick={() => (advancedOpen = !advancedOpen)}
                        >
                            {advancedOpen ? '− Advanced' : '+ Advanced'}
                        </button>
                    </div>

                    {#if advancedOpen}
                        <div class="line adv">
                            <label class="field">
                                <span class="lbl">workers</span>
                                <input
                                    type="number"
                                    min="1"
                                    max="64"
                                    bind:value={draft.workers}
                                    placeholder="auto"
                                />
                            </label>
                            <label class="field">
                                <span class="lbl">batch</span>
                                <input
                                    type="number"
                                    min="1"
                                    bind:value={draft.batch_size}
                                    placeholder="auto"
                                />
                            </label>
                            <label class="field">
                                <span class="lbl">downloads</span>
                                <input
                                    type="number"
                                    min="1"
                                    bind:value={draft.download_workers}
                                    placeholder="auto"
                                />
                            </label>
                        </div>
                        <div class="line adv">
                            <label class="field wide">
                                <span class="lbl">padding</span>
                                <select bind:value={draft.padding}>
                                    <option value={null}>default (forward)</option>
                                    <option value="forward">forward</option>
                                    <option value="symmetric">symmetric</option>
                                    <option value="none">none</option>
                                </select>
                            </label>
                            <label class="field wide">
                                <span class="lbl">method</span>
                                <input
                                    type="text"
                                    value={draft.method ?? ''}
                                    placeholder="kalpy"
                                    oninput={(e) =>
                                        (draft!.method =
                                            (e.currentTarget as HTMLInputElement).value || null)}
                                />
                            </label>
                        </div>
                    {/if}
                </div>
            </div>
        </div>

        {#if dirty || saveError}
            <div class="foot">
                {#if saveError}<span class="foot-err" role="alert">{saveError}</span>{/if}
                <span class="spacer"></span>
                <button class="discard" type="button" onclick={discard} disabled={saving}>
                    Discard
                </button>
                <button class="save" type="button" onclick={save} disabled={saving || !dirty}>
                    {saving ? 'Saving…' : 'Save defaults'}
                </button>
            </div>
        {/if}
    </section>
{/if}

<style>
    .ts-card {
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        margin-bottom: var(--s-3);
    }

    .head {
        display: flex;
        align-items: center;
        gap: var(--s-2);
        width: 100%;
        padding: var(--s-3);
        background: transparent;
        border: 0;
        color: var(--text-primary);
        font: inherit;
        cursor: pointer;
        text-align: left;
        border-radius: var(--r-3);
    }
    .head.static { cursor: default; }
    .head:hover:not(.static) .title { color: var(--accent-strong); }
    .chev {
        width: 14px;
        height: 14px;
        color: var(--text-muted);
        transition: transform var(--t-fast) var(--ease-out-expo);
    }
    .chev.open { transform: rotate(180deg); }
    .title { font-size: var(--fs-body); font-weight: 600; }
    .dirty-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--state-error-fg);
    }
    .spacer { flex: 1; }
    .hint {
        font-family: var(--font-mono);
        font-size: 10px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-faint);
    }
    .sub { font-size: var(--fs-meta); color: var(--text-muted); }
    .sub.err { color: var(--state-error-fg); }
    .retry {
        background: transparent;
        border: 1px solid var(--border-default);
        border-radius: var(--r-1);
        color: var(--text-secondary);
        font: inherit;
        font-size: var(--fs-meta);
        padding: 2px 8px;
        cursor: pointer;
    }
    .retry:hover { color: var(--text-primary); border-color: var(--border-strong); }

    .body-wrap {
        display: grid;
        grid-template-rows: 0fr;
        transition: grid-template-rows var(--t-base) var(--ease-out-expo);
    }
    .body-wrap.open { grid-template-rows: 1fr; }
    .body-inner { overflow: hidden; }

    .row {
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
        padding: var(--s-3);
        border-top: 1px solid var(--border-quiet);
    }
    .desc {
        margin: 0;
        font-size: var(--fs-meta);
        color: var(--text-secondary);
        line-height: 1.45;
        max-width: 72ch;
    }
    .line { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; }
    .line.adv { padding-top: 2px; }

    .field { display: inline-flex; align-items: center; gap: 6px; }
    .field .lbl {
        font-family: var(--font-mono);
        font-size: 10px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-faint);
    }
    .field input,
    .field select {
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        color: var(--text-primary);
        font: inherit;
        font-size: var(--fs-meta);
        padding: 3px 6px;
        width: 72px;
    }
    .field.wide input,
    .field.wide select { width: 160px; }
    .field input:focus,
    .field select:focus { outline: 0; border-color: var(--accent-tint); }

    .adv-toggle {
        background: transparent;
        border: 0;
        color: var(--text-muted);
        font: inherit;
        font-size: 11px;
        cursor: pointer;
        padding: 0;
    }
    .adv-toggle:hover { color: var(--text-primary); }

    .foot {
        display: flex;
        align-items: center;
        gap: var(--s-2);
        padding: var(--s-2) var(--s-3);
        border-top: 1px solid var(--border-quiet);
    }
    .foot-err { font-size: var(--fs-meta); color: var(--state-error-fg); }
    .discard {
        background: transparent;
        border: 0;
        color: var(--text-muted);
        font: inherit;
        font-size: var(--fs-meta);
        cursor: pointer;
        padding: 4px 8px;
    }
    .discard:hover:not(:disabled) { color: var(--text-primary); }
    .save {
        background: var(--accent-tint-soft);
        border: 1px solid var(--accent);
        color: var(--accent-strong);
        font: inherit;
        font-size: var(--fs-meta);
        font-weight: 500;
        padding: 4px 14px;
        border-radius: var(--r-1);
        cursor: pointer;
        transition: background-color var(--t-fast);
    }
    .save:hover:not(:disabled) { background: var(--accent-tint); }
    .save:disabled,
    .discard:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
