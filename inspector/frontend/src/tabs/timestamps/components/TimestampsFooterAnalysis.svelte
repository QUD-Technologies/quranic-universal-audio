<script lang="ts">
    /**
     * Timestamps footer — analysis cluster, rendered in the BottomPlayer
     * `center-trail` slot (just right of the speed button) when the Timestamps
     * tab is active.
     *
     * A single flat row: loop · letters · phonemes · wipe · tajweed · help.
     * `wipe` toggles the continuous karaoke-style
     * highlight (vs the discrete fill).
     * All operate on the shared player (dashPort) + timestamps display stores.
     * The help button opens the shortcuts/guide drop-up.
     */
    import { get } from 'svelte/store';

    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';
    import { clickOutside } from '../../../lib/actions/click-outside';
    import { dashPort } from '../../../lib/playback/dash-port';
    import { ControlIcon } from '../../../lib/recitation-animation';
    import { LS_KEYS } from '../../../lib/utils/constants';
    import { highlightWipe, showLetters, showPhonemes, wordProfile } from '../stores/display';
    import { loopTarget } from '../stores/playback';
    import { reportMode, reportModeActive } from '../stores/report-mode';
    import { loadedVerse } from '../stores/verse';
    import { findWordAt } from '../utils/loop-target';
    import TajweedSettingsPanel from './TajweedSettingsPanel.svelte';

    let guideOpen = $state(false);
    let tajweedOpen = $state(false);

    // Attribute labels gated on i18n.locale so they re-render on a locale switch.
    const groupAria = $derived((i18n.locale, m.ts_footer_analysis_group_aria_label()));
    const loopTitle = $derived((i18n.locale, m.ts_footer_loop_word_title()));
    const lettersTitle = $derived(
        (i18n.locale,
        $reportModeActive && $reportMode.kind !== 'phonemes'
            ? m.ts_footer_letters_locked_title()
            : m.ts_footer_toggle_letters_title()),
    );
    const phonemesTitle = $derived(
        (i18n.locale,
        $reportMode.kind === 'phonemes'
            ? m.ts_footer_phonemes_locked_on_title()
            : $reportModeActive
              ? m.ts_footer_phonemes_off_reporting_title()
              : m.ts_footer_toggle_phonemes_title()),
    );
    const guideTitle = $derived((i18n.locale, m.ts_footer_shortcuts_guide_title()));
    const wipeTitle = $derived((i18n.locale, m.ts_footer_wipe_title()));
    const tajweedSettingsTitle = $derived((i18n.locale, m.ts_footer_tajweed_settings_title()));

    // A word-profile shard has no letters, phonemes, per-cell tajweed tags or
    // sound timings, so these four controls have nothing to act on. Disabled
    // with an explanation rather than hidden: a reader who knows the control
    // should be there is better served by being told why it is inert.
    const wordProfileTitle = $derived(
        (i18n.locale, m.ts_footer_word_profile_disabled_title()),
    );

    function persist(key: string, v: boolean): void {
        try { localStorage.setItem(key, String(v)); } catch { /* ignore */ }
    }
    function toggleLetters(): void {
        showLetters.update((v) => { persist(LS_KEYS.TS_SHOW_LETTERS, !v); return !v; });
    }
    function togglePhonemes(): void {
        showPhonemes.update((v) => { persist(LS_KEYS.TS_SHOW_PHONEMES, !v); return !v; });
    }
    function toggleWipe(): void {
        highlightWipe.update((v) => !v); // self-persists (see stores/display)
    }
    function toggleLoop(): void {
        if (get(loopTarget)) { loopTarget.set(null); return; }
        const lv = get(loadedVerse);
        if (!lv) return;
        const t = dashPort.currentTimeMs() / 1000 - lv.tsSegOffset;
        const words = lv.data.words;
        const w = findWordAt(t, words, false) ?? words[0];
        if (!w) return;
        loopTarget.set({ kind: 'word', startSec: w.start, endSec: w.end, wordIndex: words.indexOf(w) });
    }

    type GuideRow = {
        icon?: 'letters' | 'phonemes';
        img?: string;
        key?: string;
        label: string;
    };
    // Reading i18n.locale makes this $derived (and the inline title/aria-label
    // calls below) re-run when the locale switches.
    const SHORTCUTS = $derived<{ title: string; rows: GuideRow[] }[]>(
        (i18n.locale, [
            { title: m.ts_shortcuts_section_playback(), rows: [
                { key: 'Space', label: m.ts_shortcuts_label_play_pause() },
                { key: '← / →', label: m.ts_shortcuts_label_seek_prev_next_ayah() },
                { key: '↑ / ↓', label: m.ts_shortcuts_label_prev_next_word() },
            ] },
            { title: m.ts_shortcuts_section_navigation(), rows: [
                { key: '[ / ]', label: m.ts_shortcuts_label_prev_next_ayah() },
                { key: 'R', label: m.ts_shortcuts_label_shuffle_jump() },
                { key: 'J', label: m.ts_shortcuts_label_scroll_active() },
            ] },
            { title: m.ts_shortcuts_section_display(), rows: [
                { icon: 'letters', key: 'L', label: m.ts_shortcuts_label_letters() },
                { icon: 'phonemes', key: 'P', label: m.ts_shortcuts_label_phonemes() },
            ] },
            { title: m.ts_shortcuts_section_interactions(), rows: [
                { key: 'Click', label: m.ts_shortcuts_label_seek_to_word() },
                { img: '/icons/loop.svg', key: 'Dbl-click', label: m.ts_shortcuts_label_loop_word() },
                { key: 'Waveform', label: m.ts_shortcuts_label_click_to_seek() },
            ] },
        ]),
    );

    // Keep the drop-up on-screen: it's anchored to its button, but on a narrow
    // window the footer overflows and the button slides off the right edge,
    // dragging the popup with it. Shift it left so its right edge clears the
    // viewport, and only cap its width as a last resort.
    function keepInView(node: HTMLElement) {
        const margin = 8;
        const place = (): void => {
            node.style.right = '0px';
            node.style.maxWidth = '';
            const overflowRight = node.getBoundingClientRect().right - (window.innerWidth - margin);
            node.style.right = `${Math.max(0, overflowRight)}px`;
            if (node.getBoundingClientRect().left < margin) {
                node.style.maxWidth = `${window.innerWidth - margin * 2}px`;
            }
        };
        place();
        window.addEventListener('resize', place);
        return { destroy: () => window.removeEventListener('resize', place) };
    }

    // Centre the tajweed panel on the footer's play button (the centred transport
    // column = true viewport centre), so the 2×2 grid's column gap lines up under
    // play. Falls back to viewport-clamped if the wide box would overflow an edge.
    function centerOnPlay(node: HTMLElement) {
        const margin = 8;
        const place = (): void => {
            node.style.right = 'auto';
            node.style.maxWidth = '';
            const wrap = node.parentElement;
            const controls = document.querySelector('.player .controls');
            if (!wrap || !controls) return;
            const wrapLeft = wrap.getBoundingClientRect().left;
            const c = controls.getBoundingClientRect();
            const w = node.offsetWidth;
            if (w > window.innerWidth - margin * 2) {
                node.style.maxWidth = `${window.innerWidth - margin * 2}px`;
                node.style.left = `${Math.round(margin - wrapLeft)}px`;
                return;
            }
            const vpLeft = Math.max(
                margin,
                Math.min(c.left + c.width / 2 - w / 2, window.innerWidth - margin - w),
            );
            node.style.left = `${Math.round(vpLeft - wrapLeft)}px`;
        };
        place();
        window.addEventListener('resize', place);
        return { destroy: () => window.removeEventListener('resize', place) };
    }
