<!-- Compact language dropdown; Auto follows the browser preference. -->
<script lang="ts">
    import * as m from '$lib/paraglide/messages';
    import { i18n, type Locale, type LocaleMode } from '$lib/i18n/locale.svelte';

    // Language names are endonyms, independent of the current UI language.
    const ENDONYMS: Record<Locale, string> = { en: 'English', ar: 'عربي', ckb: 'کوردی' };
    const label = $derived((i18n.locale, m.common_locale_select_label()));
    const autoLabel = $derived((i18n.locale, m.common_locale_auto_option({ language: ENDONYMS[i18n.locale] })));
</script>

<select
    class="locale-select"
    value={i18n.mode}
    aria-label={label}
    title={label}
    onchange={(event) => i18n.setMode(event.currentTarget.value as LocaleMode)}
>
    <option value="auto">{autoLabel}</option>
    {#each i18n.locales as locale (locale)}
        <option value={locale} lang={locale}>{ENDONYMS[locale]}</option>
    {/each}
</select>

<style>
    .locale-select {
        height: 32px;
        max-width: 180px;
        min-width: 0;
        padding-inline: 8px;
        border: 1px solid var(--border-default);
        border-radius: var(--r-2);
        background: var(--panel);
        color: var(--text-secondary);
        font: inherit;
        font-size: 0.8125rem;
        cursor: pointer;
        transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
    }
    .locale-select:hover {
        background: var(--panel-2);
        border-color: var(--accent);
        color: var(--accent);
    }
    .locale-select:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }
</style>
