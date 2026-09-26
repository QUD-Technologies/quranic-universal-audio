<script lang="ts">
    /**
     * ValidationPanel — Svelte accordion panel for registry-backed validation categories.
     *
     * Subscribes to `$segValidation`. Renders one <details> per non-empty
     * category using `{#each}` over a typed descriptor list.
     * Empty categories are hidden.
     *
     * Open-state: component-local Record<string, boolean>. One-at-a-time
     * (collapseSiblingDetails semantics). Resets on chapter change.
     *
     * LC-slider: reactive `lcThreshold` drives Low Confidence item filtering.
     * Qalqala filter: reactive `activeQalqalaLetter` + `qalqalaEndOfVerse`.
     *
     * Virtualization: the cards container for the open category virtualizes
     * its list when the item count exceeds VIRTUALIZE_THRESHOLD. Only the
     * window of cards intersecting the viewport (plus BUFFER_ROWS above/below)
     * are mounted; spacer divs preserve the container's scroll height. Row
     * heights are measured per-row into `cardHeights` (prefix-summed into
     * `cardOffsets`, binary-searched to map scrollTop → first visible row);
     * unmeasured rows fall back to the running `estCardHeight`. Per-row sizing
     * is required because context-shown accordions (failed / missing_words /
     * audio_bleeding) render tall, highly variable cards — a single global
     * average fed back into the window, which oscillated and hard-froze the tab.
     * Card context-state (Show/Hide Context toggle) is tracked in a per-type
     * Map so cards restore their state on re-mount as the window slides.
     */

    import { afterUpdate, onDestroy, tick } from 'svelte';
    import { get } from 'svelte/store';

    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import { can } from '../../../../lib/stores/capabilities';
    import { currentUser, isOwner } from '../../../../lib/stores/current-user';
    import type { SegValAnyItem, SegValLowConfidenceItem, SegValQalqalaItem, SegValidateResponse } from '../../../../lib/types/generated/schemas';
    import { activeTab } from '../../../../lib/utils/active-tab';
    import { TAB_NAMES } from '../../../../lib/utils/constants';
    import { pendingSegmentsDeepLink, type SegmentsDeepLink } from '../../../../lib/utils/goto-segments';
    import { getWaveformPeaks } from '../../../../lib/utils/waveform-cache';
    import { IssueRegistry } from '../../domain/registry';
    import { SORT_META, sortItems, type SortOption } from '../../domain/sorting';
    import { hasAccordionGuide, isGuideRead } from '../../guides/registry';
    import { VALIDATION_TITLE } from '../../i18n/validation-labels';
    import { accordionPin, clearAccordionPin, pinAccordion } from '../../stores/accordion-pin';
    import { autoSplitMap, ensureAutoSplitMap } from '../../stores/auto-split';
    import { toggleBoundaryState, valBoundaryFilter } from '../../stores/boundary-filter';
    import { getChapterSegments, segAllData, selectedReciter } from '../../stores/chapter';
    import { segConfig } from '../../stores/config';
    import { dirtyTick, getChapterOpsSnapshot } from '../../stores/dirty';
    import { editingSegUid, pendingWaslConfirm } from '../../stores/edit';
    import { stagedWaslPicks } from '../../stores/staged-split';
    import { openGuideModal } from '../../stores/guides';
    import { autoScrollEnabled, playingSegmentIndex } from '../../stores/playback';
    import { segValidation, splitGroupIndex, valUiLcThreshold, valUiMeasuredCardHeight,valUiOpenCategory, valUiScrollTop, waslRecheck } from '../../stores/validation';
    import { resolveSort, selectSort, toggleDir, valSortPrefs, type SortPrefs } from '../../stores/validation-sort';
    import {
        VAL_VIRTUALIZE_THRESHOLD,
        VIRT_BUFFER_ROWS,
    } from '../../utils/constants';
    import { warmSeg, warmSegChapter } from '../../utils/playback/warmup';
    import {
        BOUNDARY_STATES,
        type BoundaryCtx,
        type BoundaryState,
        boundaryStates,
        countBoundaryStates,
        filterByBoundaryStates,
    } from '../../utils/validation/boundary-state';
    import { resolveCardLeadSeg } from '../../utils/validation/card-lead-seg';
    import type { StagedKind } from '../../utils/validation/staged-split';
    import { filterStaleIssues } from '../../utils/validation/stale';
    import { _fetchPeaks } from '../../utils/waveform/utils';
    import ErrorCard from './ErrorCard.svelte';
    import FlaggedCard from './FlaggedCard.svelte';

    // ---- Props ----
    /** Filter results to this chapter number. null = all chapters. */
    export let chapter: number | null = null;
    /** Optional section label shown above the accordions. */
    export let label: string | null = null;

    // ---- Open-state (in-memory persistent) ----
    // `valUiOpenCategory` is the single source of truth — `openCategory` is
    // a read-only mirror used by template bindings. All WRITES to the open
    // state go through `valUiOpenCategory.set(...)` (in `handleAccordionToggle`
    // and the chapter-change reset below). External collapses
    // (`onChapterChange` on manual Sura pick, `onGotoClick` on Go-to) write
    // to the store directly and flow back in via this $: subscription.
    let openCategory: string | null = $valUiOpenCategory;
    $: openCategory = $valUiOpenCategory;

    // Preload the Auto Split cursor map when an auto-split accordion opens so
    // each Auto Split click is a zero-network O(1) lookup instead of a per-click
    // round trip. Fire-and-forget + deduped (stores/auto-split.ts); the
    // reciter-scoped map is dropped on switch by clearPerReciterState.
    $: if ((openCategory === 'cross_verse' || openCategory === 'repetitions') && $selectedReciter) {
        void ensureAutoSplitMap($selectedReciter);
    }

    // Reset on chapter change
    let _prevChapter = chapter;
    $: if (chapter !== _prevChapter) {
        _prevChapter = chapter;
        valUiOpenCategory.set(null);
        cardsScrollTop = 0;
        clearAccordionPin();
    }

    // Clear pin when the user leaves the Segments tab (the SegmentsTab DOM
    // stays mounted, so the accordion's `open` state persists across tab
    // hides — we MUST explicitly drop the pin so coming back captures a
    // fresh open-time snapshot against the latest segValidation).
    let _prevTab: string | null = null;
    $: {
        const t = $activeTab;
        if (_prevTab !== null && _prevTab === TAB_NAMES.SEGMENTS && t !== TAB_NAMES.SEGMENTS) {
            clearAccordionPin();
        }
        _prevTab = t;
    }

    // ---- LC slider ----
    let lcThreshold: number = $valUiLcThreshold ?? get(segConfig).lcDefaultThreshold;
    $: valUiLcThreshold.set(lcThreshold);

    // ---- Qalqala filter ----
    const QALQALA_LETTERS_ORDER: ReadonlyArray<string> = ['\u0642', '\u0637', '\u0628', '\u062c', '\u062f'];
    let activeQalqalaLetter: string | null = null;
    let qalqalaEndOfVerse: boolean = false;

    // ---- Virtualization constants ----
    /** Fallback card height (px) before real measurement. MissingVersesCard with
     *  context rows is much taller; over-estimating reduces pop-in on first open. */
    const FALLBACK_CARD_HEIGHT = 180;
    /** Extra cards rendered above/below the visible window. */
    const BUFFER_ROWS = VIRT_BUFFER_ROWS;

    // ---- Per-category virtualization state ----
    // scrollTop and viewport height of the open category's cards container.
    const CARDS_VIEWPORT_HEIGHT_FALLBACK = 500;
    let cardsScrollTop = $valUiScrollTop;
    $: valUiScrollTop.set(cardsScrollTop);

    let cardsViewportHeight = CARDS_VIEWPORT_HEIGHT_FALLBACK;

    // Per-row virtualization model. `cardHeights[i]` is the measured slot height
    // (card box + 8px gap) of displayedItems[i]; unmeasured rows fall back to
    // `estCardHeight`. `cardOffsets` are prefix sums (row tops) and a binary
    // search maps scrollTop → first visible row. Measuring a row updates only
    // that row's height, so window position never feeds back into a single
    // global average — this is what stops the variable-height oscillation that
    // hard-froze context-shown accordions (failed / missing_words /
    // audio_bleeding) on scroll. `estCardHeight` only sizes the unseen region.
    let estCardHeight = $valUiMeasuredCardHeight ?? FALLBACK_CARD_HEIGHT;
    $: valUiMeasuredCardHeight.set(estCardHeight);
    let cardHeights: number[] = [];
    let cardOffsets: number[] = [0];
    let totalContentHeight = 0;

    function recomputeOffsets(): void {
        const n = openTotal;
        const offsets: number[] = new Array(n + 1);
        let acc = 0;
        offsets[0] = 0;
        for (let i = 0; i < n; i++) {
            acc += cardHeights[i] ?? estCardHeight;
            offsets[i + 1] = acc;
        }
        cardOffsets = offsets;
        totalContentHeight = acc;
    }

    /** Largest row index whose top offset is ≤ y (clamped to [0, openTotal]). */
    function rowAtOffset(y: number): number {
        let lo = 0;
        let hi = openTotal;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if ((cardOffsets[mid] ?? Infinity) <= y) lo = mid;
            else hi = mid - 1;
        }
        return lo;
    }

    // Reset the height model when the open category changes or the row count
    // shifts (filter / structural edit). Re-measure is cheap — only the mounted
    // window is walked.
    let _prevVirtKey = '';
    $: {
        const key = `${openCategory}|${openTotal}`;
        if (key !== _prevVirtKey) {
            _prevVirtKey = key;
            cardHeights = [];
            recomputeOffsets();
        }
    }

    let cardsContainerEl: HTMLDivElement | null = null;
    let _hasRestoredScroll = false;
    $: if (cardsContainerEl && !_hasRestoredScroll) {
        if (cardsScrollTop > 0) {
            cardsContainerEl.scrollTop = cardsScrollTop;
        }
        _hasRestoredScroll = true;
    }

    let scrollRaf: number | null = null;
    function onCardsScroll(): void {
        if (scrollRaf !== null) return;
        scrollRaf = requestAnimationFrame(() => {
            scrollRaf = null;
            if (!cardsContainerEl) return;
            cardsScrollTop = cardsContainerEl.scrollTop;
            cardsViewportHeight = cardsContainerEl.clientHeight;
        });
    }

    // After each update: re-measure card heights + sync window refs → Map.
    // Two concerns merged into one afterUpdate to avoid duplicate DOM walks.
    afterUpdate(() => {
        // Measure each mounted card into its absolute slot; refresh the unseen-
        // region estimate from the window average. Only the changed rows pin —
        // a row's height is independent of scroll, so this converges.
        if (cardsContainerEl) {
            cardsViewportHeight = cardsContainerEl.clientHeight || cardsViewportHeight;
            const cards = cardsContainerEl.querySelectorAll<HTMLElement>('.val-card-wrapper');
            if (cards.length > 0) {
                let changed = false;
                let sum = 0;
                for (let li = 0; li < cards.length; li++) {
                    const el = cards[li];
                    if (!el) continue;
                    const h = el.getBoundingClientRect().height + 8; // +gap
                    if (h <= 20) continue;
                    sum += h;
                    const absIdx = startIdx + li;
                    const prev = cardHeights[absIdx];
                    if (prev == null || Math.abs(prev - h) > 1) {
                        cardHeights[absIdx] = h;
                        changed = true;
                    }
                }
                const avg = sum / cards.length;
                let estChanged = false;
                if (avg > 20 && Math.abs(avg - estCardHeight) > 4) {
                    estCardHeight = avg;
                    estChanged = true;
                }
                if (changed || estChanged) recomputeOffsets();
            }
        }
        // Sync window-slice refs to absolute-index Map
        cardRefMap.clear();
        for (let li = 0; li < windowCardRefs.length; li++) {
            const card = windowCardRefs[li];
            if (card) cardRefMap.set(startIdx + li, card);
        }
    });

    // Reset scroll position and measured height when the open category changes
    // so the new category starts at the top and re-measures its own card sizes.
    let _prevOpenCategory: string | null = openCategory;
    $: if (openCategory !== _prevOpenCategory) {
        _prevOpenCategory = openCategory;
        cardsScrollTop = 0;
        estCardHeight = FALLBACK_CARD_HEIGHT;
        if (cardsContainerEl) {
            cardsContainerEl.scrollTop = 0;
        }
    }

    // ---- Per-type context-shown state (survives virtualization re-mounts) ----
    // Maps item array index → boolean. Stored per category type so toggling
    // "Show/Hide All Context" and then scrolling away preserves the state.
    const contextStateByType: Record<string, Map<number, boolean>> = {};

    function getContextState(type: string): Map<number, boolean> {
        if (!contextStateByType[type]) contextStateByType[type] = new Map();
        return contextStateByType[type];
    }

    // Clear context state only when a filter input (chapter / LC threshold /
    // qalqala letter / end-of-verse) changes — not when the items array
    // identity shifts because of a split/merge fixup. Keying on identity
    // used to reset the Show Context toggle every time a structural edit
    // republished the array, throwing away user-visible state mid-edit.
    const _lastFilterSig: Record<string, string> = {};

    // ---- Category descriptor type ----
    interface CategoryDescriptor {
        name: string;
        type: string;
        countClass: string;
        /** All items (for summary count on non-LC categories). */
        items: SegValAnyItem[];
        /** Filtered/sorted items that actually render as ErrorCards. */
        visibleItems: SegValAnyItem[];
        /** Count shown in summary badge. */
        summaryCount: number;
        isLowConf: boolean;
        isQalqala: boolean;
        /** Letters present in qalqala items (for filter buttons). */
        qalqalaLetters: string[];
        /** Sort options this accordion offers (undefined = no sort pills). */
        sorts?: readonly SortOption[];
        /** Unset · Wasl · Waqf boundary totals (staged-split categories only). */
        boundaryCounts?: Record<BoundaryState, number>;
    }

    /** Base-layer descriptor — everything except the per-filter projection.
     *  Computed once per (validationIdentity, segAllDataIdentity, chapter)
     *  so qalqala/letter and LC/slider clicks don't rebuild filterStaleIssues
     *  or the qalqala letter set. */
    interface BaseDescriptor {
        name: string;
        kind: string;
        countClass: string;
        items: SegValAnyItem[];
        /** LC-default-threshold count (badge value when slider hasn't moved off default). */
        defaultLowConfCount: number;
        isLowConf: boolean;
        isQalqala: boolean;
        qalqalaLetters: string[];
        sorts?: readonly SortOption[];
        boundaryFilter?: boolean;
    }

    // ---- Chapter filter ----
    function matchChapter<T extends { chapter: number }>(arr: T[] | undefined): T[] {
        return chapter === null ? (arr ?? []) : (arr ?? []).filter((i) => i.chapter === chapter);
    }

    // ---- Per-category presentation hints (count badge class). ----
    // Severity \u2192 CSS hook for the count pill. The two flag-style classes
    // (val-rep-count, val-cross-count) are used to colour repetition /
    // cross-verse / muqattaat / qalqala counts in their own palette.
    const COUNT_CLASS_OVERRIDES: Record<string, string> = {
        repetitions: 'val-rep-count',
        cross_verse: 'val-cross-count',
        muqattaat: 'val-cross-count',
        qalqala: 'val-cross-count',
        basmala_amin: 'val-cross-count',
    };
    function _countClassFor(kind: string): string {
        const override = COUNT_CLASS_OVERRIDES[kind];
        if (override) return override;
        const sev = IssueRegistry[kind]?.severity ?? 'error';
        return sev === 'error' ? 'has-errors' : 'has-warnings';
    }

    // Each registry entry maps to the response field it consumes. The accordion
    // descriptor is built in registry accordionOrder.
    function _itemsFor(kind: string, data: SegValidateResponse): SegValAnyItem[] {
        if (kind === 'structural_errors') {
            return matchChapter(data.errors ?? data.structural_errors);
        }
        const slot = (data as Record<string, SegValAnyItem[] | undefined>)[kind];
        return matchChapter(slot);
    }

    // ---- Two-layer rebuild ----
    //
    // Layer 1 (`buildBaseDescriptors`): does the heavy work — `filterStaleIssues`
    // per category and the qalqala letter set computation. Memoized on
    // (validationIdentity, segAllDataIdentity, chapter), so it runs once per
    // validation refresh / chapter switch and NOT on filter button clicks or
    // LC slider drags.
    //
    // Layer 2 (`projectVisible`): cheap per-category projection that depends
    // on the filter inputs (lcThreshold, qalqala letter/EoV). Non-LC/non-
    // qalqala categories get `visibleItems = items` byref so the descriptor
    // is byref-equal to the previous tick when only filters changed for
    // *another* category — keeping Svelte's keyed-each diffs minimal.

    let _baseMemoVal: SegValidateResponse | null = null;
    let _baseMemoSegData: typeof $segAllData = null;
    let _baseMemoChapter: number | null = null;
    let _baseMemoLocale: string | null = null;
    let _baseMemoOwner: boolean | null = null;
    let _baseMemoResult: BaseDescriptor[] = [];

    function buildBaseDescriptors(
        data: SegValidateResponse | null,
        segDataRef: typeof $segAllData,
        chapterFilter: number | null,
        locale: string,
        owner: boolean,
    ): BaseDescriptor[] {
        if (data === _baseMemoVal && segDataRef === _baseMemoSegData && chapterFilter === _baseMemoChapter
            && locale === _baseMemoLocale && owner === _baseMemoOwner) {
            return _baseMemoResult;
        }
        _baseMemoVal = data;
        _baseMemoSegData = segDataRef;
        _baseMemoChapter = chapterFilter;
        _baseMemoLocale = locale;
        _baseMemoOwner = owner;

        if (!data) {
            _baseMemoResult = [];
            return _baseMemoResult;
        }

        // Owner-only categories are dropped outright for everyone else — no
        // accordion, no count, no filter entry: the category may as well not
        // exist for a non-owner.
        const ordered = Object.values(IssueRegistry)
            .filter((d) => owner || !d.ownerOnly)
            .sort((a, b) => a.accordionOrder - b.accordionOrder);

        // Build the live-uid set once so stale-filter has O(1) membership
        // checks. Only per-segment categories use uids; chapter-level
        // categories (missing_verses, structural_errors) carry segment_uid:
        // null and are always kept by filterStaleIssues.
        const allSegs = segDataRef?.segments ?? [];
        const liveUids = new Set(
            allSegs.map((s) => s.segment_uid).filter((u): u is string => !!u),
        );

        const LC_DEFAULT = get(segConfig).lcDefaultThreshold;
        _baseMemoResult = ordered.map((defn) => {
            const rawItems = _itemsFor(defn.kind, data);
            const items = filterStaleIssues(rawItems, liveUids);
            let defaultLowConfCount = 0;
            let isLowConf = false;
            let isQalqala = false;
            let qalqalaLetters: string[] = [];

            if (defn.kind === 'low_confidence') {
                isLowConf = true;
                const lowConf = items as SegValLowConfidenceItem[];
                for (const i of lowConf) {
                    if (i.confidence * 100 < LC_DEFAULT) defaultLowConfCount++;
                }
            } else if (defn.kind === 'qalqala') {
                isQalqala = true;
                const qal = items as SegValQalqalaItem[];
                // Single O(n) pass to find which letters are present —
                // beats QALQALA_LETTERS_ORDER.filter(l => qal.some(...))'s
                // O(5*n) nested loop on every reactive tick.
                const present = new Set<string>();
                for (const i of qal) {
                    if (i.qalqala_letter) present.add(i.qalqala_letter);
                }
                qalqalaLetters = QALQALA_LETTERS_ORDER.filter((l) => present.has(l));
            }

            return {
                name: (VALIDATION_TITLE[defn.kind] ?? (() => defn.displayTitle))(),
                kind: defn.kind,
                countClass: _countClassFor(defn.kind),
                items,
                defaultLowConfCount,
                isLowConf,
                isQalqala,
                qalqalaLetters,
                sorts: defn.sorts,
                boundaryFilter: defn.boundaryFilter,
            };
        });
        return _baseMemoResult;
    }

    /** Per-category visible-items projection. Cheap; runs on every filter
     *  change but only walks the affected category's already-narrowed items. */
    function projectVisible(
        base: BaseDescriptor[],
        _lcThreshold: number,
        _activeQalqalaLetter: string | null,
        _qalqalaEndOfVerse: boolean,
        _sortPrefs: SortPrefs,
        _autoSplitMap: Record<string, { refs: string[] }> | null,
        _boundarySel: ReadonlySet<BoundaryState>,
        _boundaryCtx: BoundaryCtx,
    ): CategoryDescriptor[] {
        const out: CategoryDescriptor[] = [];
        const sortCtx = { autoSplitMap: _autoSplitMap };
        for (const b of base) {
            if (b.items.length === 0) continue;
            let visibleItems: SegValAnyItem[] = b.items;
            let summaryCount = b.items.length;
            let boundaryCounts: Record<BoundaryState, number> | undefined;
            if (b.boundaryFilter) {
                // Cross-verse / missed-waqf: chips count boundaries; the badge
                // counts items still holding an unlabelled boundary (the work left).
                const cat = b.kind as StagedKind;
                boundaryCounts = countBoundaryStates(b.items, _boundaryCtx, cat);
                visibleItems = filterByBoundaryStates(b.items, _boundarySel, _boundaryCtx, cat);
                summaryCount = b.items.filter((it) => boundaryStates(it, _boundaryCtx, cat).includes('unset')).length;
            } else if (b.isLowConf) {
                const lowConf = b.items as SegValLowConfidenceItem[];
                visibleItems = lowConf.filter((i) => (i.confidence * 100) < _lcThreshold);
                summaryCount = b.defaultLowConfCount;
            } else if (b.isQalqala && (_activeQalqalaLetter || _qalqalaEndOfVerse)) {
                const qal = b.items as SegValQalqalaItem[];
                // Single-pass combined filter (letter + end-of-verse) instead
                // of two chained `.filter()` calls.
                const filtered: SegValQalqalaItem[] = [];
                for (const i of qal) {
                    if (_activeQalqalaLetter && i.qalqala_letter !== _activeQalqalaLetter) continue;
                    if (_qalqalaEndOfVerse && i.end_of_verse !== true) continue;
                    filtered.push(i);
                }
                visibleItems = filtered;
                // Qalqala: badge tracks the active filter (parallel to LC).
                summaryCount = filtered.length;
            }
            // Sort the post-filter list per the active (kind, dir) for this
            // accordion. resolveSort falls back to the registry default and
            // returns null for accordions that offer no sorts.
            const eff = resolveSort(_sortPrefs, b.kind);
            if (eff) {
                visibleItems = sortItems(visibleItems, eff.kind, eff.dir, b.sorts, sortCtx);
            }
            out.push({
                name: b.name,
                type: b.kind,
                countClass: b.countClass,
                items: b.items,
                visibleItems,
                summaryCount,
                isLowConf: b.isLowConf,
                isQalqala: b.isQalqala,
                qalqalaLetters: b.qalqalaLetters,
                sorts: b.sorts,
                boundaryCounts,
            });
        }
        return out;
    }

    // Live inputs for the staged-split boundary states: store segs + op log
    // (committed / in-progress splits and their is_wasl), pending post-split
    // picks, re-asked boundaries, the sidecar map (staged splits) and the
    // staged session picks.
    $: boundaryCtx = ((): BoundaryCtx => {
        void $segAllData; void $dirtyTick;
        return {
            chapterSegs: getChapterSegments,
            opLog: getChapterOpsSnapshot,
            splitGroupIndex: $splitGroupIndex,
            pendingWasl: $pendingWaslConfirm,
            waslRecheck: $waslRecheck,
            autoSplitMap: $autoSplitMap,
            stagedPicks: $stagedWaslPicks,
        };
    })();

    $: _baseDescriptors = buildBaseDescriptors($segValidation, $segAllData, chapter, $localeStore, $isOwner);
    $: categories = projectVisible(_baseDescriptors, lcThreshold, activeQalqalaLetter, qalqalaEndOfVerse, $valSortPrefs, $autoSplitMap, $valBoundaryFilter, boundaryCtx);
    // Filter signature: the subset of inputs that change the displayed list —
    // narrowing (chapter / LC threshold / qalqala letter / end-of-verse) plus
    // the active sort (so a sort change re-pins the open accordion's snapshot
    // against the freshly ordered list). Lifted to top-level so the re-pin
    // reactive can also react to sig flips while the same accordion stays open.
    $: _filterSig = `${chapter}|${lcThreshold}|${activeQalqalaLetter ?? ''}|${qalqalaEndOfVerse}|${JSON.stringify($valSortPrefs)}|${[...$valBoundaryFilter].join(',')}`;
    $: {
        // If the filter sig hasn't changed for a category, preserve its
        // context-shown map so structural edits (split/merge) that republish
        // the items array via identity shift don't reset Show Context toggles
        // mid-edit.
        for (const cat of categories) {
            if (_lastFilterSig[cat.type] !== _filterSig) {
                _lastFilterSig[cat.type] = _filterSig;
                contextStateByType[cat.type]?.clear();
            }
        }
    }
    $: hasAny = categories.length > 0;

    // ---- Flagged Issues (non-registry top accordion) ----
    // A manual "needs a second look" thread per segment. Deliberately NOT in
    // IssueRegistry — it must not leak into filters or issue-type counts. The
    // open-state uses a reserved key that can't collide with a registry kind.
    const FLAGGED_KEY = '__flagged__';
    $: flaggedSegs = ($segAllData?.segments ?? []).filter((s) => s.flag != null);

    // Consume a pending flag deep-link (e.g. from a "flag reply" notification):
    // open the Flagged accordion and scroll to the flagged segment once the
    // reciter's data has loaded. Consumed once, then cleared. For a targeted
    // reply we wait until the matching segment is present so we don't fire on a
    // previous reciter's stale flag list.
    $: maybeConsumeFlagDeepLink($pendingSegmentsDeepLink, flaggedSegs);

    async function maybeConsumeFlagDeepLink(dl: SegmentsDeepLink | null, flagged: typeof flaggedSegs): Promise<void> {
        if (!dl?.openFlagged || flagged.length === 0) return;
        if (dl.focusFlaggedUid && !flagged.some((s) => s.segment_uid === dl.focusFlaggedUid)) return;
        pendingSegmentsDeepLink.set(null);
        valUiOpenCategory.set(FLAGGED_KEY);
        await tick();
        const uid = dl.focusFlaggedUid;
        if (!uid) return;
        requestAnimationFrame(() => {
            const sel = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(uid) : uid;
            document
                .querySelector(`[data-flag-uid="${sel}"]`)
                ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
    }
    const canSeeFlaggerStore = can('segments.see_flagger_identity');
    $: canSeeFlagger = $canSeeFlaggerStore;

    // ---- Accordion auto-scroll ----
    // The main-list autoscroll (SegmentsList) ignores accordion plays, so the
    // open accordion keeps the focused card centred itself. Every card switch —
    // autoplay-advance, ↑/↓ nav, or a card / context play click — routes through
    // playFromSegment → playingSegmentIndex with origin 'accordion', so we react
    // to that and scroll to whichever row is now playing (main OR context).
    // `_ctxEpoch` bumps on any Show/Hide Context toggle so we also re-centre the
    // focused card when context rows appear/disappear and shove it off-screen.
    let _lastAccordionScrollKey = '';
    let _lastCtxEpoch = -1;
    let _ctxEpoch = 0;
    $: maybeAccordionAutoScroll($autoScrollEnabled, $playingSegmentIndex, $valUiOpenCategory, _ctxEpoch);
    function maybeAccordionAutoScroll(
        on: boolean,
        active: { chapter: number; index: number; origin?: string } | null,
        openCat: string | null,
        epoch: number,
    ): void {
        if (!on || openCat === null || !active || active.origin !== 'accordion') {
            _lastAccordionScrollKey = '';
            _lastCtxEpoch = epoch;
            return;
        }
        const key = `${active.chapter}:${active.index}`;
        const epochChanged = epoch !== _lastCtxEpoch;
        if (key === _lastAccordionScrollKey && !epochChanged) return;
        _lastAccordionScrollKey = key;
        _lastCtxEpoch = epoch;
        requestAnimationFrame(() => {
            document
                .querySelector<HTMLElement>(
                    `.seg-row[data-seg-chapter="${active.chapter}"][data-seg-index="${active.index}"]`,
                )
                ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        });
    }

    $: flaggedTitle = tr($localeStore, m.segments_validation_flagged_title());
    $: flaggedGuideAriaLabel = tr($localeStore, m.segments_validation_flagged_guide_aria_label());
    $: flaggedGuideTitle = tr($localeStore, m.segments_validation_flagged_guide_title());
    $: sortLabel = tr($localeStore, m.segments_validation_sort_label());
    $: boundaryFilterLabel = tr($localeStore, m.segments_validation_boundary_filter_label());
    $: boundaryLabels = ((): Record<BoundaryState, string> => (void $localeStore, {
        unset: tr($localeStore, m.segments_validation_boundary_unset()),
        wasl: tr($localeStore, m.segments_validation_boundary_wasl()),
        waqf: tr($localeStore, m.segments_validation_boundary_waqf()),
    }))();
    $: showConfidenceLabel = tr($localeStore, m.segments_validation_show_confidence_label());
    $: filterByLetterLabel = tr($localeStore, m.segments_validation_filter_by_letter_label());
    $: qalqalaEovButtonLabel = tr($localeStore, m.segments_validation_qalqala_eov_button());
    $: qalqalaEovTitle = tr($localeStore, m.segments_validation_qalqala_eov_title());
    $: showHideContextAllLabel = tr($localeStore, m.segments_validation_show_hide_context_all_button());

    // ---- Virtualization window for the open category ----
    $: openCat = categories.find((c) => c.type === openCategory) ?? null;
    // Displayed items inside the open accordion: pinned snapshot keys (in
    // open-time order), each rendered from live `visibleItems` when present,
    // else from the snapshot. Keeps cards stable across autosave-driven
    // segValidation refreshes while still letting per-item state (text,
    // button color) flow through from the live store.
    $: displayedItems = ((): SegValAnyItem[] => {
        if (!openCat) return [];
        const visible = openCat.visibleItems;
        const pin = $accordionPin;
        if (!pin || pin.category !== openCategory) return [...visible];
        const liveByKey = new Map<string, SegValAnyItem>();
        for (const it of visible) liveByKey.set(issueKey(it, openCat.type), it);
        const out: SegValAnyItem[] = [];
        for (const k of pin.keys) {
            const live = liveByKey.get(k);
            if (live) out.push(live);
            else if (pin.items[k]) out.push(pin.items[k]!);
        }
        return out;
    })();
    $: openTotal = displayedItems.length;

    // ---- Card-transition audio warmup ----
    //
    // Reviewers work through accordion cards in order. Each card's "lead seg"
    // (resolved per-type — see resolveCardLeadSeg) is what the reviewer
    // typically plays first. Cross-card transitions frequently span chapters
    // (e.g. missing_words[0]=4:127 → [1]=6:73), so the browser's forward
    // buffer can never cover the next card's audio. The chapter-load + hover
    // + within-card-next-sibling warmups can't help either — they're scoped
    // to a single chapter.
    //
    // Strategy:
    //   1. On category open / displayedItems change → warm card[0]'s lead.
    //   2. On `playingSegmentIndex` change → find which card holds the now-
    //      playing seg, and warm card[N+1]'s lead. Idempotent via warmup.ts
    //      dedupe so re-fires within 30 s are free.
    //
    // The (chapter, index) → card-index map rebuilds on every displayedItems
    // identity change. O(N · indices-per-card) but N is bounded by the
    // accordion's visible card count.
    let _cardOwnerByChIdx: Map<string, number> = new Map();
    $: {
        const m = new Map<string, number>();
        const items = displayedItems;
        const kind = openCategory;
        if (kind) {
            for (let i = 0; i < items.length; i++) {
                const it = items[i] as {
                    chapter?: number;
                    seg_index?: number;
                    seg_indices?: number[];
                };
                if (it?.chapter == null) continue;
                const indices = it.seg_indices ?? (it.seg_index != null ? [it.seg_index] : []);
                for (const idx of indices) {
                    const key = `${it.chapter}:${idx}`;
                    if (!m.has(key)) m.set(key, i);
                }
            }
        }
        _cardOwnerByChIdx = m;
    }

    // Warm a card's lead seg before the user clicks it. CBR goes through
    // `warmSegChapter`: probe the CDN (so the click plays direct, not via the
    // audio-proxy) and warm the chapter off the Space. For VBR chapters the
    // seg-clip URL is per-segment and can't be pre-cached — fall back to the
    // byte-Range warmup, which still primes the server-side ffmpeg read.
    function _warmAccordionLead(lead: ReturnType<typeof resolveCardLeadSeg>): void {
        if (!lead) return;
        const reciter = $selectedReciter;
        if (!reciter) return;
        const audioUrl = lead.audio_url
            ?? $segAllData?.audio_by_chapter?.[String(lead.chapter)]
            ?? '';
        if (!audioUrl) return;
        const isVbr = lead.chapter != null
            && (lead.chapter as number) > 0
            && ($segAllData?.reciter_vbr_chapters ?? []).includes(lead.chapter as number);
        if (isVbr) {
            warmSeg(lead, reciter);
            return;
        }
        warmSegChapter(lead, reciter, audioUrl);
    }

    /**
     * Pre-fetch chapter-overview peaks for every chapter represented in the
     * currently-open accordion. One batched request via ``_fetchPeaks`` —
     * populates ``getWaveformPeaks(url)`` cache so per-card canvases
     * (including not-yet-scrolled-into-view context cards) paint in-memory
     * via slice instead of paying ~1s ffmpeg per Play click via
     * ``/segment-peaks`` POST. Reuses the same chapter-extraction approach
     * as ``_warmAccordionLead`` but covers the full ``displayedItems`` list,
     * not just the lead card.
     *
     * Dedup: chapters already cached (any prior fetch / chapter pick already
     * populated this entry) are filtered out — sending only the misses. The
     * route's own LRU response cache absorbs repeat opens of the same
     * accordion across the session.
     *
     * Wire/CPU bound: a typical 5-15-chapter accordion is ~100 KiB-1 MiB on
     * the wire and ~300-700 ms server-side. Big-N categories like Qalqala
     * (663 segs across many chapters) may approach the all-114 envelope —
     * the dedup gate keeps that to the chapters not yet seen this session.
     */
    function _prefetchAccordionPeaks(): void {
        const reciter = $selectedReciter;
        if (!reciter || !openCategory) return;
        const audioByChapter = $segAllData?.audio_by_chapter ?? {};
        const wanted = new Set<number>();
        for (const item of displayedItems) {
            const lead = resolveCardLeadSeg(item, openCategory);
            if (!lead || lead.chapter == null) continue;
            const url = lead.audio_url ?? audioByChapter[String(lead.chapter)] ?? '';
            if (!url) continue;
            if (getWaveformPeaks(url)?.peaks?.length) continue;
            wanted.add(lead.chapter as number);
        }
        if (wanted.size === 0) return;
        _fetchPeaks(reciter, Array.from(wanted).sort((a, b) => a - b));
    }

    // Card-0 warmup — once per category OPEN. Deliberately not keyed on item-0:
    // every autosave's validation refresh drops the card just fixed, so an
    // item-0 key re-fired a chapter warm + peaks prefetch right as the user
    // clicked the next card, competing with that play.
    let _lastCard0Cat: string | null = null;
    $: {
        const kind = openCategory;
        const first = displayedItems[0];
        if (!kind) {
            _lastCard0Cat = null;
        } else if (first && kind !== _lastCard0Cat) {
            _lastCard0Cat = kind;
            _warmAccordionLead(resolveCardLeadSeg(first, kind));
            _prefetchAccordionPeaks();
        }
    }

    // Next-card warmup driven by playingSegmentIndex. When the playing seg
    // first belongs to card N (where the previous play was in card N-1 or no
    // card), warm card[N+1]'s lead so by the time the reviewer scrolls to it
    // and clicks, the shadow audio element has already filled the browser
    // HTTP cache for the next chapter. The primary segPort's eventual swap
    // hits the warm cache → near-instant canplay (same path as in-chapter
    // chapter-load prewarm).
    let _lastPlayedCardIdx = -1;
    $: {
        const playing = $playingSegmentIndex;
        if (!playing || !openCategory) {
            _lastPlayedCardIdx = -1;
        } else {
            const key = `${playing.chapter}:${playing.index}`;
            const owner = _cardOwnerByChIdx.get(key);
            if (owner != null && owner !== _lastPlayedCardIdx) {
                _lastPlayedCardIdx = owner;
                const nextItem = displayedItems[owner + 1];
                if (nextItem) {
                    _warmAccordionLead(resolveCardLeadSeg(nextItem, openCategory));
                }
            }
        }
    }

    // Virtualization stays ACTIVE during editMode. To keep the editing row
    // mounted — so scrolling away doesn't evict the edit panel mid-flow —
    // we expand the slice window to include whichever card resolves to the
    // editing segment UID. All other cards virtualize normally, so a 200-row
    // accordion doesn't mount 200 SegmentRows every reactive tick during
    // edit.
    $: virtualize = openTotal > VAL_VIRTUALIZE_THRESHOLD;
    // Resolve editing seg's (chapter, index) — needed to locate the card
    // owning it within `visibleItems`. Only recompute when the UID changes
    // or segAllData identity shifts (structural edits reindex).
    $: editingCoords = ((): { chapter: number; index: number } | null => {
        const uid = $editingSegUid;
        if (!uid) return null;
        const segs = $segAllData?.segments;
        if (!segs) return null;
        const seg = segs.find((s) => s.segment_uid === uid);
        if (!seg || seg.chapter == null) return null;
        return { chapter: seg.chapter, index: seg.index };
    })();
    // Find the editing card's index within the open category's visible list.
    // `seg_index` is used for the legacy fallback path; uid-carrying items
    // resolve via filterStaleIssues + resolveIssueSeg uid-first.
    $: editingItemIdx = ((): number => {
        if (!virtualize || !editingCoords || !openCat) return -1;
        const items = displayedItems as ReadonlyArray<{
            chapter?: number;
            seg_index?: number;
            seg_indices?: number[];
        }>;
        for (let i = 0; i < items.length; i++) {
            const it = items[i];
            if (!it || it.chapter !== editingCoords.chapter) continue;
            if (it.seg_index === editingCoords.index) return i;
            if (it.seg_indices?.includes(editingCoords.index)) return i;
        }
        return -1;
    })();
    $: baseStartIdx = (void cardOffsets, virtualize
        ? Math.max(0, rowAtOffset(cardsScrollTop) - BUFFER_ROWS)
        : 0);
    $: baseEndIdx = (void cardOffsets, virtualize
        ? Math.min(openTotal, rowAtOffset(cardsScrollTop + cardsViewportHeight) + 1 + BUFFER_ROWS)
        : openTotal);
    // Expand the window to cover the editing card so scrolling away doesn't
    // unmount it. Bound check against openTotal handles items added/removed
    // by re-validation while still in edit mode.
    $: startIdx = virtualize && editingItemIdx >= 0
        ? Math.min(baseStartIdx, editingItemIdx)
        : baseStartIdx;
    $: endIdx = virtualize && editingItemIdx >= 0
        ? Math.max(baseEndIdx, editingItemIdx + 1)
        : baseEndIdx;
    $: topSpacerPx = virtualize ? (cardOffsets[startIdx] ?? 0) : 0;
    $: bottomSpacerPx = virtualize
        ? Math.max(0, totalContentHeight - (cardOffsets[endIdx] ?? totalContentHeight))
        : 0;

    // ---- ErrorCard refs (window-slice array, synced to absolute Map in afterUpdate) ----
    // `windowCardRefs` holds bind:this refs for the currently rendered slice.
    // Rebuilt into `cardRefMap` in the shared afterUpdate block above so
    // handleShowAllContext can reach every visible card by absolute index.
    let windowCardRefs: ErrorCard[] = [];
    const cardRefMap: Map<number, ErrorCard> = new Map();

    // ---- "Show/Hide All Context" per category ----
    // Operates on mounted window cards; also writes contextStateByType so cards
    // that scroll into view later restore the correct initial state.
    function handleShowAllContext(type: string): void {
        const ctxMap = getContextState(type);
        const cat = categories.find((c) => c.type === type);
        if (!cat) return;
        const anyShown = Array.from(cardRefMap.values()).some((c) => c?.getIsContextShown());
        const newState = !anyShown;
        // The button is only wired on the currently-open accordion. Use the
        // pin-aware `displayedItems.length` so indices match what the user
        // actually sees rendered (pin may include cards that have dropped
        // from the live `visibleItems` since open-time).
        const total = type === openCategory ? displayedItems.length : cat.visibleItems.length;
        for (let i = 0; i < total; i++) {
            ctxMap.set(i, newState);
        }
        for (const c of cardRefMap.values()) {
            if (!c) continue;
            if (newState) c.showContextForced();
            else c.hideContextForced();
        }
    }

    // ---- Accordion toggle handler (factored out to avoid TS cast in template) ----
    function handleAccordionToggle(e: Event, type: string): void {
        const detailsEl = e.currentTarget as HTMLDetailsElement;
        const isOpen = detailsEl.open;
        const cur = openCategory;
        valUiOpenCategory.set(isOpen ? type : (cur === type ? null : cur));
    }

    // Capture the open-time snapshot of visible items so autosave-driven
    // segValidation refreshes don't yank cards out from under the user.
    // Re-capture when `openCategory` *transitions* (open / close / switch
    // category) — not on every reactive tick of `categories` (which republishes
    // on each segValidation refresh).
    //
    // Also re-capture when openCategory is non-null but the pin store is
    // empty: this fires when returning to the Segments tab after a leave
    // that cleared the pin while the accordion's DOM state remained open.
    let _prevPinCategory: string | null = null;
    $: {
        const next = openCategory;
        const pinNow = $accordionPin;
        const onSegments = $activeTab === TAB_NAMES.SEGMENTS;
        const needsRepin = onSegments && next != null && (pinNow == null || pinNow.category !== next);
        if (next !== _prevPinCategory || needsRepin) {
            if (next == null) {
                clearAccordionPin();
            } else if (onSegments) {
                const cat = categories.find((c) => c.type === next);
                if (cat) pinAccordion(next, cat.visibleItems, issueKey);
                else clearAccordionPin();
            }
            _prevPinCategory = next;
        }
    }

    // When the user changes a filter (qalqala letter / end-of-verse / LC
    // threshold), the open accordion's pinned snapshot must be re-captured
    // against the freshly filtered list — otherwise `displayedItems` would
    // keep rehydrating filtered-out items via `pin.items[k]`. We piggyback
    // on `_filterSig`: when it flips, defer one microtask so `categories`
    // has flushed under Svelte 5's batched reactivity, then re-pin from the
    // current `cat.visibleItems`. Autosave does not change `_filterSig`, so
    // the pin's autosave-stability guarantee is unaffected.
    let _lastPinSig: string | null = null;
    $: if (openCategory != null && _filterSig !== _lastPinSig) {
        _lastPinSig = _filterSig;
        queueMicrotask(() => {
            const next = openCategory;
            if (next == null) return;
            if ($activeTab !== TAB_NAMES.SEGMENTS) return;
            const cat = categories.find((c) => c.type === next);
            if (cat) pinAccordion(next, cat.visibleItems, issueKey);
        });
    }

    function openGuide(e: MouseEvent, type: string): void {
        e.preventDefault();
        e.stopPropagation();
        // Drive the shared modal host (mounted in SegmentsTab) so the same
        // path serves both this button and the onboarding gate modal.
        openGuideModal(type, e.currentTarget as HTMLElement);
    }

    // ---- Stable composite each-key for issue cards ----
    // Object-reference keying caused every ErrorCard to remount whenever
    // `$segValidation` republished (re-validate after save). A composite
    // key based on the underlying segment identity survives republishes,
    // so cards stay mounted and their transient state is preserved.
    function issueKey(it: SegValAnyItem, kind: string): string {
        const any = it as {
            segment_uid?: string | null;
            chapter: number;
            seg_index?: number;
            verse_key?: string;
        };
        if (any.segment_uid) return `${kind}:${any.segment_uid}`;
        if (any.seg_index != null) return `${kind}:${any.chapter}:${any.seg_index}`;
        const mw = it as { missing_words?: number[]; seg_indices?: number[] };
        const words = mw.missing_words?.join(',') ?? '';
        const indices = mw.seg_indices?.join(',') ?? '';
        return `${kind}:${any.chapter}:${any.verse_key ?? ''}:${words}:${indices}`;
    }

    // ---- LC slider debounce ----
    // Drag updates the input's value live (visible thumb stays smooth) but
    // we only republish `lcThreshold` after a short idle so visibleItems
    // doesn't re-filter+sort on every intermediate keystroke value.
    let lcSliderRaw: number = lcThreshold;
    let _lcDebounceTimer: ReturnType<typeof setTimeout> | null = null;
    const LC_DEBOUNCE_MS = 80;
    function onLcSliderInput(e: Event): void {
        const v = parseInt((e.currentTarget as HTMLInputElement).value, 10);
        if (Number.isNaN(v)) return;
        lcSliderRaw = v;
        if (_lcDebounceTimer !== null) clearTimeout(_lcDebounceTimer);
        _lcDebounceTimer = setTimeout(() => {
            _lcDebounceTimer = null;
            lcThreshold = lcSliderRaw;
        }, LC_DEBOUNCE_MS);
    }
    onDestroy(() => {
        if (_lcDebounceTimer !== null) clearTimeout(_lcDebounceTimer);
    });

    // ---- Context state sync: card notifies panel when user toggles Show/Hide ----
    function onCardContextChange(type: string, absIdx: number, shown: boolean): void {
        getContextState(type).set(absIdx, shown);
        // Bump the epoch so the auto-scroll driver re-centres the focused card —
        // showing/hiding context rows shifts it and would otherwise leave it
        // off-screen.
        _ctxEpoch += 1;
    }

    function relayCardContext(catType: string, absIdx: number, ev: CustomEvent<boolean>): void {
        onCardContextChange(catType, absIdx, ev.detail);
    }

    function contextRelay(catType: string, absIdx: number): (_ev: CustomEvent<boolean>) => void {
        return (_ev) => relayCardContext(catType, absIdx, _ev);
    }
</script>

{#if hasAny || flaggedSegs.length > 0}
    <div class="seg-validation-panel">
        {#if label}
            <div class="val-section-label">{label}</div>
        {/if}

        {#if flaggedSegs.length > 0}
            <details
                class="flagged-accordion"
                data-category={FLAGGED_KEY}
                open={openCategory === FLAGGED_KEY}
                on:toggle={(e) => handleAccordionToggle(e, FLAGGED_KEY)}
            >
                <summary class="val-summary">
                    <button
                        type="button"
                        class="val-guide-btn"
                        class:unread={$currentUser.hf_user_id != null
                            && !isGuideRead($currentUser.guides_read, 'flagging')}
                        aria-label={flaggedGuideAriaLabel}
                        title={flaggedGuideTitle}
                        on:click={(e) => openGuide(e, 'flagging')}
                    >?</button>
                    <span class="val-summary-main">
                        <span class="val-summary-title">{flaggedTitle}</span>
                        <span class="val-count flagged-count">{flaggedSegs.length}</span>
                    </span>
                </summary>

                {#if openCategory === FLAGGED_KEY}
                    <div class="val-cards-container flagged-cards">
                        {#each flaggedSegs as fseg (fseg.segment_uid ?? `${fseg.chapter}:${fseg.index}`)}
                            <FlaggedCard seg={fseg} {canSeeFlagger} />
                        {/each}
                    </div>
                {/if}
            </details>
        {/if}

        {#each categories as cat (cat.type)}
            <details
                data-category={cat.type}
                open={openCategory === cat.type}
                on:toggle={(e) => handleAccordionToggle(e, cat.type)}
            >
                <summary class="val-summary">
                    {#if hasAccordionGuide(cat.type)}
                        <button
                            type="button"
                            class="val-guide-btn"
                            class:unread={$currentUser.hf_user_id != null
                                && !isGuideRead($currentUser.guides_read, cat.type)}
                            aria-label={m.segments_validation_open_guide_aria_label({ name: cat.name })}
                            title={m.segments_validation_open_guide_title({ name: cat.name })}
                            on:click={(e) => openGuide(e, cat.type)}
                        >?</button>
                    {/if}
                    <span class="val-summary-main">
                        <span class="val-summary-title">{cat.name}</span>
                        <span class="val-count {cat.countClass}" data-lc-count>
                            {(cat.isLowConf || cat.isQalqala) ? cat.visibleItems.length : cat.summaryCount}
                        </span>
                    </span>
                </summary>

                <!-- Sort pills (per registry `sorts`) -->
                {#if cat.sorts && cat.sorts.length > 0}
                    {@const active = resolveSort($valSortPrefs, cat.type)}
                    <div class="lc-slider-row val-sort-row">
                        <span class="lc-slider-label">{sortLabel}</span>
                        {#each cat.sorts as opt (opt.kind)}
                            {@const isActive = active?.kind === opt.kind}
                            <button
                                class="val-btn val-cross val-sort-pill"
                                class:active={isActive}
                                aria-pressed={isActive}
                                title={isActive
                                    ? m.segments_validation_sort_pill_active_title({ label: SORT_META[opt.kind].label() })
                                    : m.segments_validation_sort_pill_title({ label: SORT_META[opt.kind].label() })}
                                on:click={() => (isActive ? toggleDir(cat.type) : selectSort(cat.type, opt.kind))}
                            >{SORT_META[opt.kind].label()}{#if isActive && active}<span class="val-sort-arrow">{active.dir === 'asc' ? '▲' : '▼'}</span>{/if}</button>
                        {/each}
                    </div>
                {/if}

                <!-- Boundary chips (Cross-verse, Missed Waqf): Unset · Wasl · Waqf, each
                     with its boundary count; multi-select filter. -->
                {#if cat.boundaryCounts}
                    <div class="lc-slider-row val-boundary-row">
                        <span class="lc-slider-label">{boundaryFilterLabel}</span>
                        {#each BOUNDARY_STATES as st (st)}
                            {@const on = $valBoundaryFilter.has(st)}
                            <button
                                class="val-btn val-cross val-boundary-chip val-boundary-{st}"
                                class:active={on}
                                aria-pressed={on}
                                title={m.segments_validation_boundary_chip_title({ label: boundaryLabels[st] })}
                                on:click={() => toggleBoundaryState(st)}
                            >{boundaryLabels[st]}<span class="val-boundary-count">{cat.boundaryCounts[st]}</span></button>
                        {/each}
                    </div>
                {/if}

                <!-- LC slider (Low Confidence only) -->
                {#if cat.isLowConf}
                    <div class="lc-slider-row">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label class="lc-slider-label">
                            {showConfidenceLabel}
                            <span class="lc-slider-val">{lcSliderRaw}%</span>
                        </label>
                        <input
                            type="range"
                            class="lc-slider"
                            min="50"
                            max="99"
                            step="1"
                            value={lcSliderRaw}
                            on:input={onLcSliderInput}
                        />
                    </div>
                {/if}

                <!-- Qalqala letter filter -->
                {#if cat.isQalqala && cat.qalqalaLetters.length > 0}
                    <div class="lc-slider-row qalqala-filter-row">
                        <span class="lc-slider-label">{filterByLetterLabel}</span>
                        {#each cat.qalqalaLetters as letter}
                            <button
                                class="val-btn val-cross qalqala-letter-btn"
                                class:active={activeQalqalaLetter === letter}
                                title={m.segments_validation_qalqala_letter_title({ letter })}
                                data-letter={letter}
                                on:click={() => {
                                    activeQalqalaLetter = activeQalqalaLetter === letter ? null : letter;
                                }}
                            >{letter}</button>
                        {/each}
                        <button
                            class="val-btn val-cross qalqala-eov-btn"
                            class:active={qalqalaEndOfVerse}
                            title={qalqalaEovTitle}
                            on:click={() => { qalqalaEndOfVerse = !qalqalaEndOfVerse; }}
                        >{qalqalaEovButtonLabel}</button>
                    </div>
                {/if}

                {#if openCategory === cat.type}
                    <!-- "Show All Context" + cards -->
                    <div class="val-ctx-all-row">
                        <button
                            class="val-action-btn val-action-btn-muted"
                            on:click={() => handleShowAllContext(cat.type)}
                        >{showHideContextAllLabel}</button>
                    </div>

                    <div
                        class="val-cards-container"
                        bind:this={cardsContainerEl}
                        on:scroll={onCardsScroll}
                    >
                        {#if topSpacerPx > 0}
                            <div class="val-cards-spacer" style="height: {topSpacerPx}px" aria-hidden="true"></div>
                        {/if}
                        {#each displayedItems.slice(startIdx, endIdx) as issue, localIdx (issueKey(issue, cat.type))}
                            <ErrorCard
                                bind:this={windowCardRefs[localIdx]}
                                category={cat.type}
                                item={issue}
                                initialContextShown={getContextState(cat.type).get(startIdx + localIdx) ?? false}
                                on:contextchange={contextRelay(cat.type, startIdx + localIdx)}
                            />
                        {/each}
                        {#if bottomSpacerPx > 0}
                            <div class="val-cards-spacer" style="height: {bottomSpacerPx}px" aria-hidden="true"></div>
                        {/if}
                    </div>
                {/if}
            </details>
        {/each}
    </div>
{/if}

<style>
    /* Flagged Issues — a warm-amber top accordion, set apart from the cyan/red
       validation categories so a manual flag reads as "a human asked for a
       second look", not an automated validation error. */
    .flagged-count {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 18px;
        margin-inline-start: 4px;
        padding: 1px 7px;
        font-family: var(--font-mono);
        font-size: 11px;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--state-warn-fg);
        background: var(--state-warn-bg);
        border: 1px solid var(--state-warn-border);
        border-radius: var(--r-pill);
    }

    .flagged-cards {
        padding-top: var(--s-2);
    }
</style>
