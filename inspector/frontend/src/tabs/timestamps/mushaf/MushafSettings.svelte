<script lang="ts">
    /** Mushaf display settings — print year and what stays visible. */
    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';
    import {
        MUSHAF_YEARS,
        mushafScope,
        mushafShowUpcoming,
        mushafYear,
        type MushafScope,
    } from '../stores/mushaf';

    const numberFmt = $derived(new Intl.NumberFormat(i18n.locale === 'ar' ? 'ar-EG' : 'en', { useGrouping: false }));
    const scopes = $derived<{ value: MushafScope; label: string }[]>(
        (i18n.locale, [
            { value: 'spread', label: m.ts_mushaf_show_spread() },
            { value: 'verse', label: m.ts_mushaf_show_verse() },
        ]),
    );
    const memorizing = $derived($mushafScope === 'verse' && !$mushafShowUpcoming);

    /** Re-evaluate a message on locale switch (reads the locale rune). */
    const L = (s: string): string => (i18n.locale, s);
</script>

<div class="ms">
    <div class="ms-row">
        <span class="ms-label">{L(m.ts_mushaf_settings_print())}</span>
        <div class="ms-seg" role="radiogroup" aria-label={L(m.ts_mushaf_settings_print())}>
            {#each MUSHAF_YEARS as y (y)}
                <button
                    type="button" role="radio" aria-checked={$mushafYear === y}
                    class:on={$mushafYear === y}
                    onclick={() => mushafYear.set(y)}
                >{L(m.ts_mushaf_year({ year: numberFmt.format(Number(y)) }))}</button>
            {/each}
        </div>
    </div>
    <div class="ms-row">
        <span class="ms-label">{L(m.ts_mushaf_settings_show())}</span>
        <div class="ms-seg" role="radiogroup" aria-label={L(m.ts_mushaf_settings_show())}>
            {#each scopes as s (s.value)}
                <button
                    type="button" role="radio" aria-checked={$mushafScope === s.value}
                    class:on={$mushafScope === s.value}
                    onclick={() => mushafScope.set(s.value)}
                >{s.label}</button>
            {/each}
        </div>
    </div>
    <div class="ms-row">
        <span class="ms-label">{L(m.ts_mushaf_settings_upcoming())}</span>
        <div class="ms-seg" role="radiogroup" aria-label={L(m.ts_mushaf_settings_upcoming())}>
            <button
                type="button" role="radio" aria-checked={$mushafShowUpcoming}
                class:on={$mushafShowUpcoming}
                onclick={() => mushafShowUpcoming.set(true)}
            >{L(m.ts_mushaf_upcoming_show())}</button>
            <button
                type="button" role="radio" aria-checked={!$mushafShowUpcoming}
                class:on={!$mushafShowUpcoming}
                onclick={() => mushafShowUpcoming.set(false)}
            >{L(m.ts_mushaf_upcoming_hide())}</button>
        </div>
    </div>
    <p class="ms-hint" class:active={memorizing}>{L(m.ts_mushaf_memorize_hint())}</p>
</div>

<style>
    .ms {
        display: grid;
        gap: var(--s-2);
        width: 340px;
    }
    .ms-row {
        display: grid;
        grid-template-columns: 7.5em 1fr;
        align-items: center;
        gap: var(--s-3);
    }
    .ms-label {
        font-size: var(--fs-meta);
        color: var(--text-secondary);
    }
    .ms-seg {
        display: inline-flex;
        padding: 2px;
        gap: 2px;
        background: var(--panel-2);
        border-radius: var(--r-2);
    }
    .ms-seg button {
        flex: 1 1 0;
        padding: 3px var(--s-2);
        font-size: var(--fs-meta);
        white-space: nowrap;
        color: var(--text-muted);
        background: transparent;
        border: none;
        border-radius: var(--r-1);
        cursor: pointer;
        transition: color var(--t-fast), background var(--t-fast);
    }
    .ms-seg button:hover { color: var(--text-primary); }
    .ms-seg button.on { color: var(--accent); background: var(--accent-tint); }
    .ms-seg button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
    .ms-hint {
        margin: var(--s-1) 0 0;
        font-size: var(--fs-meta);
        color: var(--text-faint);
    }
    .ms-hint.active { color: var(--accent); }
</style>
