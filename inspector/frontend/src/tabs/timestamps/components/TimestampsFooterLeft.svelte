<script lang="ts">
    /**
     * Timestamps footer — left cluster (BottomPlayer `meta` slot when the
     * Timestamps tab is active). A tri-state shuffle cycle button sits beside
     * the published-only reciter picker chip (no state pill); the picker
     * drop-up lists the other TS-capable reciters.
     *
     * The analysis cluster (loop / letters / phonemes / translations / help)
     * lives in the player CENTER — see TimestampsFooterAnalysis. The
     * bookmark + panel buttons live on the now-reciting filmstrip.
     */
    import { onMount, tick } from 'svelte';
    import { get } from 'svelte/store';

    import { clickOutside } from '../../../lib/actions/click-outside';
    import ReciterChip from '../../../lib/components/ReciterChip.svelte';
    import SearchInput from '../../../lib/components/SearchInput.svelte';
    import { i18n } from '../../../lib/i18n/locale.svelte';
    import * as m from '../../../lib/paraglide/messages';
    import { dashPort } from '../../../lib/playback/dash-port';
    import { playerContext } from '../../../lib/stores/player-context';
    import { LS_KEYS } from '../../../lib/utils/constants';
    import { combinationCompact, vocabLabel } from '../../../lib/utils/delivery-label';
    import { filterByFields } from '../../../lib/utils/fuzzy-match';
    import { catalogData } from '../../dashboard/stores/catalog-data';
    import { loadManifest } from '../services/ts_client';
    import {
        findTsEntryBySlug,
        resolveTsDeliveries,
        type TsPublishedEntry,
    } from '../services/ts-published';
    import { cycleShuffle, requestManualShuffle, shuffleMode } from '../stores/shuffle';

    // Tri-state shuffle: 0 off · 1 ayah (same reciter) · 2 both (random
    // reciter + ayah). Off/ayah share the single-reciter glyph (ayah just
    // highlights it); both swaps to the random-reciter glyph.
    const SHUFFLE_LABEL = $derived(
        (i18n.locale,
        [
            m.ts_footer_shuffle_off_label(),
            m.ts_footer_shuffle_ayah_label(),
            m.ts_footer_shuffle_both_label(),
        ]),
    );

    let manifestSlugs = $state(new Set<string>());
    let pickerOpen = $state(false);
    let pickerSearch = $state('');
    /** Selected riwayah slug, or '' for "All". */
    let pickerRiwayah = $state('');
    let searchInputEl: SearchInput | null = $state(null);

    onMount(() => {
        void loadManifest()
            .then((m) => { manifestSlugs = new Set(Object.keys(m.reciters ?? {})); })
            .catch(() => {});
    });

    const entries = $derived(resolveTsDeliveries($catalogData.reciters, manifestSlugs));

    // Riwayah chips are built from the entries actually on offer — one chip per
    // riwayah present, carrying its count, most-populated first. Only shown when
    // there is more than one riwayah to choose between.
    const riwayahChips = $derived.by(() => {
        (void i18n.locale);
        const counts = new Map<string, number>();
        for (const e of entries) {
            const r = e.delivery.riwayah;
            if (r) counts.set(r, (counts.get(r) ?? 0) + 1);
        }
        return [...counts]
            .map(([slug, count]) => ({ slug, count, label: vocabLabel('riwayah', slug) }))
            .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    });

    // A chip that disappears (catalog reload, manifest change) must not leave a
    // filter pinned to a riwayah with no entries behind it.
    $effect(() => {
        if (pickerRiwayah && !riwayahChips.some((c) => c.slug === pickerRiwayah)) pickerRiwayah = '';
    });

    const riwayahEntries = $derived(
        pickerRiwayah ? entries.filter((e) => e.delivery.riwayah === pickerRiwayah) : entries,
    );
    const filteredEntries = $derived(
        filterByFields(riwayahEntries, pickerSearch, (e) => [e.reciter.name, e.reciter.name_ar]),
    );
    const curSlug = $derived($playerContext.delivery?.slug ?? '');
    const curEntry = $derived(findTsEntryBySlug($catalogData.reciters, manifestSlugs, curSlug));
    const hasMany = $derived(entries.length > 1);

    function closePicker(): void {
        pickerOpen = false;
        pickerSearch = '';
        pickerRiwayah = '';
    }

    async function togglePicker(): Promise<void> {
        if (!hasMany) return;
        if (pickerOpen) {
            closePicker();
            return;
        }
        pickerOpen = true;
        pickerSearch = '';
        pickerRiwayah = '';
        await tick();
        searchInputEl?.focus();
    }

    function pick(e: TsPublishedEntry): void {
        closePicker();
        if (e.delivery.slug === curSlug) return;
        try { localStorage.setItem(LS_KEYS.TS_RECITER, e.delivery.slug); } catch { /* ignore */ }
        playerContext.update((s) => ({
            ...s,
            reciter: e.reciter,
            delivery: e.delivery,
            surahNum: s.surahNum ?? 1,
            positionMs: 0,
            isPlaying: true,
        }));
    }

    function onShuffleClick(): void {
        const wasPaused = dashPort.paused;
        cycleShuffle();
        if (wasPaused && get(shuffleMode) !== 0) requestManualShuffle();
    }
