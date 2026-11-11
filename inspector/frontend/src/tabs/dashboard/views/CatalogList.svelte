<script lang="ts">
    /**
     * Dashboard list view. Filters operate on combinations (deliveries),
     * then visible combinations are grouped back by reciter for display.
     */
    import { onDestroy, onMount } from 'svelte';

    import {
        type Axis,
        buildSchemaDescriptor,
        type SchemaDescriptor,
    } from '../../../lib/catalog/schema-descriptor';
    import { localeStore, tr } from '../../../lib/i18n/locale-store';
    import * as m from '../../../lib/paraglide/messages';
    import PickerFilterRail from '../../../lib/components/picker/PickerFilterRail.svelte';
    import SearchInput from '../../../lib/components/SearchInput.svelte';
    import { openInfoModal } from '../../../lib/stores/info-modal';
    import { playerContext } from '../../../lib/stores/player-context';
    import type { PublicDelivery, PublicReciter } from '../../../lib/types/generated/schemas';
    import { bucketRank } from '../../../lib/types/public-bucket';
    import { axisLabel as axisLabelOf, tagLabel as tagLabelOf } from '../../../lib/utils/axis-labels';
    import { compareDeliveries } from '../../../lib/utils/delivery-sort';
    import { type FacetSpec, recomputeFacets } from '../../../lib/utils/facets';
    import { filterByFields } from '../../../lib/utils/fuzzy-match';
    import ActivityRail from '../components/ActivityRail.svelte';
    import type { RowEntry } from '../components/CatalogTable.svelte';
    import CatalogTable from '../components/CatalogTable.svelte';
    import SubmitWizard from '../components/submit/SubmitWizard.svelte';
    import { catalogData, startCatalogPolling } from '../stores/catalog-data';
    import {
        clearAllFilters,
        type DashboardSort,
        dashboardState,
        openDetail,
        setSearch,
        setSort,
        toggleFacet,
    } from '../stores/dashboard-state';
    import { openSubmitWizard } from '../stores/submit-wizard';

    // ---- Shared-height layout --------------------------------------------
    // The page itself never scrolls: the three columns scroll internally inside
    // a shared height (`--catalog-h`) so they end on the same bottom line, just
    // above the fixed player + now-reciting bar. The height is a pure CSS calc
    // (see the style block) off the live `--player-h`/`--now-reciting-h` vars the shell
    // maintains, so the columns reflow automatically as the now-reciting bar
    // grows/shrinks — no JS observation of that dynamic bar. JS only feeds in the
    // two measured inputs: the grid's top offset (header) and the filter rail's
    // natural height (so short rails stay tight rather than filling the viewport).
    let gridEl: HTMLDivElement | undefined;
    let railMeasureEl: HTMLDivElement | undefined;
    let railObserver: ResizeObserver | null = null;

    function syncLayoutVars(): void {
        if (!gridEl) return;
        const top = gridEl.getBoundingClientRect().top;
        if (top > 0) gridEl.style.setProperty('--cat-grid-top', `${top}px`);
        if (railMeasureEl) {
            gridEl.style.setProperty('--cat-rail-h', `${railMeasureEl.offsetHeight}px`);
        }
    }

    onMount(() => {
        const stopPolling = startCatalogPolling();
        syncLayoutVars();
        requestAnimationFrame(syncLayoutVars); // second pass once laid out
        window.addEventListener('resize', syncLayoutVars);
        if (railMeasureEl && typeof ResizeObserver !== 'undefined') {
            railObserver = new ResizeObserver(syncLayoutVars);
            railObserver.observe(railMeasureEl);
        }
        return () => {
            stopPolling();
            window.removeEventListener('resize', syncLayoutVars);
            railObserver?.disconnect();
        };
    });
    onDestroy(() => railObserver?.disconnect());

    let descriptor: SchemaDescriptor | null = null;
    $: allDeliveries = $catalogData.reciters.flatMap((r) => r.deliveries);
    $: if (allDeliveries.length > 0 && descriptor === null) {
        descriptor = buildSchemaDescriptor(allDeliveries);
    }

    $: facetSpecs = (descriptor?.axes ?? []).map<FacetSpec<PublicDelivery>>((axis: Axis) => ({
        key: axis.key,
        tagsOf: axis.tagsOf,
    }));

    $: facetResult = recomputeFacets(allDeliveries, $dashboardState.activeFilters, facetSpecs);

    $: visibleDeliveries = allDeliveries.filter((_, i) => facetResult.rowVisibility[i]);

    // Group visible combinations back under their reciter.
    $: rowEntries = groupByReciter($catalogData.reciters, visibleDeliveries);

    $: searched = filterByFields(
        rowEntries,
        $dashboardState.search,
        (e) => [e.reciter.name, e.reciter.name_ar],
    );

    $: sorted = sortRows(searched, $dashboardState.sort);

    // Total reciters in the catalog (search-bar denominator).
    $: totalReciters = $catalogData.reciters.length;

    // Signature of the active query — changes only when the user re-filters,
    // so CatalogTable can reset its scroll to the top without snapping back on
    // a background catalog poll.
    $: filterKey = [
        $dashboardState.search,
        $dashboardState.sort,
        Object.entries($dashboardState.activeFilters)
            .map(([k, v]) => `${k}:${[...v].sort().join(',')}`)
            .sort()
            .join('|'),
    ].join('§');

    // One collator for every name compare below — far cheaper than letting each
    // `localeCompare` spin up its own Intl collation on every sort.
    const collator = new Intl.Collator();

    function groupByReciter(
        reciters: PublicReciter[],
        visible: PublicDelivery[],
    ): RowEntry[] {
        const bySlug = new Set(visible.map((d) => d.slug));
        const out: RowEntry[] = [];
        for (const r of reciters) {
            const vis = r.deliveries.filter((d) => bySlug.has(d.slug));
            if (vis.length === 0) continue;
            out.push({ reciter: r, visibleDeliveries: vis });
        }
        return out;
    }

    function sortRows(rs: RowEntry[], sort: DashboardSort): RowEntry[] {
        const copy = [...rs];
        if (sort === 'alphabetical') {
            copy.sort((a, b) => collator.compare(a.reciter.name, b.reciter.name));
        } else if (sort === 'combinations') {
            copy.sort((a, b) => b.visibleDeliveries.length - a.visibleDeliveries.length);
        } else if (sort === 'status') {
            copy.sort((a, b) => {
                const s = bucketRank(a.reciter.primary_bucket) - bucketRank(b.reciter.primary_bucket);
                if (s !== 0) return s;
                return compareRecency(a, b);
            });
        } else {
            copy.sort(compareRecency);
        }
        return copy;
    }

    function compareRecency(a: RowEntry, b: RowEntry): number {
        const ax = a.reciter.last_activity ?? '';
        const bx = b.reciter.last_activity ?? '';
        if (ax === bx) return collator.compare(a.reciter.name, b.reciter.name);
        return ax < bx ? 1 : -1;
    }

    function hasActiveFacets(): boolean {
        for (const set of Object.values($dashboardState.activeFilters)) {
            if (set && set.size > 0) return true;
        }
        return false;
    }

    $: hasFilters =
        $dashboardState.search.length > 0
        || hasActiveFacets();

    function onSortChange(ev: Event): void {
        const value = (ev.target as HTMLSelectElement).value;
        setSort(value as DashboardSort);
    }

    function onPlay(row: RowEntry): void {
        // Only `by_surah` is playable — `by_ayah` sidecars key chapters as
        // `"<surah>:<ayah>"`, which the BottomPlayer's `urls[String(surahNum)]`
        // lookup misses. Prefer a visible by_surah delivery so the play button
        // honors active facets; bail if there's none.
        const delivery = [...row.visibleDeliveries]
            .filter((d) => d.audio_category !== 'by_ayah')
            .sort(compareDeliveries)[0];
        if (!delivery) return;
        playerContext.update((s) => ({
            ...s,
            reciter: row.reciter,
            delivery,
            surahNum: s.surahNum ?? 1,
            positionMs: 0,
            isPlaying: true,
        }));
    }

    const tagLabel = (axisKey: string, tag: string): string => tagLabelOf(descriptor, axisKey, tag);
    const axisLabel = (axisKey: string): string => axisLabelOf(descriptor, axisKey);

    // Locale-reactive chrome strings (legacy Svelte-4 `$:` idiom).
    $: searchPlaceholder = tr($localeStore, m.dashboard_catalog_search_placeholder());
    $: submitRecitationLabel = tr($localeStore, m.dashboard_catalog_submit_recitation_button());
    $: aboutAriaLabel = tr($localeStore, m.dashboard_catalog_about_aria_label());
    $: aboutTitle = tr($localeStore, m.dashboard_catalog_about_title());
    $: sortLabel = tr($localeStore, m.dashboard_catalog_sort_label());
    $: sortStatusLabel = tr($localeStore, m.dashboard_catalog_sort_status());
    $: sortRecentLabel = tr($localeStore, m.dashboard_catalog_sort_recent());
    $: sortAlphabeticalLabel = tr($localeStore, m.dashboard_catalog_sort_alphabetical());
    $: sortCombinationsLabel = tr($localeStore, m.dashboard_catalog_sort_combinations());
    $: loadingLabel = tr($localeStore, m.dashboard_catalog_loading());
    $: filtersLabel = tr($localeStore, m.dashboard_catalog_filters_label());
    $: clearFilterAriaLabel = tr($localeStore, m.dashboard_catalog_clear_filter_aria_label());
    $: clearSearchAriaLabel = tr($localeStore, m.dashboard_catalog_clear_search_aria_label());
    $: clearAllLabel = tr($localeStore, m.common_action_clear_all());