</script>

<div class="tfa" role="group" aria-label={groupAria}>
    <button
        type="button" class="icon-btn" class:on={$loopTarget !== null}
        aria-pressed={$loopTarget !== null} title={loopTitle} onclick={toggleLoop}
    ><img class="img-icon" src="/icons/loop.svg" alt="" aria-hidden="true" /></button>
    <button
        type="button" class="icon-btn" class:on={$showLetters && !$wordProfile}
        aria-pressed={$showLetters && !$wordProfile}
        disabled={$wordProfile || ($reportModeActive && $reportMode.kind !== 'phonemes')}
        title={$wordProfile ? wordProfileTitle : lettersTitle}
        onclick={toggleLetters}
    ><ControlIcon name="letters" /></button>
    <button
        type="button" class="icon-btn" class:on={$showPhonemes && !$wordProfile}
        aria-pressed={$showPhonemes && !$wordProfile}
        disabled={$wordProfile || $reportModeActive}
        title={$wordProfile ? wordProfileTitle : phonemesTitle}
        onclick={togglePhonemes}
    ><ControlIcon name="phonemes" /></button>
    <button
        type="button" class="icon-btn" class:on={$highlightWipe && !$wordProfile}
        aria-pressed={$highlightWipe && !$wordProfile}
        disabled={$wordProfile}
        title={$wordProfile ? wordProfileTitle : wipeTitle}
        onclick={toggleWipe}
    ><ControlIcon name="wipe" /></button>

    <div class="guide-wrap" use:clickOutside={() => (tajweedOpen = false)}>
        <button
            type="button" class="icon-btn" class:on={tajweedOpen}
            disabled={$wordProfile}
            title={$wordProfile ? wordProfileTitle : tajweedSettingsTitle}
            aria-haspopup="dialog" aria-expanded={tajweedOpen}
            onclick={() => (tajweedOpen = !tajweedOpen)}
        ><ControlIcon name="tajweed" /></button>
        {#if tajweedOpen}
            <div class="guide-pop" use:centerOnPlay>
                <TajweedSettingsPanel />
            </div>
        {/if}
    </div>

    <div class="guide-wrap" use:clickOutside={() => (guideOpen = false)}>
        <button
            type="button" class="icon-btn" title={guideTitle}
            aria-haspopup="dialog" aria-expanded={guideOpen}
            onclick={() => (guideOpen = !guideOpen)}
        ><ControlIcon name="help" size={15} /></button>
        {#if guideOpen}
            <div class="guide-pop" use:keepInView>
                <div class="guide-shortcuts">
                    {#each SHORTCUTS as sec (sec.title)}
                        <div class="guide-sec">
                            <h4>{sec.title}</h4>
                            {#each sec.rows as r (r.label)}
                                <div class="guide-row">
                                    {#if r.icon}
                                        <span class="g-ic"><ControlIcon name={r.icon} size={14} /></span>
                                    {:else if r.img}
                                        <span class="g-ic"><img class="g-img" src={r.img} alt="" aria-hidden="true" /></span>
                                    {/if}
                                    {#if r.key}<kbd>{r.key}</kbd>{/if}
                                    <span class="g-label">{r.label}</span>
                                </div>
                            {/each}
                        </div>
                    {/each}
                </div>
            </div>
        {/if}
    </div>
</div>

<style>
    /* Single flat row: loop · letters · phonemes · globe · help. */
    .tfa {
        display: inline-flex;
        align-items: center;
        gap: 2px;
    }
    .icon-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 22px;
        color: var(--text-muted);
        background: transparent;
        border: 1px solid transparent;
        border-radius: var(--r-2);
        cursor: pointer;
        transition: color var(--t-fast), background var(--t-fast);
    }
    .icon-btn:hover:not(:disabled) { color: var(--text-primary); background: var(--panel-2); }
    .icon-btn.on { color: var(--accent); background: var(--accent-tint); }
    .icon-btn:disabled { opacity: 0.3; cursor: not-allowed; }
    .img-icon {
        width: 15px;
        height: 15px;
        opacity: 0.6;
        transition: opacity var(--t-fast);
    }
    .icon-btn:hover .img-icon { opacity: 0.9; }
    .icon-btn.on .img-icon { opacity: 1; }
    /* White monochrome SVG glyphs (built for the dark button) — invert to a dark
       mark on the light theme's near-white button surface. */
    :global(html[data-theme='light']) .img-icon,
    :global(html[data-theme='light']) .g-img { filter: invert(1); }

    .guide-wrap { position: relative; display: inline-flex; }
    .guide-pop {
        position: absolute;
        bottom: calc(100% + var(--s-2));
        /* Anchored to the help button's trailing edge so the wider box grows
         * toward the leading side instead of overflowing the viewport. */
        inset-inline-end: 0;
        inset-inline-start: auto;
        display: flex;
        align-items: stretch;
        gap: var(--s-3);
        width: max-content;
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        box-shadow: 0 16px 48px oklch(0 0 0 / 0.45);
        z-index: 50;
        padding: var(--s-3);
    }
    .guide-shortcuts {
        flex: 0 0 auto;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: var(--s-3) var(--s-4);
        min-width: 360px;
    }
    .guide-sec h4 {
        margin: 0 0 4px;
        font-size: var(--fs-meta);
        color: var(--text-primary);
    }
    .guide-row {
        display: flex;
        align-items: center;
        gap: var(--s-2);
        margin-bottom: 2px;
        font-size: var(--fs-meta);
        color: var(--text-secondary);
    }
    .g-ic {
        display: inline-flex;
        flex: 0 0 auto;
        color: var(--text-primary);
    }
    .g-img { width: 14px; height: 14px; opacity: 0.85; }
    .g-label { flex: 1 1 auto; }
    .guide-row kbd {
        flex: 0 0 auto;
        min-width: 42px;
        text-align: center;
        padding: 1px 5px;
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        font-family: var(--font-mono);
        font-size: 10px;
        color: var(--text-faint);
    }
</style>
