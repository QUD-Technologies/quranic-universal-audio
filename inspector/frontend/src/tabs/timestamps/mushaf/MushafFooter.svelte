<script lang="ts">
    /**
     * Footer controls for the Mushaf view: the view toggle, and — while the
     * mushaf is open — Repeat and the display settings, each a drop-up.
     */
    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';
    import { clickOutside } from '../../../lib/actions/click-outside';
    import { mushafActive, mushafAvailable, mushafMode } from '../stores/mushaf';
    import MushafRepeatPanel from './MushafRepeatPanel.svelte';
    import MushafSettings from './MushafSettings.svelte';
    import { mushafFullscreen } from './fullscreen.svelte';
    import { mushafRepeat } from './repeat.svelte';

    let open = $state<'repeat' | 'settings' | null>(null);

    const toggleTitle = $derived(
        (i18n.locale,
        !$mushafAvailable
            ? m.ts_mushaf_unavailable_title()
            : $mushafActive
              ? m.ts_mushaf_toggle_off_title()
              : m.ts_mushaf_toggle_on_title()),
    );

    function toggle(which: 'repeat' | 'settings'): void {
        open = open === which ? null : which;
    }

    function close(which: 'repeat' | 'settings'): void {
        // A verse being picked on the page is a click outside the drop-up —
        // keep the panel open for it.
        if (which === 'repeat' && mushafRepeat.picking) return;
        if (open === which) open = null;
    }

    const fullTitle = $derived(
        (i18n.locale, mushafFullscreen.on ? m.ts_mushaf_fullscreen_exit() : m.ts_mushaf_fullscreen_enter()),
    );

    function toggleView(): void {
        mushafFullscreen.exit();
        mushafMode.update((v) => !v);
        mushafRepeat.stop();
        open = null;
    }

    // Keep a drop-up inside the viewport (the footer can overflow on narrow windows).
    function keepInView(node: HTMLElement) {
        const margin = 8;
        const place = (): void => {
            node.style.transform = '';
            const r = node.getBoundingClientRect();
            const shift = r.right > window.innerWidth - margin
                ? window.innerWidth - margin - r.right
                : r.left < margin ? margin - r.left : 0;
            if (shift) node.style.transform = `translateX(${shift}px)`;
        };
        place();
        window.addEventListener('resize', place);
        return { destroy: () => window.removeEventListener('resize', place) };
    }

    /** Re-evaluate a message on locale switch (reads the locale rune). */
    const L = (s: string): string => (i18n.locale, s);
</script>

<div class="mf">
    <button
        type="button" class="icon-btn" class:on={$mushafActive}
        aria-pressed={$mushafActive} disabled={!$mushafAvailable}
        title={toggleTitle} aria-label={toggleTitle}
        onclick={toggleView}
    >
        <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 4c-1.6-1-3.6-1.4-6-1.2v9.4c2.4-.2 4.4.2 6 1.2 1.6-1 3.6-1.4 6-1.2V2.8C11.6 2.6 9.6 3 8 4Z" />
            <path d="M8 4v9.2" />
        </svg>
    </button>

    {#if $mushafActive}
        <div class="pop-wrap" use:clickOutside={() => close('repeat')}>
            <button
                type="button" class="icon-btn" class:on={mushafRepeat.running || open === 'repeat'}
                aria-haspopup="dialog" aria-expanded={open === 'repeat'}
                title={L(m.ts_mushaf_repeat_title())} aria-label={L(m.ts_mushaf_repeat_title())}
                onclick={() => toggle('repeat')}
            >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M3 7V6a2 2 0 0 1 2-2h7.5M10.5 2 12.5 4l-2 2M13 9v1a2 2 0 0 1-2 2H3.5M5.5 14l-2-2 2-2" />
                </svg>
                {#if mushafRepeat.running && mushafRepeat.cursor}
                    <span class="badge">{mushafRepeat.cursor.rep}</span>
                {/if}
            </button>
            {#if open === 'repeat'}
                <div class="pop" role="dialog" aria-label={L(m.ts_mushaf_repeat_title())} use:keepInView>
                    <h4>{L(m.ts_mushaf_repeat_title())}</h4>
                    <MushafRepeatPanel />
                </div>
            {/if}
        </div>

        <div class="pop-wrap" use:clickOutside={() => close('settings')}>
            <button
                type="button" class="icon-btn" class:on={open === 'settings'}
                aria-haspopup="dialog" aria-expanded={open === 'settings'}
                title={L(m.ts_mushaf_settings_title())} aria-label={L(m.ts_mushaf_settings_title())}
                onclick={() => toggle('settings')}
            >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M2 4.5h7M12 4.5h2M2 11.5h2M7 11.5h7" />
                    <circle cx="10.5" cy="4.5" r="1.5" /><circle cx="5.5" cy="11.5" r="1.5" />
                </svg>
            </button>
            {#if open === 'settings'}
                <div class="pop" role="dialog" aria-label={L(m.ts_mushaf_settings_title())} use:keepInView>
                    <h4>{L(m.ts_mushaf_settings_title())}</h4>
                    <MushafSettings />
                </div>
            {/if}
        </div>

        <button
            type="button" class="icon-btn" class:on={mushafFullscreen.on}
            aria-pressed={mushafFullscreen.on}
            title={fullTitle} aria-label={fullTitle}
            onclick={() => mushafFullscreen.toggle()}
        >
            <svg viewBox="0 0 16 16" aria-hidden="true">
                {#if mushafFullscreen.on}
                    <path d="M6 2v4H2M10 2v4h4M6 14v-4H2M10 14v-4h4" />
                {:else}
                    <path d="M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4" />
                {/if}
            </svg>
        </button>
    {/if}
</div>

<style>
    .mf { display: inline-flex; align-items: center; gap: 2px; }
    .icon-btn {
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 22px;
        padding: 0;
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
    .icon-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
    .icon-btn svg {
        width: 15px;
        height: 15px;
        fill: none;
        stroke: currentColor;
        stroke-width: 1.3;
        stroke-linecap: round;
        stroke-linejoin: round;
    }
    .badge {
        position: absolute;
        top: -5px;
        inset-inline-end: -3px;
        min-width: 12px;
        padding: 0 2px;
        font-size: 9px;
        line-height: 12px;
        font-variant-numeric: tabular-nums;
        color: var(--accent-fg);
        background: var(--accent);
        border-radius: 6px;
    }

    .pop-wrap { position: relative; display: inline-flex; }
    .pop {
        position: absolute;
        bottom: calc(100% + var(--s-2));
        left: 50%;
        translate: -50% 0;
        width: max-content;
        padding: var(--s-3);
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        box-shadow: var(--shadow-pop);
        z-index: 50;
    }
    .pop h4 {
        margin: 0 0 var(--s-2);
        font-size: var(--fs-meta);
        color: var(--text-primary);
    }
</style>
