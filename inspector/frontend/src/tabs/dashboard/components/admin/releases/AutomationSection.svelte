<script lang="ts">
    /**
     * Owner-only Automation section — the top-of-Releases config for the five
     * release automations (auto-gen TS, stale-TS regen, stale-metadata refresh,
     * scheduled HF batch-publish, scheduled GH cut).
     *
     * One collapsible card (no nested cards): a header with the enabled count +
     * a disclosure, then one hairline-separated row per automation. Each row is a
     * switch + inline settings (reusing the ReleasesTsSettings field/radio token
     * vocabulary) + a muted last-run / next-run status line. The GH-cut row shows
     * the auto-computed next version with an inline override. Edits are local; a
     * single explicit Save persists the whole config (the server echoes the fresh
     * payload back). Mounted only for holders of ``release.manage_automation``.
     */
    import { onMount } from 'svelte';
    import { i18n } from '../../../../../lib/i18n/locale.svelte';
    import * as m from '../../../../../lib/paraglide/messages';

    import {
        type AutomationConfigBody,
        type AutomationPayload,
        fetchAutomation,
        saveAutomation,
    } from '../../../../../lib/api/admin-releases';

    // The codegen marks every defaulted field optional. The server always sends a
    // complete blob, so we normalize into a fully-required local shape on load —
    // that keeps the editor bindings type-safe and the dirty check apples-to-apples.
    interface SchedCfg {
        enabled: boolean;
        interval_days: number;
        time_of_day: string;
        timezone: string;
    }
    interface Draft {
        auto_gen_ts: {
            enabled: boolean;
            gate_by_comments: boolean;
            gate_by_flags: boolean;
        };
        stale_ts_regen: { enabled: boolean; guard_minutes: number; scope: 'full' | 'affected' };
        stale_metadata: { enabled: boolean; guard_minutes: number };
        hf_publish: SchedCfg;
        gh_cut: SchedCfg & {
            next_version_override: string | null;
            include_letter_timestamps: boolean;
        };
        auto_release_inactive: { enabled: boolean; inactive_days: number };
    }

    function normalize(c: AutomationConfigBody): Draft {
        const a = c.auto_gen_ts ?? {};
        const r = c.stale_ts_regen ?? {};
        const m = c.stale_metadata ?? {};
        const h = c.hf_publish ?? {};
        const g = c.gh_cut ?? {};
        const ri = c.auto_release_inactive ?? {};
        return {
            auto_gen_ts: {
                enabled: a.enabled ?? false,
                gate_by_comments: a.gate_by_comments ?? true,
                gate_by_flags: a.gate_by_flags ?? true,
            },
            stale_ts_regen: {
                enabled: r.enabled ?? false,
                guard_minutes: r.guard_minutes ?? 60,
                scope: r.scope ?? 'affected',
            },
            stale_metadata: { enabled: m.enabled ?? false, guard_minutes: m.guard_minutes ?? 60 },
            hf_publish: {
                enabled: h.enabled ?? false,
                interval_days: h.interval_days ?? 7,
                time_of_day: h.time_of_day ?? '09:00',
                timezone: h.timezone ?? 'Australia/Sydney',
            },
            gh_cut: {
                enabled: g.enabled ?? false,
                interval_days: g.interval_days ?? 7,
                time_of_day: g.time_of_day ?? '09:00',
                timezone: g.timezone ?? 'Australia/Sydney',
                next_version_override: g.next_version_override ?? null,
                include_letter_timestamps: g.include_letter_timestamps ?? true,
            },
            auto_release_inactive: {
                enabled: ri.enabled ?? false,
                inactive_days: ri.inactive_days ?? 14,
            },
        };
    }

    let server = $state<AutomationPayload | null>(null);
    let draft = $state<Draft | null>(null);
    let baseline = $state<Draft | null>(null);
    let loading = $state(true);
    let error = $state<string | null>(null);
    let saving = $state(false);
    let saveError = $state<string | null>(null);
    let open = $state(false);

    const dirty = $derived(
        !!draft && !!baseline && JSON.stringify(draft) !== JSON.stringify(baseline),
    );
    const enabledCount = $derived.by(() => {
        if (!draft) return 0;
        return [
            draft.auto_gen_ts,
            draft.stale_ts_regen,
            draft.stale_metadata,
            draft.hf_publish,
            draft.gh_cut,
            draft.auto_release_inactive,
        ].filter((a) => a.enabled).length;
    });

    onMount(load);

    function adopt(payload: AutomationPayload): void {
        server = payload;
        const norm = normalize(payload.config);
        baseline = norm;
        draft = structuredClone(norm);
    }

    async function load(): Promise<void> {
        loading = true;
        error = null;
        try {
            adopt(await fetchAutomation());
        } catch (e) {
            error = (e as Error).message ?? 'Failed to load automations';
        } finally {
            loading = false;
        }
    }

    async function save(): Promise<void> {
        if (!draft || saving) return;
        saving = true;
        saveError = null;
        try {
            adopt(await saveAutomation(draft as unknown as AutomationConfigBody));
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

    function stateFor(id: string) {
        return server?.state?.find((s) => s.automation_id === id);
    }

    function statusLine(id: string): string {
        const st = stateFor(id);
        if (!st || !st.last_run_at) return 'Never run';
        const verb =
            st.last_status === 'launched'
                ? 'Launched'
                : st.last_status === 'skipped'
                  ? 'Skipped'
                  : st.last_status === 'error'
                    ? 'Error'
                    : 'Ran';
        const when = relTime(st.last_run_at);
        return st.last_detail ? `${verb} ${when} · ${st.last_detail}` : `${verb} ${when}`;
    }

    function nextRun(id: string): string | null {
        const next = stateFor(id)?.next_run_at;
        return next ? relTime(next) : null;
    }

    function relTime(iso: string | null | undefined): string {
        if (!iso) return '';
        const then = Date.parse(iso);
        if (Number.isNaN(then)) return '';
        const diff = then - Date.now();
        const abs = Math.abs(diff);
        const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
        const steps: [Intl.RelativeTimeFormatUnit, number][] = [
            ['day', 86_400_000],
            ['hour', 3_600_000],
            ['minute', 60_000],
        ];
        for (const [unit, ms] of steps) {
            if (abs >= ms) return rtf.format(Math.round(diff / ms), unit);
        }
        return rtf.format(Math.round(diff / 1000), 'second');
    }
</script>

{#if loading}
    <div class="auto-card" aria-busy="true">
        <div class="head static"><span class="title">Automation</span><span class="sub">Loading…</span></div>
    </div>
{:else if error}
    <div class="auto-card">
        <div class="head static">
            <span class="title">Automation</span>
            <span class="sub err" role="alert">{error}</span>
            <button class="retry" type="button" onclick={load}>Retry</button>
        </div>
    </div>
{:else if draft}
    <section class="auto-card" class:open>
        <button
            class="head"
            type="button"
            aria-expanded={open}
            onclick={() => (open = !open)}
        >
            <svg class="chev" class:open viewBox="0 0 16 16" aria-hidden="true">
                <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" />
            </svg>
            <span class="title">Automation</span>
            <span class="count" class:none={enabledCount === 0}>
                {enabledCount} of 6 on
            </span>
            {#if dirty}<span class="dirty-dot" title="Unsaved changes" aria-label="Unsaved changes"></span>{/if}
            <span class="spacer"></span>
            <span class="hint">Owner-only</span>
        </button>

        <div class="body-wrap" class:open>
            <div class="body-inner" inert={!open}>
                <!-- 1. Auto-generate timestamps -->
                <div class="row">
                    {@render head(
                        draft.auto_gen_ts.enabled,
                        () => (draft!.auto_gen_ts.enabled = !draft!.auto_gen_ts.enabled),
                        'Auto-generate timestamps',
                        'When a reviewer marks a recitation ready and it clears the gates, launch the timestamps job.',
                        'auto_gen_ts',
                    )}
                    {#if draft.auto_gen_ts.enabled}
                        <div class="settings">
                            <label class="check">
                                <input type="checkbox" bind:checked={draft.auto_gen_ts.gate_by_comments} />
                                <span>Skip when the reviewer left a comment</span>
                            </label>
                            <label class="check">
                                <input type="checkbox" bind:checked={draft.auto_gen_ts.gate_by_flags} />
                                <span>Skip when any segment is flagged</span>
                            </label>
                        </div>
                    {/if}
                </div>

                <!-- 2. Regenerate stale timestamps -->
                <div class="row">
                    {@render head(
                        draft.stale_ts_regen.enabled,
                        () => (draft!.stale_ts_regen.enabled = !draft!.stale_ts_regen.enabled),
                        'Regenerate stale timestamps',
                        'When segment edits leave the timestamps behind, regenerate once the edits settle.',
                        'stale_ts_regen',
                    )}
                    {#if draft.stale_ts_regen.enabled}
                        <div class="settings">
                            <div class="line">
                                {@render guard(draft.stale_ts_regen)}
                                <span class="seg">
                                    <label class="radio" class:active={draft.stale_ts_regen.scope === 'affected'}>
                                        <input type="radio" value="affected" bind:group={draft.stale_ts_regen.scope} />
                                        <span>Affected chapters</span>
                                    </label>
                                    <label class="radio" class:active={draft.stale_ts_regen.scope === 'full'}>
                                        <input type="radio" value="full" bind:group={draft.stale_ts_regen.scope} />
                                        <span>Full reciter</span>
                                    </label>
                                </span>
                            </div>
                        </div>
                    {/if}
                </div>

                <!-- 3. Refresh stale catalog metadata -->
                <div class="row">
                    {@render head(
                        draft.stale_metadata.enabled,
                        () => (draft!.stale_metadata.enabled = !draft!.stale_metadata.enabled),
                        'Refresh stale catalog metadata',
                        'When a catalog edit makes the HF dataset metadata stale, refresh it once edits settle.',
                        'stale_metadata',
                    )}
                    {#if draft.stale_metadata.enabled}
                        <div class="settings">{@render guard(draft.stale_metadata)}</div>
                    {/if}
                </div>

                <!-- 4. Scheduled HF batch-publish -->
                <div class="row">
                    {@render head(
                        draft.hf_publish.enabled,
                        () => (draft!.hf_publish.enabled = !draft!.hf_publish.enabled),
                        'Publish to HF dataset',
                        'On a schedule, batch-publish every fresh and stale recitation in one job.',
                        'hf_publish',
                    )}
                    {#if draft.hf_publish.enabled}
                        <div class="settings">{@render schedule(draft.hf_publish)}</div>
                    {/if}
                </div>

                <!-- 5. Scheduled GH cut -->
                <div class="row">
                    {@render head(
                        draft.gh_cut.enabled,
                        () => (draft!.gh_cut.enabled = !draft!.gh_cut.enabled),
                        'Cut GitHub release',
                        'On a schedule, cut a new global GitHub release whenever there are changes.',
                        'gh_cut',
                    )}
                    {#if draft.gh_cut.enabled}
                        <div class="settings">
                            {@render schedule(draft.gh_cut)}
                            <div class="line">
                                <span class="next-ver">
                                    Next version:
                                    {#if draft.gh_cut.next_version_override?.trim()}
                                        <strong>{draft.gh_cut.next_version_override.trim()}</strong>
                                        <span class="ver-tag">override</span>
                                    {:else if server?.next_version}
                                        <strong>{server.next_version}</strong>
                                        <span class="ver-tag">auto</span>
                                    {:else}
                                        <span class="ver-none">nothing to cut</span>
                                    {/if}
                                </span>
                                <label class="field wide">
                                    <span class="lbl">override</span>
                                    <input
                                        type="text"
                                        placeholder="auto"
                                        value={draft.gh_cut.next_version_override ?? ''}
                                        oninput={(e) =>
                                            (draft!.gh_cut.next_version_override =
                                                (e.currentTarget as HTMLInputElement).value || null)}
                                    />
                                </label>
                            </div>
                        </div>
                    {/if}

                    <!-- Release contents also apply to manual cuts when the schedule is off. -->
                    <div class="settings">
                        <label class="check">
                            <input type="checkbox" bind:checked={draft.gh_cut.include_letter_timestamps} />
                            <span>{(i18n.locale, m.dashboard_release_include_letter_timestamps())}</span>
                        </label>
                        <span class="desc">{(i18n.locale, m.dashboard_release_letter_timestamps_hint())}</span>
                    </div>
                </div>

                <!-- 6. Auto-release inactive reviewer claims -->
                <div class="row">
                    {@render head(
                        draft.auto_release_inactive.enabled,
                        () =>
                            (draft!.auto_release_inactive.enabled =
                                !draft!.auto_release_inactive.enabled),
                        'Release inactive claims',
                        'When a reviewer hasn’t touched their claimed recitation for a while, release it back to the pool and notify them. Skips work already marked ready.',
                        'auto_release_inactive',
                    )}
                    {#if draft.auto_release_inactive.enabled}
                        <div class="settings">
                            <label class="field">
                                <span class="lbl">inactive (days)</span>
                                <input
                                    type="number"
                                    min="1"
                                    bind:value={draft.auto_release_inactive.inactive_days}
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
                <button class="discard" type="button" onclick={discard} disabled={saving}>Discard</button>
                <button class="save" type="button" onclick={save} disabled={saving || !dirty}>
                    {saving ? 'Saving…' : 'Save automations'}
                </button>
            </div>
        {/if}
    </section>
{/if}

<!-- Per-row header: switch + name + description, with the status/next-run line. -->
{#snippet head(
    on: boolean,
    toggle: () => void,
    name: string,
    desc: string,
    id: string,
)}
    <div class="row-head">
        <button
            class="switch"
            type="button"
            role="switch"
            aria-checked={on}
            aria-label={name}
            onclick={toggle}
        >
            <span class="knob"></span>
        </button>
        <div class="meta">
            <div class="name-line">
                <span class="name" class:off={!on}>{name}</span>
                {#if on && nextRun(id)}<span class="next">Next run {nextRun(id)}</span>{/if}
            </div>
            <div class="desc">{desc}</div>
            <div class="status">{statusLine(id)}</div>
        </div>
    </div>
{/snippet}

<!-- guard-minutes debounce. -->
{#snippet guard(cfg: { guard_minutes: number })}
    <label class="field">
        <span class="lbl">settle (min)</span>
        <input type="number" min="0" bind:value={cfg.guard_minutes} />
    </label>
{/snippet}

<!-- interval + time-of-day + timezone for the scheduled automations. -->
{#snippet schedule(cfg: { interval_days: number; time_of_day: string; timezone: string })}
    <div class="line">
        <label class="field">
            <span class="lbl">every (days)</span>
            <input type="number" min="1" bind:value={cfg.interval_days} />
        </label>
        <label class="field">
            <span class="lbl">at</span>
            <input type="time" bind:value={cfg.time_of_day} />
        </label>
        <label class="field wide">
            <span class="lbl">timezone</span>
            <input type="text" bind:value={cfg.timezone} placeholder="Australia/Sydney" />
        </label>
    </div>
{/snippet}

<style>
    .auto-card {
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        margin-bottom: var(--s-3);
    }

    /* Header / disclosure */
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
    .count {
        font-family: var(--font-mono);
        font-size: var(--fs-meta);
        color: var(--accent);
        background: var(--accent-tint);
        padding: 1px 7px;
        border-radius: var(--r-1);
    }
    .count.none { color: var(--text-muted); background: transparent; padding-left: 0; }
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

    /* Collapse: grid-rows 0fr→1fr animates height; --t-base zeroes under reduced motion. */
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
    .row-head { display: flex; align-items: flex-start; gap: var(--s-3); }

    /* Switch */
    .switch {
        flex-shrink: 0;
        width: 34px;
        height: 19px;
        margin-top: 1px;
        padding: 2px;
        border: 1px solid var(--border-strong);
        border-radius: 999px;
        background: var(--canvas-inset);
        cursor: pointer;
        transition: background var(--t-fast), border-color var(--t-fast);
    }
    .switch .knob {
        display: block;
        width: 13px;
        height: 13px;
        border-radius: 50%;
        background: var(--text-muted);
        transition: transform var(--t-fast) var(--ease-out-expo), background var(--t-fast);
    }
    .switch[aria-checked='true'] {
        background: var(--accent-tint);
        border-color: var(--accent);
    }
    .switch[aria-checked='true'] .knob { transform: translateX(15px); background: var(--accent); }
    .switch:focus-visible { outline: 0; box-shadow: 0 0 0 2px var(--accent-tint); }

    .meta { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .name-line { display: flex; align-items: baseline; gap: var(--s-2); flex-wrap: wrap; }
    .name { font-size: var(--fs-body); font-weight: 600; color: var(--text-primary); }
    .name.off { color: var(--text-secondary); font-weight: 400; }
    .next {
        font-family: var(--font-mono);
        font-size: 10.5px;
        color: var(--accent);
    }
    .desc { font-size: var(--fs-meta); color: var(--text-secondary); line-height: 1.45; max-width: 64ch; }
    .status { font-family: var(--font-mono); font-size: 10.5px; color: var(--text-muted); }

    /* Settings — reuses the ReleasesTsSettings field/radio vocabulary. */
    .settings {
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
        padding-left: calc(34px + var(--s-3));
    }
    .line { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; }
    .seg { display: inline-flex; align-items: center; gap: var(--s-3); }

    .field { display: inline-flex; align-items: center; gap: 6px; }
    .field .lbl {
        font-family: var(--font-mono);
        font-size: 10px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-faint);
    }
    .field input {
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        color: var(--text-primary);
        font: inherit;
        font-size: var(--fs-meta);
        padding: 3px 6px;
        width: 64px;
    }
    .field.wide input { width: 150px; }
    .field input:focus { outline: 0; border-color: var(--accent-tint); }

    .check {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: var(--fs-meta);
        color: var(--text-secondary);
        cursor: pointer;
    }
    .check input { accent-color: var(--accent); margin: 0; }

    .radio {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: var(--fs-meta);
        color: var(--text-secondary);
        cursor: pointer;
    }
    .radio.active { color: var(--text-primary); }
    .radio input { accent-color: var(--accent); margin: 0; }

    .next-ver { font-size: var(--fs-meta); color: var(--text-secondary); }
    .next-ver strong { font-family: var(--font-mono); color: var(--text-primary); font-weight: 600; }
    .ver-tag {
        font-family: var(--font-mono);
        font-size: 10px;
        color: var(--accent);
        background: var(--accent-tint);
        padding: 0 5px;
        border-radius: var(--r-1);
        margin-left: 2px;
    }
    .ver-none { color: var(--text-faint); }

    /* Footer (save bar) */
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
