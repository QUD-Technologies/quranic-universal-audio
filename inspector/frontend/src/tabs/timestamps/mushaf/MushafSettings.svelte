<script lang="ts">
    /** Mushaf display settings — print year, what stays visible, surah header and verse marker. */
    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';
    import {
        MUSHAF_YEARS,
        mushafFrame,
        mushafMarker,
        mushafScope,
        mushafShowUpcoming,
        mushafYear,
        type MushafFrame,
        type MushafMarker,
        type MushafScope,
    } from '../stores/mushaf';

    const numberFmt = $derived(new Intl.NumberFormat(i18n.locale === 'ar' ? 'ar-EG' : 'en', { useGrouping: false }));
    const scopes = $derived<{ value: MushafScope; label: string }[]>(
        (i18n.locale, [
            { value: 'spread', label: m.ts_mushaf_show_spread() },
            { value: 'verse', label: m.ts_mushaf_show_verse() },
        ]),
    );
    const frames = $derived<{ value: MushafFrame; label: string }[]>(
        (i18n.locale, [
            { value: 'print', label: m.ts_mushaf_style_print() },
            { value: 'cartouche', label: m.ts_mushaf_frame_cartouche() },
            { value: 'ribbon', label: m.ts_mushaf_frame_ribbon() },
            { value: 'panel', label: m.ts_mushaf_frame_panel() },
            { value: 'none', label: m.ts_mushaf_style_none() },
        ]),
    );
    const markers = $derived<{ value: MushafMarker; label: string }[]>(
        (i18n.locale, [
            { value: 'auto', label: m.ts_mushaf_style_print() },
            { value: 'circle', label: m.ts_mushaf_marker_circle() },
            { value: 'rosette', label: m.ts_mushaf_marker_rosette() },
            { value: 'number', label: m.ts_mushaf_marker_number() },
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
    <div class="ms-row">
        <span class="ms-label">{L(m.ts_mushaf_settings_frame())}</span>
        <div class="ms-seg ms-wrap" role="radiogroup" aria-label={L(m.ts_mushaf_settings_frame())}>
            {#each frames as f (f.value)}
                <button
                    type="button" role="radio" aria-checked={$mushafFrame === f.value}
                    class:on={$mushafFrame === f.value}
                    onclick={() => mushafFrame.set(f.value)}
                >{f.label}</button>
            {/each}
        </div>
    </div>
    <div class="ms-row">
        <span class="ms-label">{L(m.ts_mushaf_settings_marker())}</span>
        <div class="ms-seg" role="radiogroup" aria-label={L(m.ts_mushaf_settings_marker())}>
            {#each markers as k (k.value)}
                <button
                    type="button" role="radio" aria-checked={$mushafMarker === k.value}
                    class:on={$mushafMarker === k.value}
                    onclick={() => mushafMarker.set(k.value)}
                >{k.label}</button>
            {/each}
        </div>
    </div>
    <p class="ms-hint" class:active={memorizing}>{L(m.ts_mushaf_memorize_hint())}</p>
</div>

<style>
    .ms {
        display: grid;
        gap: var(--s-2);
        width: 380px;
        max-width: calc(100vw - 2 * var(--s-3));
    }
    .ms-wrap { flex-wrap: wrap; }
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
