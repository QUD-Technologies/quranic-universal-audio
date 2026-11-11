<script lang="ts">
    /**
     * HistoryPanel — top-level edit-history view.
     *
     * Responsibilities:
     *   - Visibility gate via `$historyVisible` → `hidden` attribute on the
     *     outer `#seg-history-view` div. The ID is preserved so the
     *     waveform IntersectionObserver (see waveform-utils.ts) can scope
     *     canvas redraws to this container.
     *   - Summary stat cards from `$historyData.summary` (with verses count
     *     patched from `countVersesFromBatches`) OR filtered summary when
     *     any filter is active.
     *   - `<HistoryFilters>` section.
     *   - Batches list: applies filter + sort via `buildDisplayItems`
     *     → `{#each}` dispatch to `<SplitChainRow>` / `<HistoryBatch>`.
     *
     * Virtualization: when `displayEntries.length` exceeds VIRTUALIZE_THRESHOLD
     * the batches list windows itself. Per-entry heights are measured via a
     * ResizeObserver on each entry-wrapper and stored in a Map keyed by
     * `entryKey(entry)`. Scroll math reuses `virtualization.ts` from the
     * SegmentsList — same prefix-sum pattern, no row-pinning needed since
     * history entries are read-only.
     *
     * What lives outside this component:
     *   - `#seg-history-btn` (in SegmentsTab toolbar) — opens the view.
     *   - Normal-content hide when `$historyVisible` is true — SegmentsTab
     *     gates the normal-content block with `{#if !$historyVisible && ...}`.
     */

    import { onDestroy, onMount } from 'svelte';

    import AudioElement from '../../../../lib/components/AudioElement.svelte';
    import { localizeDigits } from '../../../../lib/i18n/format';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import {
        buildDisplayItems,
        computeFilteredSummary,
        countVersesFromBatches,
        type DisplayEntry,
        editChains,
        type FilteredItemSummary,
        filterErrCats,
        filterHasWasl,
        filterOpTypes,
        filterTier,
        flatItems,
        historyData,
        historyLoadState,
        historyVisible,
        sortMode,
    } from '../../stores/history';
    import { waveformContainer } from '../../stores/playback';
    import { VIRT_BUFFER_ROWS } from '../../utils/constants';
    import { createPreviewPlaybackContext } from '../../utils/playback/preview';
    import {
        bottomSpacerValue,
        findIdxAtOffset,
        rebuildCumHeights,
        topSpacerValue,
    } from '../../../../lib/utils/list-virtualization';
    import EditChainRow from './EditChainRow.svelte';
    import HistoryBatch from './HistoryBatch.svelte';
    import HistoryFilters from './HistoryFilters.svelte';

    // Filtered flat items -----------------------------------------------------
    $: hasFilters = $filterOpTypes.size > 0 || $filterErrCats.size > 0 || $filterHasWasl || $filterTier !== null;

    $: generations = $historyData?.generations ?? [];

    // Batches display ---------------------------------------------------------
    $: displayEntries = (!$historyData || !$historyData.batches) ? [] : buildDisplayItems(
        $flatItems,
        $historyData.batches,
        $sortMode,
        $editChains,
        $filterOpTypes,
        $filterErrCats,
        $filterHasWasl,
        generations,
        $filterTier,
    );

    // Summary derivation ------------------------------------------------------
    interface SummaryCard { value: number | string; label: string }
    $: summary = tr($localeStore, computeSummary(displayEntries));

    function computeSummary(entries: DisplayEntry[]): SummaryCard[] | null {
        if (!$historyData || !$historyData.batches || $historyData.batches.length === 0) {
            return null;
        }
        if (hasFilters) {
            const fs: FilteredItemSummary = computeFilteredSummary(entries);
            return [
                { value: fs.total_operations, label: m.segments_history_stat_operations() },
                { value: fs.chapters_edited, label: m.segments_history_stat_chapters() },
                { value: fs.verses_edited, label: m.segments_history_stat_verses() },
            ];
        }
        // Unfiltered summary comes from server-computed data.summary, with
        // verses patched via countVersesFromBatches (preserved verbatim).
        const s = $historyData.summary;
        const versesEdited = countVersesFromBatches($historyData.batches);
        return [
            { value: s?.total_operations ?? 0, label: m.segments_history_stat_operations() },
            { value: s?.chapters_edited ?? 0, label: m.segments_history_stat_chapters() },
            { value: versesEdited, label: m.segments_history_stat_verses() },
        ];
    }

    function entryKey(di: DisplayEntry): string {
        if (di.type === 'chain') {
            const first = di.chain.ops[0];
            return `chain:${first?.op.op_id ?? 'x'}`;
        }
        return `op:${di.item.batchId ?? 'p'}:${di.item.batchIdx}:${di.item.groupIdx}:${di.item.type}`;
    }

    $: loadingLabel = tr($localeStore, m.segments_history_loading());
    $: loadErrorLabel = tr($localeStore, m.segments_history_load_error());
    $: noHistoryLabel = tr($localeStore, m.segments_history_no_history());
    $: noFilterMatchesLabel = tr($localeStore, m.segments_history_no_filter_matches());

    // Preview playback context — owns one hidden <audio> element and one
    // AudioRange instance. SegmentRow children with `readOnly + previewCtx`
    // route their play button through this. Disposed on panel unmount.
    const previewCtx = createPreviewPlaybackContext();
    let audio: AudioElement;
    $: if (audio && $historyVisible) {
        previewCtx.attachAudioEl(audio.element());
    }

    // ---- Virtualization -----------------------------------------------------
    /** Initial guess before any entry mounts. History cards vary widely
     *  (single-op ~120px, grouped diff ~300px, split chain ~400px); a
     *  middle-of-road estimate keeps first-paint within a row of the
     *  real layout. */
    const FALLBACK_ENTRY_HEIGHT = 220;
    /** Skip windowing below this count — small lists pay nothing for the
     *  spacer/observer overhead. */
    const VIRTUALIZE_THRESHOLD = 40;

    let batchesEl: HTMLDivElement | undefined;
    let scrollTop = 0;
    let viewportHeight = 600;

    const heights = new Map<string, number>();
    let cumHeights: number[] = [0];
    let estimateHeight = FALLBACK_ENTRY_HEIGHT;
    let _measuredCount = 0;
    let _measuredSum = 0;

    let scrollRaf: number | null = null;
    function onScroll(): void {
        if (scrollRaf !== null) return;
        scrollRaf = requestAnimationFrame(() => {
            scrollRaf = null;
            if (!batchesEl) return;
            scrollTop = batchesEl.scrollTop;
            viewportHeight = batchesEl.clientHeight;
        });
    }

    // ResizeObserver — measure each entry-wrapper and feed into the height
    // cache. When a card above the window resizes (e.g. an accordion-like
    // expand inside HistoryBatch), the spacer above must shrink/grow by the
    // delta so visible content doesn't jump.
    const entryObserver = typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver((entries) => {
            if (entries.length === 0) return;
            const oldTop = topSpacerValue(
                cumHeights, startIdx, -1, displayEntries, entryKey, heights, estimateHeight,
            );
            let changed = false;
            for (const entry of entries) {
                const el = entry.target as HTMLElement & { __entryKey?: string };
                const key = el.__entryKey;
                if (!key) continue;
                const box = entry.borderBoxSize?.[0];
                const h = box !== undefined ? box.blockSize : entry.contentRect.height;
                if (h <= 0) continue;
                const prev = heights.get(key);
                if (prev !== undefined && Math.abs(prev - h) < 0.5) continue;
                heights.set(key, h);
                if (prev === undefined) {
                    _measuredCount++;
                    _measuredSum += h;
                } else {
                    _measuredSum += h - prev;
                }
                estimateHeight = _measuredCount > 0
                    ? _measuredSum / _measuredCount
                    : FALLBACK_ENTRY_HEIGHT;
                changed = true;
            }
            if (!changed) return;
            cumHeights = rebuildCumHeights(displayEntries, entryKey, heights, estimateHeight);
            const newTop = topSpacerValue(
                cumHeights, startIdx, -1, displayEntries, entryKey, heights, estimateHeight,
            );
            const delta = newTop - oldTop;
            if (delta !== 0 && batchesEl) {
                batchesEl.scrollTop += delta;
                scrollTop = batchesEl.scrollTop;
            }
        })
        : null;

    const containerObserver = typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(() => {
            if (batchesEl) viewportHeight = batchesEl.clientHeight;
        })
        : null;

    function observeEntry(node: HTMLElement, key: string) {
        (node as HTMLElement & { __entryKey?: string }).__entryKey = key;
        entryObserver?.observe(node);
        return {
            update(newKey: string): void {
                (node as HTMLElement & { __entryKey?: string }).__entryKey = newKey;
            },
            destroy(): void {
                entryObserver?.unobserve(node);
            },
        };
    }

    onMount(() => {
        if (batchesEl) {
            viewportHeight = batchesEl.clientHeight;
            containerObserver?.observe(batchesEl);
        }
    });

    onDestroy(() => {
        if (scrollRaf !== null) cancelAnimationFrame(scrollRaf);
        entryObserver?.disconnect();
        containerObserver?.disconnect();
        previewCtx.dispose();
    });

    // Rebuild prefix sum whenever displayEntries changes (filter / sort /
    // history republish). New entries unmounted to the cache fall back to
    // `estimateHeight` until their wrapper mounts and the ResizeObserver fires.
    $: {
        void displayEntries;
        cumHeights = rebuildCumHeights(displayEntries, entryKey, heights, estimateHeight);
    }

    // When the user toggles a filter, scrolling state from the previous list
    // is meaningless — reset to the top so the new entries render from the
    // start. Tied to (filterOpTypes, filterErrCats) identity, not displayEntries
    // (which would also reset on innocuous re-renders).
    let _lastFilterIdentity: { op: typeof $filterOpTypes; cat: typeof $filterErrCats } = {
        op: $filterOpTypes, cat: $filterErrCats,
    };
    $: if ($filterOpTypes !== _lastFilterIdentity.op || $filterErrCats !== _lastFilterIdentity.cat) {
        _lastFilterIdentity = { op: $filterOpTypes, cat: $filterErrCats };
        if (batchesEl) {
            batchesEl.scrollTop = 0;
            scrollTop = 0;
        }
    }

    $: total = displayEntries.length;
    $: virtualize = total > VIRTUALIZE_THRESHOLD;
    $: startIdx = virtualize
        ? Math.max(0, findIdxAtOffset(cumHeights, scrollTop) - VIRT_BUFFER_ROWS)
        : 0;
    $: endIdx = virtualize
        ? Math.min(total, findIdxAtOffset(cumHeights, scrollTop + viewportHeight) + 1 + VIRT_BUFFER_ROWS)
        : total;
    $: visibleEntries = virtualize ? displayEntries.slice(startIdx, endIdx) : displayEntries;
    $: topSpacerPx = virtualize
        ? topSpacerValue(cumHeights, startIdx, -1, displayEntries, entryKey, heights, estimateHeight)
        : 0;
    $: bottomSpacerPx = virtualize
        ? bottomSpacerValue(cumHeights, endIdx, total, -1, displayEntries, entryKey, heights, estimateHeight)
        : 0;
