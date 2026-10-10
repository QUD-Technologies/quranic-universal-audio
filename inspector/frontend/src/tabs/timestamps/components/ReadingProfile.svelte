<script lang="ts">
    /**
     * Readings side panel — docked beside the open recitation picker at the
     * picker's height, shown whenever the selected recitation is Hafs (the host
     * renders nothing for other riwayat). It lists what the recitation reads
     * wherever Hafs allows more than one way: per group, one row per word with
     * the word in the Quran font beside its options, the options read carrying
     * the verses read that way and the others dimmed. Each verse chip jumps the
     * Timestamps view to that verse for this reciter. Overflow scrolls inside
     * the panel; labels follow the UI locale only.
     *
     * The profile comes from `GET /api/ts/readings/<slug>` (see
     * `reading-profile-source`), cached per delivery.
     */
    import { i18n } from '../../../lib/i18n/locale.svelte';
    import * as m from '../../../lib/paraglide/messages';
    import { pendingTsNavigation } from '../../../lib/stores/navigation';
    import type { TsReadingVerse } from '../../../lib/types/generated/schemas';
    import { surahName } from '../../../lib/utils/surah-info';
    import { GROUP_TITLE, optionLabel } from '../domain/reading-choices';
    import { loadReadingProfile } from '../services/reading-profile-source';
    import type { ProfileGroup } from '../utils/reading-profile';

    interface Props {
        slug: string;
        /** Called after a verse chip asked the Timestamps view to jump. */
        onjump?: () => void;
    }

    const { slug, onjump }: Props = $props();

    type LoadState =
        | { kind: 'loading' }
        | { kind: 'error' }
        | { kind: 'ready'; groups: ProfileGroup[] };

    const PANEL_ID = 'ts-readings';
    let loadState = $state<LoadState>({ kind: 'loading' });
    let loadedSlug = '';

    function load(target: string): void {
        loadedSlug = target;
        loadState = { kind: 'loading' };
        loadReadingProfile(target)
            .then((groups) => { if (loadedSlug === target) loadState = { kind: 'ready', groups }; })
            .catch(() => { if (loadedSlug === target) loadState = { kind: 'error' }; });
    }

    $effect(() => {
        if (slug && slug !== loadedSlug) load(slug);
    });

    function jump(verse: TsReadingVerse): void {
        pendingTsNavigation.set({ surah: verse.surah, ayah: verse.ayah, autoplay: true, slug });
        onjump?.();
    }

    const verseTitle = (verse: TsReadingVerse): string =>
        m.ts_readings_go_to({ surah: surahName(verse.surah, i18n.locale), ref: verse.label });
</script>

