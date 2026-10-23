<script lang="ts">
    /**
     * HistoryFilters — edit-type / issue-category pills + sort toggles.
     *
     * Derivations live in the store; pills are declarative `<button>`s with
     * `class:active` bound to the store filter sets.
     *
     * Count semantics:
     *   - Op-type counts are faceted by the active category filter set.
     *   - Category counts are faceted by the active op-type filter set.
     *   - Chain-type "split_segment" count is total chains + raw ops.
     *
     * A section is hidden when it has fewer than two distinct options
     * (there's nothing to filter on). The "Clear Filters" button shows
     * only when at least one filter is active.
     */

    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import {
        buildDisplayItems,
        chainHasWaslAnnotation,
        editChains,
        entryAffectsTimestamps,
        filterErrCats,
        filterHasWasl,
        filterOpTypes,
        filterTier,
        flatItems,
        historyData,
        itemHasWaslAnnotation,
        setSortMode,
        sortMode,
        tierOf,
        toggleFilter,
        toggleTierFilter,
        toggleWaslFilter,
    } from '../../stores/history';
    import { editOpLabel } from '../../i18n/history-labels';
    import { VALIDATION_TITLE } from '../../i18n/validation-labels';
    import { ERROR_CAT_LABELS } from '../../utils/constants';
    import { deriveOpIssueDelta } from '../../utils/validation/classified-issues';

    // `basmala_amin`: legacy category, still hidden in old history.
    const HISTORY_NEUTRAL_CATEGORIES = new Set(['basmala_amin', 'muqattaat']);
    const historyCategory = (cat: string | null | undefined): string | null =>
        cat && !HISTORY_NEUTRAL_CATEGORIES.has(cat) ? cat : null;

    // Derived pill data ------------------------------------------------------

    $: unfilteredEntries = (() => {
        if (!$historyData || !$historyData.batches) return [];
        return buildDisplayItems(
            $flatItems,
            $historyData.batches,
            'time',
            $editChains,
            new Set(),
            new Set()
        );
    })();

    // Base counts (for stable ordering and showing 0-count pills)
    $: baseOpCounts = (() => {
        const counts: Record<string, number> = {};
        for (const entry of unfilteredEntries) {
            if (entry.type === 'chain') {
                for (const { op } of entry.chain.ops) counts[op.op_type] = (counts[op.op_type] || 0) + 1;
            } else {
                for (const op of entry.item.group) counts[op.op_type] = (counts[op.op_type] || 0) + 1;
            }
        }
        return counts;
    })();

    $: baseCatCounts = (() => {
        const counts: Record<string, number> = {};
        for (const entry of unfilteredEntries) {
            const ops = entry.type === 'chain' ? entry.chain.ops.map(c => c.op) : entry.item.group;
            if (ops.length === 0) continue;
            const delta = deriveOpIssueDelta(ops);
            const touched = new Set<string>([
                ...delta.involved,
                ...ops.map((op) => historyCategory(op.op_context_category)).filter((c): c is string => !!c),
            ]);
            for (const cat of touched) counts[cat] = (counts[cat] || 0) + 1;
        }
        return counts;
    })();

    $: sourceForOps = (() => {
        if (!$historyData || !$historyData.batches) return [];
        return buildDisplayItems(
            $flatItems,
            $historyData.batches,
            'time',
            $editChains,
            new Set(), // No op-type filter
            $filterErrCats // Respect active category filter
        );
    })();

    // Op-type counts (faceted by active category filter).
    $: opCounts = (() => {
        const counts: Record<string, number> = {};
        for (const key of Object.keys(baseOpCounts)) counts[key] = 0;
        for (const entry of sourceForOps) {
            if (entry.type === 'chain') {
                for (const { op } of entry.chain.ops) counts[op.op_type] = (counts[op.op_type] || 0) + 1;
            } else {
                for (const op of entry.item.group) counts[op.op_type] = (counts[op.op_type] || 0) + 1;
            }
        }
        return counts;
    })();

    $: sourceForCats = (() => {
        if (!$historyData || !$historyData.batches) return [];
        return buildDisplayItems(
            $flatItems,
            $historyData.batches,
            'time',
            $editChains,
            $filterOpTypes, // Respect active op-type filter
            new Set() // No category filter
        );
    })();

    // Category counts (faceted by active op-type filter).
    $: catCounts = (() => {
        const counts: Record<string, number> = {};
        for (const key of Object.keys(baseCatCounts)) counts[key] = 0;
        for (const entry of sourceForCats) {
            const ops = entry.type === 'chain' ? entry.chain.ops.map(c => c.op) : entry.item.group;
            if (ops.length === 0) continue;
            const delta = deriveOpIssueDelta(ops);
            const touched = new Set<string>([
                ...delta.involved,
                ...ops.map((op) => historyCategory(op.op_context_category)).filter((c): c is string => !!c),
            ]);
            for (const cat of touched) counts[cat] = (counts[cat] || 0) + 1;
        }
        return counts;
    })();

    // Sort ordered entries for stable pill order using base counts.
    $: opEntries = Object.entries(opCounts).sort((a, b) => (baseOpCounts[b[0]] || 0) - (baseOpCounts[a[0]] || 0));
    $: catEntries = Object.entries(catCounts).sort((a, b) => (baseCatCounts[b[0]] || 0) - (baseCatCounts[a[0]] || 0));

    // Wasl-annotation pill count: any entry in the unfiltered list whose ops
    // carry an is_wasl annotation in any targets_after snapshot. Counted off
    // the same `unfilteredEntries` base so the count is stable regardless of
    // other active filters.
    $: waslCount = (() => {
        let n = 0;
        for (const entry of unfilteredEntries) {
            if (entry.type === 'chain') {
                if (chainHasWaslAnnotation(entry.chain)) n++;
            } else if (itemHasWaslAnnotation(entry.item)) {
                n++;
            }
        }
        return n;
    })();

    // Generation tiers --------------------------------------------------------
    // Partition the unfiltered entries by TS-generation tier. Only surfaced when
    // the current (post-latest-generation) tier holds timestamp-affecting edits
    // — i.e. there's actually drift to regenerate; no pending edits → no section.
    $: gens = $historyData?.generations ?? [];
    $: tierStats = (() => {
        if (gens.length === 0) return { tiers: [] as Array<{ index: number; count: number; affecting: number; isCurrent: boolean }>, pendingAffecting: 0 };
        const byTier = new Map<number, { count: number; affecting: number }>();
        for (const entry of unfilteredEntries) {
            const t = tierOf(entry.date, gens);
            const s = byTier.get(t) ?? { count: 0, affecting: 0 };
            s.count += 1;
            if (entryAffectsTimestamps(entry)) s.affecting += 1;
            byTier.set(t, s);
        }
        const currentIdx = gens.length;
        const pendingAffecting = byTier.get(currentIdx)?.affecting ?? 0;
        const tiers = [...byTier.entries()]
            .sort((a, b) => a[0] - b[0])
            .map(([index, s]) => ({ index, count: s.count, affecting: s.affecting, isCurrent: index === currentIdx }));
        return { tiers, pendingAffecting };
    })();
    $: showTiers = tierStats.pendingAffecting > 0;

    function tierLabel(index: number, isCurrent: boolean): string {
        if (isCurrent) return m.segments_history_tier_pending_regen();
        if (index === 0) return m.segments_history_tier_initial();
        return m.segments_history_tier_after_gen({ n: index });
    }

    $: showOps = Object.keys(baseOpCounts).length >= 2;
    $: showCats = Object.keys(baseCatCounts).length >= 2;
    $: showWasl = waslCount > 0;
    $: hasAny = showOps || showCats || showWasl || showTiers;

    $: timestampsFilterLabel = tr($localeStore, m.segments_history_filter_timestamps_label());
    $: timestampsFilterHintTitle = tr($localeStore, m.segments_history_filter_timestamps_hint_title());
    $: editTypeLabel = tr($localeStore, m.segments_history_filter_edit_type_label());
    $: issueTypeLabel = tr($localeStore, m.segments_history_filter_issue_type_label());
    $: annotationLabel = tr($localeStore, m.segments_history_filter_annotation_label());
    $: waslFilterTitle = tr($localeStore, m.segments_history_filter_wasl_title());
    $: waslFilterPill = tr($localeStore, m.segments_history_filter_wasl_pill());
    $: sortByLabel = tr($localeStore, m.segments_history_filter_sort_label());
    $: sortEditTimeLabel = tr($localeStore, m.segments_history_sort_edit_time());
    $: sortQuranOrderLabel = tr($localeStore, m.segments_history_sort_quran_order());
