<!--
  LocaleSwitcher — a single button cycling Auto → English → عربي → کوردی → Auto, the
  sibling of ThemeToggle in the header.

  It shows ONE language at a time (its endonym), never a segmented pair. 'Auto'
  (the default) follows the browser's language and shows the resolved language
  marked by a small corner monitor badge — the same "following the device"
  affordance ThemeToggle uses; an explicit choice pins the locale (Paraglide's
  `insp_locale` key) and drops the badge, leaving just the word. Fully
  token-driven so it re-skins for light AND dark.
-->
<script lang="ts">
    import * as m from '$lib/paraglide/messages';
    import { i18n, type Locale } from '$lib/i18n/locale.svelte';
    import AutoBadge from './AutoBadge.svelte';

    // Each language in its own name — data, not translated by the active UI locale.
    const ENDONYMS: Record<Locale, string> = { en: 'English', ar: 'عربي', ckb: 'کوردی' };

    const mode = $derived(i18n.mode);
    const isAuto = $derived(mode === 'auto');
    // In auto mode show the resolved locale; when pinned, the pinned one.
    // (`mode === 'auto'` narrows the else branch to Locale; `isAuto` would not.)
    const shown = $derived<Locale>(mode === 'auto' ? i18n.locale : mode);

    // Reading i18n.locale keeps the tooltip reactive across switches.
    const label = $derived(
        (i18n.locale,
        isAuto
            ? m.common_locale_toggle_auto_label()
            : mode === 'ckb'
              ? m.common_locale_toggle_ckb_label()
              : mode === 'ar'
              ? m.common_locale_toggle_ar_label()
              : m.common_locale_toggle_en_label()),
    );
</script>

<button
    type="button"
    class="locale-toggle"
    class:auto={isAuto}
    title={label}
    aria-label={label}
    onclick={() => i18n.cycle()}
>
    <span class="name" lang={shown}>{ENDONYMS[shown] ?? shown}</span>
    {#if isAuto}
        <AutoBadge />
    {/if}
</button>

<style>
    .locale-toggle {
        position: relative;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        height: 32px;
        padding: 0 9px;
        border: 1px solid var(--border-default);
        background: var(--panel);
        color: var(--text-secondary);
        border-radius: var(--r-2);
        font: inherit;
        font-size: 0.8125rem;
        line-height: 1;
        cursor: pointer;
        transition:
            background var(--t-fast),
            color var(--t-fast),
            border-color var(--t-fast);
    }
    .locale-toggle:hover {
        background: var(--panel-2);
        border-color: var(--accent);
        color: var(--accent);
    }
    .locale-toggle:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }
    .name {
        font-weight: 500;
    }
    /* The Arabic endonym reads better a touch larger against the Latin metrics. */
    .name:lang(ar),
    .name:lang(ckb) {
        font-size: 0.9375rem;
    }
</style>