</script>

<div
    id="seg-history-view"
    class="seg-history-view"
    hidden={!$historyVisible}
    use:waveformContainer
>
    <AudioElement bind:this={audio} preload="metadata" />

    <div id="seg-history-stats" class="seg-history-stats">
        {#if summary}
            <div class="seg-history-stat-cards">
                {#each summary as card}
                    <div class="seg-history-stat-card">
                        <div class="seg-history-stat-value">{tr($localeStore, localizeDigits(card.value))}</div>
                        <div class="seg-history-stat-label">{card.label}</div>
                    </div>
                {/each}
            </div>
        {/if}
    </div>

    {#if $historyLoadState === 'loaded'}
        <HistoryFilters />
    {/if}

    <div
        id="seg-history-batches"
        class="seg-history-batches"
        bind:this={batchesEl}
        on:scroll={onScroll}
    >
        {#if $historyLoadState === 'loading'}
            <div class="seg-history-empty">{loadingLabel}</div>
        {:else if $historyLoadState === 'error'}
            <div class="seg-history-empty">{loadErrorLabel}</div>
        {:else if $historyLoadState === 'empty'}
            <div class="seg-history-empty">{noHistoryLabel}</div>
        {:else if displayEntries.length === 0 && hasFilters}
            <div class="seg-history-empty">{noFilterMatchesLabel}</div>
        {:else}
            {#if topSpacerPx > 0}
                <div class="seg-history-spacer" style="height: {topSpacerPx}px" aria-hidden="true"></div>
            {/if}
            {#each visibleEntries as entry (entryKey(entry))}
                <div use:observeEntry={entryKey(entry)}>
                    {#if entry.type === 'chain'}
                        <EditChainRow chain={entry.chain} {previewCtx} />
                    {:else}
                        <HistoryBatch item={entry.item} {previewCtx} />
                    {/if}
                </div>
            {/each}
            {#if bottomSpacerPx > 0}
                <div class="seg-history-spacer" style="height: {bottomSpacerPx}px" aria-hidden="true"></div>
            {/if}
        {/if}
    </div>
</div>