</script>

<div id="seg-history-filters" class="seg-history-filters" class:hidden-none={!hasAny}>
    {#if showTiers}
        <div class="seg-history-filter-section">
            <span class="seg-history-filter-label" title={timestampsFilterHintTitle}>{timestampsFilterLabel}</span>
            <div id="seg-history-filter-tiers" class="seg-history-filter-pills">
                {#each tierStats.tiers as t}
                    <button
                        class="seg-history-filter-pill"
                        class:active={$filterTier === t.index}
                        class:tier-pending={t.isCurrent}
                        data-filter-type="tier"
                        data-filter-value={t.index}
                        title={t.isCurrent ? m.segments_history_tier_pending_title({ count: t.affecting }) : m.segments_history_tier_generic_title()}
                        on:click={() => toggleTierFilter(t.index)}
                    >
                        {t.isCurrent ? '⚠ ' : ''}{tierLabel(t.index, t.isCurrent)} <span class="pill-count">{t.isCurrent ? t.affecting : t.count}</span>
                    </button>
                {/each}
            </div>
        </div>
    {/if}

    {#if showOps}
        <div class="seg-history-filter-section">
            <span class="seg-history-filter-label">{editTypeLabel}</span>
            <div id="seg-history-filter-ops" class="seg-history-filter-pills">
                {#each opEntries as [opType, count]}
                    <button
                        class="seg-history-filter-pill"
                        class:active={$filterOpTypes.has(opType)}
                        class:empty={count === 0}
                        data-filter-type="op"
                        data-filter-value={opType}
                        on:click={() => toggleFilter('op', opType)}
                    >
                        {tr($localeStore, editOpLabel(opType))} <span class="pill-count">{count}</span>
                    </button>
                {/each}
            </div>
        </div>
    {/if}

    {#if showCats}
        <div class="seg-history-filter-section">
            <span class="seg-history-filter-label">{issueTypeLabel}</span>
            <div id="seg-history-filter-cats" class="seg-history-filter-pills">
                {#each catEntries as [cat, count]}
                    <button
                        class="seg-history-filter-pill"
                        class:active={$filterErrCats.has(cat)}
                        class:empty={count === 0}
                        data-filter-type="cat"
                        data-filter-value={cat}
                        on:click={() => toggleFilter('cat', cat)}
                    >
                        {tr($localeStore, VALIDATION_TITLE[cat]?.() ?? ERROR_CAT_LABELS[cat] ?? cat)} <span class="pill-count">{count}</span>
                    </button>
                {/each}
            </div>
        </div>
    {/if}

    {#if showWasl}
        <div class="seg-history-filter-section">
            <span class="seg-history-filter-label">{annotationLabel}</span>
            <div id="seg-history-filter-wasl" class="seg-history-filter-pills">
                <button
                    class="seg-history-filter-pill"
                    class:active={$filterHasWasl}
                    data-filter-type="wasl"
                    data-filter-value="wasl"
                    title={waslFilterTitle}
                    on:click={() => toggleWaslFilter()}
                >
                    {waslFilterPill} <span class="pill-count">{waslCount}</span>
                </button>
            </div>
        </div>
    {/if}

    <div class="seg-history-filter-section">
        <span class="seg-history-filter-label">{sortByLabel}</span>
        <div class="seg-history-filter-pills">
            <button
                id="seg-history-sort-time"
                class="seg-history-filter-pill"
                class:active={$sortMode === 'time'}
                on:click={() => setSortMode('time')}
            >{sortEditTimeLabel}</button>
            <button
                id="seg-history-sort-quran"
                class="seg-history-filter-pill"
                class:active={$sortMode === 'quran'}
                on:click={() => setSortMode('quran')}
            >{sortQuranOrderLabel}</button>
        </div>
    </div>

</div>

<style>
    .hidden-none {
        display: none !important;
    }
    /* The current ("pending regen") tier — amber attention, matching the
       Releases-tab TS-stale chip. */
    .tier-pending {
        border-color: oklch(0.86 0.130 75 / 0.45);
        color: var(--state-error-fg);
    }
    .tier-pending.active {
        background: var(--state-error-bg);
    }
</style>