</script>

<div class="tfl">
    <!-- Reciter picker (published only, no state pill) -->
    <div class="picker-wrap" use:clickOutside={closePicker}>
        <button
            type="button" class="picker-trigger" class:interactive={hasMany}
            onclick={togglePicker}
            disabled={!hasMany && !curEntry}
            aria-haspopup={hasMany ? 'listbox' : undefined}
            aria-expanded={hasMany ? pickerOpen : undefined}
        >
            {#if curEntry}
                <ReciterChip
                    name={curEntry.reciter.name}
                    nameAr={curEntry.reciter.name_ar}
                    country={curEntry.reciter.country}
                    subline={combinationCompact(curEntry.delivery)}
                    bucket={null}
                    switchable={hasMany}
                />
            {:else}
                <span class="muted">{m.ts_footer_picker_empty()}</span>
            {/if}
        </button>

        {#if pickerOpen && hasMany}
            <div class="dropup" role="listbox" aria-label={m.ts_footer_picker_listbox_aria_label()}>
                <div class="dropup-search">
                    <SearchInput
                        bind:this={searchInputEl}
                        value={pickerSearch}
                        placeholder={m.ts_footer_picker_search_placeholder()}
                        count={filteredEntries.length}
                        total={riwayahEntries.length}
                        ariaLabel={m.ts_footer_picker_search_aria_label()}
                        on:input={(e) => { pickerSearch = e.detail; }}
                    />
                </div>
                {#if riwayahChips.length > 1}
                    <div class="chips" role="group" aria-label={m.ts_footer_picker_riwayah_aria_label()}>
                        <button
                            type="button" class="chip" class:on={!pickerRiwayah}
                            aria-pressed={!pickerRiwayah}
                            onclick={() => { pickerRiwayah = ''; }}
                        >
                            {m.ts_footer_picker_riwayah_all()}
                            <span class="chip-n">{entries.length}</span>
                        </button>
                        {#each riwayahChips as c (c.slug)}
                            <button
                                type="button" class="chip" class:on={pickerRiwayah === c.slug}
                                aria-pressed={pickerRiwayah === c.slug}
                                onclick={() => { pickerRiwayah = pickerRiwayah === c.slug ? '' : c.slug; }}
                            >
                                {c.label}
                                <span class="chip-n">{c.count}</span>
                            </button>
                        {/each}
                    </div>
                {/if}
                <div class="dropup-list">
                    {#each filteredEntries as e (e.delivery.slug)}
                        <button
                            type="button" class="opt" class:active={e.delivery.slug === curSlug}
                            role="option" aria-selected={e.delivery.slug === curSlug}
                            onclick={() => pick(e)}
                        >
                            <ReciterChip
                                name={e.reciter.name}
                                nameAr={e.reciter.name_ar}
                                country={e.reciter.country}
                                subline={combinationCompact(e.delivery)}
                                bucket={null}
                                variant="compact"
                            />
                        </button>
                    {:else}
                        <p class="empty">{m.ts_footer_picker_no_results()}</p>
                    {/each}
                </div>
            </div>
        {/if}
    </div>

    <!-- Tri-state shuffle cycle (double-height, right of the picker) -->
    <button
        type="button" class="shuffle-btn" class:on={$shuffleMode !== 0}
        aria-label={SHUFFLE_LABEL[$shuffleMode]} title={SHUFFLE_LABEL[$shuffleMode]}
        onclick={onShuffleClick}
    ><span class="shuffle-imgs">
            <!-- Both glyphs render once (decoded at mount); the cycle only flips
                 which is visible via CSS — no src swap, so no per-toggle decode. -->
            <img
                class="shuffle-img" class:active={$shuffleMode !== 2}
                src="/icons/reciter.svg" alt="" aria-hidden="true"
            />
            <img
                class="shuffle-img" class:active={$shuffleMode === 2}
                src="/icons/reciter_random.svg" alt="" aria-hidden="true"
            />
        </span></button>
</div>

<style>
    .tfl {
        display: inline-flex;
        align-items: stretch;
        gap: var(--s-2);
        min-width: 0;
    }
    /* Double-height shuffle button — matches the picker chip's height. */
    .shuffle-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 auto;
        width: 34px;
        align-self: stretch;
        color: var(--text-muted);
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        cursor: pointer;
        transition: color var(--t-fast), background var(--t-fast), border-color var(--t-fast);
    }
    .shuffle-btn:hover { color: var(--text-primary); border-color: var(--border-strong); background: var(--panel); }
    .shuffle-btn.on { color: var(--accent); background: var(--accent-tint); border-color: var(--accent); }
    /* Wrapper carries the emphasis opacity (off/hover/on); each glyph toggles
       its own opacity instantly so the swap never re-decodes or re-fetches. */
    .shuffle-imgs {
        position: relative;
        width: 18px;
        height: 18px;
        opacity: 0.6;
        transition: opacity var(--t-fast);
    }
    .shuffle-btn:hover .shuffle-imgs { opacity: 0.9; }
    .shuffle-btn.on .shuffle-imgs { opacity: 1; }
    .shuffle-img {
        position: absolute;
        inset: 0;
        width: 18px;
        height: 18px;
        opacity: 0;
    }
    .shuffle-img.active { opacity: 1; }
    /* The reciter glyphs are white monochrome SVGs (built for the dark button).
       On light the button surface is near-white, so invert them to a dark mark
       — the button chrome still carries the muted/accent state via `color`. */
    :global(html[data-theme='light']) .shuffle-img { filter: invert(1); }

    .picker-wrap { position: relative; min-width: 0; }
    .picker-trigger {
        display: inline-flex;
        align-items: center;
        max-width: 100%;
        min-width: 0;
        padding: 4px var(--s-2) 4px 4px;
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: 999px;
        color: inherit;
        cursor: default;
        font: inherit;
        text-align: start;
    }
    .picker-trigger.interactive { cursor: pointer; }
    .picker-trigger.interactive:hover { border-color: var(--border-strong); background: var(--panel); }
    .picker-trigger:disabled { opacity: 0.7; }
    .muted { color: var(--text-muted); padding-inline: var(--s-2); font-size: var(--fs-meta); }

    .dropup {
        position: absolute;
        bottom: calc(100% + var(--s-2));
        inset-inline-start: 0;
        min-width: 320px;
        max-width: 480px;
        max-height: min(720px, 80vh);
        overflow: hidden;
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        box-shadow: 0 16px 48px oklch(0 0 0 / 0.45);
        z-index: 50;
        padding: var(--s-2);
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
    }
    .dropup-search {
        flex: 0 0 auto;
        position: sticky;
        top: 0;
        z-index: 1;
    }
    /* Riwayah filter chips — one row under the search, wrapping. */
    .chips {
        flex: 0 0 auto;
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        padding-bottom: var(--s-1);
        border-bottom: 1px solid var(--border-quiet);
    }
    .chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 3px 9px;
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: 999px;
        color: var(--text-muted);
        cursor: pointer;
        font: inherit;
        font-size: var(--fs-meta);
        line-height: 1.4;
        white-space: nowrap;
        transition: color var(--t-fast), background var(--t-fast), border-color var(--t-fast);
    }
    .chip:hover { color: var(--text-primary); border-color: var(--border-strong); background: var(--panel); }
    .chip.on { color: var(--accent); background: var(--accent-tint); border-color: var(--accent); }
    .chip-n {
        color: var(--text-faint);
        font-variant-numeric: tabular-nums;
    }
    .chip.on .chip-n { color: inherit; opacity: 0.75; }

    .dropup-list {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 2px;
    }
    .empty {
        margin: 0;
        padding: var(--s-3) var(--s-2);
        color: var(--text-faint);
        font-size: var(--fs-meta);
        font-style: italic;
        text-align: center;
    }
    .opt {
        display: flex;
        align-items: center;
        padding: 6px var(--s-2);
        background: transparent;
        border: 0;
        border-radius: var(--r-2);
        cursor: pointer;
        text-align: start;
        font: inherit;
        color: var(--text-primary);
        transition: background var(--t-fast);
    }
    .opt:hover { background: var(--accent-tint-soft); }
    .opt.active { background: var(--accent-tint); }
</style>