{#key i18n.locale}
<section class="rp" aria-labelledby="{PANEL_ID}-title">
    <h2 id="{PANEL_ID}-title" class="rp-title">{m.ts_readings_title()}</h2>
    <div class="rp-body">
        {#if loadState.kind === 'loading'}
            <p class="rp-status" role="status">{m.ts_readings_loading_start()}</p>
        {:else if loadState.kind === 'error'}
            <p class="rp-status" role="alert">
                {m.ts_readings_error()}
                <button type="button" class="rp-retry" onclick={() => load(slug)}>{m.ts_readings_retry()}</button>
            </p>
        {:else if !loadState.groups.length}
            <p class="rp-status">{m.ts_readings_empty()}</p>
        {:else}
            {#each loadState.groups as group (group.group)}
                <section class="rp-group" aria-labelledby="{PANEL_ID}-{group.group}">
                    <h3 id="{PANEL_ID}-{group.group}" class="rp-group-title">{GROUP_TITLE[group.group]()}</h3>
                    <ul class="rp-words">
                        {#each group.words as word (word.key)}
                            <li class="rp-word">
                                <span class="rp-quran" lang="ar" dir="rtl">{word.texts.join(' ')}</span>
                                <ul class="rp-options">
                                    {#each word.options as opt (opt.option)}
                                        {@const read = opt.verses.length > 0}
                                        <li class="rp-opt" class:read>
                                            <span class="rp-mark" aria-hidden="true">
                                                {#if read}<svg viewBox="0 0 12 12"><path d="M3 6.2l2 2 4-4.4" /></svg>{/if}
                                            </span>
                                            <span class="rp-opt-label">{optionLabel(opt.option)}</span>
                                            {#each opt.verses as verse (verse.label)}
                                                <button
                                                    type="button" class="rp-ref"
                                                    title={verseTitle(verse)} aria-label={verseTitle(verse)}
                                                    onclick={() => jump(verse)}
                                                ><span dir="ltr">{verse.label}</span><svg class="rp-go" viewBox="0 0 10 10" aria-hidden="true"><path d="M3 7l4-4M4 3h3v3" /></svg></button>
                                            {/each}
                                        </li>
                                    {/each}
                                </ul>
                            </li>
                        {/each}
                    </ul>
                </section>
            {/each}
        {/if}
    </div>
</section>
{/key}

<style>
    .rp {
        width: 380px;
        display: flex;
        flex-direction: column;
        min-height: 0;
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        box-shadow: 0 16px 48px oklch(0 0 0 / 0.45);
        overflow: hidden;
    }
    .rp-title {
        margin: 0;
        padding: var(--s-3) var(--s-4) var(--s-2);
        font-size: var(--fs-body);
        font-weight: 600;
        color: var(--text-primary);
        border-bottom: 1px solid var(--border-quiet);
    }
    .rp-body {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        overscroll-behavior: contain;
        padding: 0 var(--s-3) var(--s-3);
        scrollbar-width: thin;
        scrollbar-color: var(--border-default) transparent;
    }
    .rp-status {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: var(--s-2);
        margin: 0;
        padding: var(--s-4) var(--s-1);
        font-size: var(--fs-body);
        color: var(--text-muted);
    }
    .rp-retry {
        padding: 2px var(--s-2);
        font: inherit;
        color: var(--text-primary);
        background: var(--panel-2);
        border: 1px solid var(--border-default);
        border-radius: var(--r-2);
        cursor: pointer;
    }

    .rp-group { padding-top: var(--s-3); }
    .rp-group-title {
        margin: 0 0 var(--s-1);
        padding-inline: var(--s-1);
        font-size: var(--fs-meta);
        font-weight: 600;
        color: var(--text-muted);
    }
    .rp-words {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
    }
    .rp-word {
        display: grid;
        grid-template-columns: 6.5rem 1fr;
        align-items: center;
        gap: var(--s-3);
        padding: 4px var(--s-2);
        background: var(--panel-2);
        border-radius: var(--r-2);
    }
    .rp-quran {
        font-family: var(--font-quran, 'DigitalKhatt', 'Traditional Arabic', 'Scheherazade New', 'Amiri', serif);
        font-size: 1.45rem;
        line-height: 1.8;
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
        gap: 3px;
    }
    .rp-opt {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 4px 6px;
        min-height: 22px;
        font-size: var(--fs-body);
        color: var(--text-faint);
    }
    .rp-opt.read { color: var(--text-primary); }
    .rp-opt.read .rp-opt-label { font-weight: 600; }
    .rp-mark {
        width: 14px;
        height: 14px;
        flex: 0 0 auto;
        border-radius: 50%;
        border: 1.5px solid var(--border-default);
        display: inline-flex;
        align-items: center;
        justify-content: center;
    }
    .rp-opt.read .rp-mark { background: var(--accent); border-color: var(--accent); }
    .rp-mark svg { width: 9px; height: 9px; fill: none; stroke: var(--accent-fg); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }

    .rp-ref {
        display: inline-flex;
        align-items: center;
        gap: 3px;
        padding: 0 4px 0 6px;
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
    :global([dir='rtl']) .rp-ref { padding: 0 6px 0 4px; }
    /* Go-to arrow: points up and onward in the reading direction, so it mirrors in RTL. */
    .rp-go {
        width: 9px;
        height: 9px;
        fill: none;
        stroke: currentColor;
        stroke-width: 1.5;
        stroke-linecap: round;
        stroke-linejoin: round;
        opacity: 0.65;
        transition: transform var(--t-fast), opacity var(--t-fast);
    }
    :global([dir='rtl']) .rp-go { transform: scaleX(-1); }
    .rp-ref:hover,
    .rp-ref:focus-visible { background: var(--accent-tint); border-color: var(--accent); }
    .rp-ref:focus-visible { outline: none; box-shadow: 0 0 0 2px var(--accent-tint-strong); }
    .rp-ref:hover .rp-go,
    .rp-ref:focus-visible .rp-go { opacity: 1; transform: translate(1px, -1px); }
    :global([dir='rtl']) .rp-ref:hover .rp-go,
    :global([dir='rtl']) .rp-ref:focus-visible .rp-go { transform: scaleX(-1) translate(1px, -1px); }

    @media (max-width: 760px) {
        .rp { width: auto; flex: 1 1 50%; }
    }
</style>
