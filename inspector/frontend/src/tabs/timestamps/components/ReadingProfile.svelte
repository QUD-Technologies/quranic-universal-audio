<script lang="ts">
    /**
     * Reading profile — a footer button beside the recitation picker that opens
     * a drop-up of how the selected recitation reads wherever Hafs allows more
     * than one way: per group, per family of selectors, per word, every option with the
     * verses read that way (each a link that jumps the Timestamps view there)
     * and the options this recitation never reads, dimmed. It shows only what
     * the reciter reads — never how that was determined.
     *
     * The profile loads on first open (see `reading-profile-source`) and is
     * cached per delivery.
     */
    import { tick } from 'svelte';

    import { clickOutside } from '../../../lib/actions/click-outside';
    import LoadingSpinner from '../../../lib/components/player/LoadingSpinner.svelte';
    import { i18n } from '../../../lib/i18n/locale.svelte';
    import * as m from '../../../lib/paraglide/messages';
    import { pendingTsNavigation } from '../../../lib/stores/navigation';
    import { surahName } from '../../../lib/utils/surah-info';
    import { FAMILIES, GROUP_TITLE, OPTION_TERM_AR, optionLabel } from '../domain/reading-choices';
    import { loadReadingProfile } from '../services/reading-profile-source';
    import type { ProfileGroup, VerseLink } from '../utils/reading-profile';

    interface Props {
        slug: string;
        reciterName: string;
        reciterNameAr?: string | null;
    }

    const { slug, reciterName, reciterNameAr = null }: Props = $props();

    type LoadState =
        | { kind: 'idle' }
        | { kind: 'loading'; done: number; total: number }
        | { kind: 'error' }
        | { kind: 'ready'; groups: ProfileGroup[] };

    const PANEL_ID = 'ts-reading-profile';
    let open = $state(false);
    let loadState = $state<LoadState>({ kind: 'idle' });
    let loadedSlug = '';
    let triggerEl: HTMLButtonElement | null = $state(null);
    let panelEl: HTMLDivElement | null = $state(null);

    function load(target: string): void {
        loadedSlug = target;
        loadState = { kind: 'loading', done: 0, total: 0 };
        loadReadingProfile(target, (done, total) => {
            if (loadedSlug === target && loadState.kind === 'loading') loadState = { kind: 'loading', done, total };
        })
            .then((groups) => { if (loadedSlug === target) loadState = { kind: 'ready', groups }; })
            .catch(() => { if (loadedSlug === target) loadState = { kind: 'error' }; });
    }

    $effect(() => {
        if (open && slug && slug !== loadedSlug) load(slug);
    });

    async function toggle(): Promise<void> {
        open = !open;
        if (!open) return;
        await tick();
        panelEl?.focus();
    }

    function close(returnFocus = false): void {
        if (!open) return;
        open = false;
        if (returnFocus) triggerEl?.focus();
    }

    function onKeydown(e: KeyboardEvent): void {
        if (e.key !== 'Escape') return;
        e.stopPropagation();
        close(true);
    }

    function jump(verse: VerseLink): void {
        pendingTsNavigation.set({ surah: verse.surah, ayah: verse.ayah, autoplay: true, slug });
        close();
    }

    const triggerLabel = $derived((i18n.locale, m.ts_readings_button()));
    const triggerAria = $derived((i18n.locale, m.ts_readings_button_aria()));

    const verseTitle = (verse: VerseLink): string =>
        m.ts_readings_go_to({ surah: surahName(verse.surah, i18n.locale), ref: verse.label });
</script>