</script>

<div class="grid" bind:this={gridEl}>
    <aside class="rail">
        <div class="rail-inner" bind:this={railMeasureEl}>
            {#if descriptor}
                <PickerFilterRail
                    axes={descriptor.axes}
                    activeFilters={$dashboardState.activeFilters}
                    perFacetCounts={facetResult.perFacetCounts}
                    on:toggle={(e) => toggleFacet(e.detail.axis, e.detail.tag)}
                />
            {/if}
        </div>
    </aside>

    <section class="body">
        <div class="toolbar">
            <div class="search-group">
                <div class="search">
                    <SearchInput
                        value={$dashboardState.search}
                        placeholder={searchPlaceholder}
                        count={sorted.length}
                        total={totalReciters}
                        debounceMs={120}
                        on:input={(e) => setSearch(e.detail)}
                    />
                </div>
                <button
                    type="button"
                    class="submit-recitation"
                    on:click={openSubmitWizard}
                >
                    <span class="sr-glyph" aria-hidden="true">+</span>
                    <span class="sr-label">{submitRecitationLabel}</span>
                </button>
                <button
                    type="button"
                    class="info-btn"
                    aria-label={aboutAriaLabel}
                    title={aboutTitle}
                    on:click={openInfoModal}
                >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" />
                        <path d="M12 11v5" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                        <circle cx="12" cy="7.5" r="1.25" fill="currentColor" />
                    </svg>
                </button>
            </div>
            <div class="sort">
                <label>
                    <span class="sort-label">{sortLabel}</span>
                    <select
                        value={$dashboardState.sort}
                        on:change={onSortChange}
                    >
                        <option value="status">{sortStatusLabel}</option>
                        <option value="recent">{sortRecentLabel}</option>
                        <option value="alphabetical">{sortAlphabeticalLabel}</option>
                        <option value="combinations">{sortCombinationsLabel}</option>
                    </select>
                </label>
            </div>
        </div>
        {#if $catalogData.loading}
            <div class="state">{loadingLabel}</div>
        {:else if $catalogData.error}
            <div class="state error">{$catalogData.error}</div>
        {:else}
            <div class="chips-bar" class:empty={!hasFilters}>
                <span class="chips-label">{filtersLabel}</span>
                {#each Object.entries($dashboardState.activeFilters) as [axisKey, tags]}
                    {#each [...tags] as tag (axisKey + ':' + tag)}
                        <span class="chip">
                            <span class="chip-axis">{axisLabel(axisKey)}:</span>
                            {tagLabel(axisKey, tag)}
                            <button class="chip-close" aria-label={clearFilterAriaLabel} on:click={() => toggleFacet(axisKey, tag)}>×</button>
                        </span>
                    {/each}
                {/each}
                {#if $dashboardState.search}
                    <span class="chip">
                        “{$dashboardState.search}”
                        <button class="chip-close" aria-label={clearSearchAriaLabel} on:click={() => setSearch('')}>×</button>
                    </span>
                {/if}
                {#if hasFilters}
                    <button class="clear" on:click={clearAllFilters}>{clearAllLabel}</button>
                {/if}
            </div>

            <CatalogTable
                rows={sorted}
                resetKey={filterKey}
                on:open={(e) => openDetail(e.detail.reciter_id)}
                on:play={(e) => onPlay(e.detail)}
            />
        {/if}
    </section>

    <ActivityRail />
</div>

<SubmitWizard />

<style>
    .toolbar {
        display: flex;
        align-items: center;
        gap: var(--s-4);
        padding: 0 0 var(--s-2);
        flex-wrap: wrap;
    }
    .search-group {
        display: flex;
        align-items: center;
        gap: var(--s-2);
        min-width: 0;
    }
    .search {
        width: 250px;
        min-width: 160px;
        max-width: 250px;
    }
    .sort {
        margin-inline-start: auto;
    }
    .sort label { display: inline-flex; align-items: center; gap: var(--s-2); }
    .sort-label {
        font-size: var(--fs-meta);
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }
    .sort select {
        padding: var(--s-2) var(--s-2);
        background: var(--panel);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        color: var(--text-primary);
        font-size: var(--fs-meta);
        cursor: pointer;
    }
    .sort select:focus { border-color: var(--accent); outline: none; }

    .submit-recitation {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        height: 32px;
        padding: 0 var(--s-3);
        background: var(--accent);
        color: var(--accent-fg);
        border: 1px solid var(--accent);
        border-radius: var(--r-2);
        font: inherit;
        font-size: var(--fs-meta);
        font-weight: 500;
        cursor: pointer;
        transition: background var(--t-fast),
                    border-color var(--t-fast),
                    transform var(--t-fast),
                    box-shadow var(--t-base) var(--ease-out-quart);
        box-shadow: 0 1px 2px oklch(0 0 0 / 0.18);
    }
    .submit-recitation:hover {
        background: var(--accent-strong);
        border-color: var(--accent-strong);
        box-shadow: 0 6px 18px oklch(0.785 0.13 220 / 0.18);
    }
    .submit-recitation:active { transform: translateY(1px); }
    .submit-recitation:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }
    .sr-glyph {
        font-size: 14px;
        line-height: 1;
        font-weight: 400;
        opacity: 0.9;
        transition: transform var(--t-base) var(--ease-out-quart);
    }
    .submit-recitation:hover .sr-glyph {
        transform: rotate(90deg);
    }
    .sr-label { letter-spacing: 0.01em; }

    .info-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        flex-shrink: 0;
        background: transparent;
        color: var(--text-muted);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        cursor: pointer;
        transition: color var(--t-fast),
                    border-color var(--t-fast),
                    background var(--t-fast);
    }
    .info-btn:hover {
        color: var(--text-primary);
        border-color: var(--border-strong);
        background: var(--panel);
    }
    .info-btn:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }
    .info-btn svg { display: block; }

    .grid {
        display: grid;
        grid-template-columns: 320px minmax(0, 1fr) 320px;
        gap: var(--s-6);
        padding: 0 var(--gutter) var(--s-12);
        /* Shared column height: the smaller of the filter rail's natural height
           and the space left between the grid top and the fixed player stack.
           Built from the live shell vars so it reflows as the now-reciting bar
           resizes; `--cat-grid-top` / `--cat-rail-h` are fed by JS. The trailing
           subtractions reserve the grid's own bottom padding + a small gap. */
        --catalog-h: max(280px, min(
            var(--cat-rail-h, 200vh),
            calc(100dvh - var(--cat-grid-top, 120px) - var(--player-h, 72px)
                 - var(--now-reciting-h, 0px) - var(--s-12) - 8px)
        ));
    }
    @media (max-width: 1280px) {
        .grid { grid-template-columns: 330px minmax(0, 1fr); }
        .grid :global(.activity) { display: none; }
    }
    @media (max-width: 900px) {
        .grid { grid-template-columns: 1fr; }
        /* Single column: drop the shared-height envelope and let the page flow;
           the list keeps its own viewport-tall scroll box (see CatalogTable). */
        .rail { max-height: none; overflow: visible; }
        .body { height: auto; }
    }
    .rail {
        max-height: var(--catalog-h);
        overflow-y: auto;
        overflow-x: hidden;
    }
    .rail-inner {
        display: flex;
        flex-direction: column;
        gap: var(--s-5);
        padding-top: var(--s-3);
    }
    .body {
        min-width: 0;
        display: flex;
        flex-direction: column;
        height: var(--catalog-h);
        min-height: 0;
    }
    .state {
        padding: var(--s-12) 0;
        text-align: center;
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }
    .state.error { color: var(--state-error-fg); }

    .chips-bar {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--s-2);
        padding: var(--s-3) 0 var(--s-4);
        border-bottom: 1px solid var(--border-quiet);
        min-height: 32px;
    }
    .chips-bar.empty { min-height: 0; padding: 0; border-bottom: none; }
    .chips-bar.empty > * { display: none; }
    .chips-label {
        color: var(--text-muted);
        font-size: var(--fs-meta);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-inline-end: var(--s-2);
    }
    .chip {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        padding: 3px var(--s-2);
        background: var(--accent-tint-soft);
        border: 1px solid var(--accent-tint);
        border-radius: var(--r-2);
        color: var(--text-primary);
        font-size: var(--fs-meta);
    }
    .chip-axis {
        color: var(--text-muted);
        text-transform: lowercase;
    }
    .chip-close {
        background: transparent;
        border: 0;
        color: var(--text-muted);
        font-size: 14px;
        line-height: 1;
        padding: 0 0 0 2px;
        cursor: pointer;
        transition: color var(--t-fast);
    }
    .chip-close:hover { color: var(--accent-strong); }
    .clear {
        background: transparent;
        border: 0;
        margin-inline-start: auto;
        color: var(--text-muted);
        font-size: var(--fs-meta);
        text-decoration: underline;
        text-decoration-color: var(--border-default);
        text-underline-offset: 3px;
        cursor: pointer;
    }
    .clear:hover { color: var(--text-primary); }
</style>