<div class="rp" use:clickOutside={() => close()}>
    <button
        bind:this={triggerEl}
        type="button" class="rp-trigger" class:on={open}
        aria-haspopup="dialog" aria-expanded={open} aria-controls={PANEL_ID}
        aria-label={triggerAria} title={triggerAria}
        onclick={toggle}
    >
        <svg class="rp-icon" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M10 17.5V11M10 11C10 8 6 7.5 5 4.5M10 11c0-3 4-3.5 5-6.5" />
            <circle cx="5" cy="3.6" r="1.6" />
            <circle cx="15" cy="3.6" r="1.6" />
        </svg>
        <span class="rp-trigger-label">{triggerLabel}</span>
    </button>

    {#if open}
        {#key i18n.locale}
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <div
            bind:this={panelEl} id={PANEL_ID} class="rp-panel"
            role="dialog" aria-labelledby="{PANEL_ID}-title" tabindex="-1"
            onkeydown={onKeydown}
        >
            <header class="rp-head">
                <div class="rp-head-text">
                    <h2 id="{PANEL_ID}-title" class="rp-title">{m.ts_readings_title()}</h2>
                    <p class="rp-who">
                        <span>{reciterName}</span>
                        {#if reciterNameAr}<span class="rp-who-ar" lang="ar" dir="rtl">{reciterNameAr}</span>{/if}
                    </p>
                </div>
                <button type="button" class="rp-close" aria-label={m.ts_readings_close()} onclick={() => close(true)}>
                    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
                </button>
            </header>
            <p class="rp-intro">{m.ts_readings_intro()}</p>

            <div class="rp-body">
                {#if loadState.kind === 'loading' || loadState.kind === 'idle'}
                    <p class="rp-status" role="status">
                        <LoadingSpinner size={14} />
                        {#if loadState.kind === 'loading' && loadState.total}
                            {m.ts_readings_loading({ done: loadState.done, total: loadState.total })}
                        {:else}
                            {m.ts_readings_loading_start()}
                        {/if}
                    </p>
                {:else if loadState.kind === 'error'}
                    <div class="rp-status rp-error" role="alert">
                        <span>{m.ts_readings_error()}</span>
                        <button type="button" class="rp-retry" onclick={() => load(slug)}>{m.ts_readings_retry()}</button>
                    </div>
                {:else if !loadState.groups.length}
                    <p class="rp-status">{m.ts_readings_empty()}</p>
                {:else}
                    {#each loadState.groups as group (group.group)}
                        <section class="rp-group" aria-labelledby="{PANEL_ID}-{group.group}">
                            <h3 id="{PANEL_ID}-{group.group}" class="rp-group-title">{GROUP_TITLE[group.group]()}</h3>
                            {#each group.families as fam (fam.family)}
                                {@const spec = FAMILIES[fam.family]!}
                                <article class="rp-sel">
                                    <h4 class="rp-sel-title">{spec.title()}</h4>
                                    <p class="rp-sel-desc">{spec.description()}</p>
                                    <ul class="rp-words">
                                        {#each fam.words as word (word.key)}
                                            <li class="rp-word">
                                                <span class="rp-quran" lang="ar" dir="rtl">{word.texts.join(' ')}</span>
                                                <ul class="rp-options">
                                                    {#each word.options as opt (opt.option)}
                                                        {@const read = opt.verses.length > 0}
                                                        <li class="rp-opt" class:read>
                                                            <span class="rp-mark" aria-hidden="true">
                                                                {#if read}<svg viewBox="0 0 12 12"><path d="M3 6.2l2 2 4-4.4" /></svg>{/if}
                                                            </span>
                                                            <span class="rp-opt-name">
                                                                <span class="rp-opt-label">{optionLabel(opt.option)}</span>
                                                                {#if i18n.locale !== 'ar' && OPTION_TERM_AR[opt.option]}
                                                                    <span class="rp-opt-term" lang="ar" dir="rtl">{OPTION_TERM_AR[opt.option]}</span>
                                                                {/if}
                                                            </span>
                                                            <span class="rp-refs">
                                                                {#if read}
                                                                    {#each opt.verses as verse (verse.label)}
                                                                        <button
                                                                            type="button" class="rp-ref" dir="ltr"
                                                                            title={verseTitle(verse)} aria-label={verseTitle(verse)}
                                                                            onclick={() => jump(verse)}
                                                                        >{verse.label}</button>
                                                                    {/each}
                                                                {:else}
                                                                    <span class="rp-other">{m.ts_readings_other()}</span>
                                                                {/if}
                                                            </span>
                                                        </li>
                                                    {/each}
                                                </ul>
                                            </li>
                                        {/each}
                                    </ul>
                                </article>
                            {/each}
                        </section>
                    {/each}
                {/if}
            </div>
        </div>
        {/key}
    {/if}
</div>

<style>
    .rp { position: relative; display: inline-flex; }

    /* Double-height like the shuffle button beside it: glyph over a small label. */
    .rp-trigger {
        display: inline-flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 1px;
        min-width: 34px;
        padding: 0 var(--s-2);
        color: var(--text-muted);
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        font: inherit;
        font-size: 10.5px;
        line-height: 1.2;
        cursor: pointer;
        white-space: nowrap;
        transition: color var(--t-fast), background var(--t-fast), border-color var(--t-fast);
    }
    .rp-trigger:hover { color: var(--text-primary); border-color: var(--border-strong); background: var(--panel); }
    .rp-trigger.on { color: var(--accent); background: var(--accent-tint); border-color: var(--accent); }
    .rp-trigger:focus-visible,
    .rp-close:focus-visible,
    .rp-ref:focus-visible,
    .rp-retry:focus-visible {
        outline: none;
        box-shadow: 0 0 0 3px var(--accent-tint-strong);
        border-color: var(--accent);
    }
    .rp-icon {
        width: 16px;
        height: 16px;
        flex: 0 0 auto;
        fill: none;
        stroke: currentColor;
        stroke-width: 1.5;
        stroke-linecap: round;
    }

    .rp-panel {
        position: absolute;
        bottom: calc(100% + var(--s-2));
        inset-inline-start: 0;
        width: min(520px, calc(100vw - 2 * var(--s-4)));
        max-height: min(680px, 78vh);
        display: flex;
        flex-direction: column;
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        box-shadow: var(--shadow-pop);
        z-index: 50;
        overflow: hidden;
    }
    .rp-panel:focus { outline: none; }
    .rp-panel:focus-visible { box-shadow: var(--shadow-pop), 0 0 0 3px var(--accent-tint-strong); }

    .rp-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: var(--s-3);
        padding: var(--s-4) var(--s-4) 0;
    }
    .rp-title {
        margin: 0;
        font-size: var(--fs-h3);
        font-weight: 600;
        line-height: 1.3;
        color: var(--text-primary);
    }
    .rp-who {
        margin: 2px 0 0;
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: var(--s-2);
        font-size: var(--fs-body);
        color: var(--text-secondary);
    }
    .rp-who-ar { font-size: var(--fs-row); }
    .rp-close {
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        margin-inline-end: -6px;
        padding: 0;
        color: var(--text-muted);
        background: transparent;
        border: 1px solid transparent;
        border-radius: var(--r-2);
        cursor: pointer;
    }
    .rp-close:hover { color: var(--text-primary); background: var(--panel-2); }
    .rp-close svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; }
    .rp-intro {
        margin: var(--s-2) var(--s-4) 0;
        padding-bottom: var(--s-3);
        border-bottom: 1px solid var(--border-quiet);
        font-size: var(--fs-body);
        line-height: 1.5;
        color: var(--text-muted);
    }

    .rp-body {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        overscroll-behavior: contain;
        padding: 0 var(--s-4) var(--s-4);
        scrollbar-width: thin;
        scrollbar-color: var(--border-default) transparent;
    }
    .rp-status {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: var(--s-2);
        margin: 0;
        padding: var(--s-5) 0 var(--s-2);
        font-size: var(--fs-body);
        color: var(--text-muted);
    }
    .rp-error { color: var(--state-error, var(--text-secondary)); }
    .rp-retry {
        padding: 4px var(--s-3);
        font: inherit;
        color: var(--text-primary);
        background: var(--panel-2);
        border: 1px solid var(--border-default);
        border-radius: var(--r-2);
        cursor: pointer;
    }
    .rp-retry:hover { border-color: var(--border-strong); }

    .rp-group { padding-top: var(--s-5); }
    .rp-group + .rp-group { margin-top: var(--s-1); border-top: 1px solid var(--border-quiet); }
    .rp-group-title {
        margin: 0 0 var(--s-2);
        font-size: var(--fs-meta);
        font-weight: 600;
        letter-spacing: 0.02em;
        color: var(--text-muted);
    }
    .rp-sel + .rp-sel { margin-top: var(--s-4); }
    .rp-sel-title {
        margin: 0;
        font-size: var(--fs-row);
        font-weight: 600;
        line-height: 1.4;
        color: var(--text-primary);
    }
    .rp-sel-desc {
        margin: 2px 0 0;
        font-size: var(--fs-body);
        line-height: 1.5;
        color: var(--text-secondary);
        max-width: 62ch;
    }

    .rp-words {
        list-style: none;
        margin: var(--s-2) 0 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: var(--s-1);
    }
    .rp-word {
        display: grid;
        grid-template-columns: minmax(7.5rem, auto) 1fr;
        align-items: center;
        gap: var(--s-2) var(--s-4);
        padding: var(--s-2) var(--s-3);
        background: var(--panel-2);
        border-radius: var(--r-2);
    }
    .rp-quran {
        font-family: var(--font-quran, 'DigitalKhatt', 'Traditional Arabic', 'Scheherazade New', 'Amiri', serif);
        font-size: 1.75rem;
        line-height: 1.9;
        color: var(--text-primary);
        text-align: center;
        white-space: nowrap;
    }

    .rp-options {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }
    .rp-opt {
        display: grid;
        grid-template-columns: 16px minmax(6.5rem, auto) 1fr;
        align-items: center;
        gap: var(--s-2);
        min-height: 24px;
        color: var(--text-muted);
    }
    .rp-mark {
        width: 16px;
        height: 16px;
        border-radius: 50%;
        border: 1.5px solid var(--border-default);
        display: inline-flex;
        align-items: center;
        justify-content: center;
    }
    .rp-opt.read .rp-mark { background: var(--accent); border-color: var(--accent); }
    .rp-mark svg { width: 10px; height: 10px; fill: none; stroke: var(--accent-fg); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .rp-opt-name { display: inline-flex; align-items: baseline; gap: 6px; min-width: 0; }
    .rp-opt-label { font-size: var(--fs-body); }
    .rp-opt.read .rp-opt-label { color: var(--text-primary); font-weight: 600; }
    .rp-opt-term { font-size: var(--fs-body); color: var(--text-faint); }
    .rp-opt.read .rp-opt-term { color: var(--text-secondary); }

    .rp-refs { display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
    .rp-ref {
        padding: 1px 7px;
        font-family: var(--font-mono);
        font-size: var(--fs-meta);
        font-variant-numeric: tabular-nums;
        line-height: 1.6;
        color: var(--accent);
        background: var(--accent-tint-soft);
        border: 1px solid transparent;
        border-radius: var(--r-1);
        cursor: pointer;
        transition: background var(--t-fast), border-color var(--t-fast);
    }
    .rp-ref:hover { background: var(--accent-tint); border-color: var(--accent); }
    .rp-other { font-size: var(--fs-meta); color: var(--text-faint); }

    @media (max-width: 1280px) {
        .rp-trigger-label { display: none; }
    }
    @media (max-width: 640px) {
        .rp-panel {
            position: fixed;
            inset-inline: var(--s-4);
            bottom: calc(var(--player-h, 112px) + var(--s-2));
            width: auto;
            max-height: min(620px, 72vh);
        }
        .rp-word { grid-template-columns: 1fr; justify-items: stretch; }
        .rp-quran { text-align: start; }
    }
</style>
