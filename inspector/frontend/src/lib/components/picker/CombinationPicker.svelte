<script lang="ts">
    /**
     * CombinationPicker — modal picker keyed on (reciter, delivery) pairs.
     *
     * Sister to ReciterPicker, but every row is a delivery (one reciter can
     * appear multiple times in different buckets). The user's active claim
     * is pinned to the top in a "Your active claim" section; the rest are
     * grouped by `delivery.bucket` in the workflow order
     *   available_for_review → under_review → published.
     *
     * Only emits `select` for a non-null delivery. Audio preview is a
     * bubbled `preview` event — this component doesn't touch audio itself.
     */
    import { createEventDispatcher, onMount, tick } from 'svelte';
    import { get } from 'svelte/store';

    import { localizeDigits } from '$lib/i18n/format';
    import { localeStore, tr } from '$lib/i18n/locale-store';
    import * as m from '$lib/paraglide/messages';

    // Picker subscribes to the shared dashboard catalog store instead of
    // re-fetching /api/public/reciters. loadCatalog() is idempotent so the
    // first caller (Dashboard, Segments-tab context resolver, or picker open)
    // wins and others share the cached snapshot.
    import { catalogData, loadCatalog } from '../../../tabs/dashboard/stores/catalog-data';
    import { fetchAdminReviews } from '../../api/admin-reviews';
    import {
        type Axis,
        buildSchemaDescriptor,
        type SchemaDescriptor,
    } from '../../catalog/schema-descriptor';
    import { hasCapability } from '../../stores/capabilities';
    import { currentUser } from '../../stores/current-user';
    import type { BucketCounts, PublicDelivery, PublicReciter } from '../../types/generated/schemas';
    import type { PublicBucket } from '../../types/public-bucket';
    import { tagLabel } from '../../utils/axis-labels';
    import { compactCoverageLabel, compactHoursLabel } from '../../utils/delivery-label';
    import { type FacetSpec, recomputeFacets } from '../../utils/facets';
    import { filterByFields } from '../../utils/fuzzy-match';
    import Modal from '../Modal.svelte';
    import ReciterChip from '../ReciterChip.svelte';
    import SearchInput from '../SearchInput.svelte';
    import StatePill from '../StatePill.svelte';
    import type { CombinationSelection, InitialFilter } from './combination-picker-types';
    import PickerFilterRail from './PickerFilterRail.svelte';
    import PickerFooter from './PickerFooter.svelte';
    import PickerReviewStatus from './PickerReviewStatus.svelte';
    import PickerStateTabs from './PickerStateTabs.svelte';

    interface Combo {
        reciter: PublicReciter;
        delivery: PublicDelivery;
    }

    export let open = true;
    export let title = m.common_picker_default_title();
    export let initialFilter: InitialFilter = {};
    /** Buckets eligible to appear in the picker. */
    export let allowedBuckets: readonly PublicBucket[] = [
        'available_for_review',
        'under_review',
        'published',
    ];

    const dispatch = createEventDispatcher<{
        select: CombinationSelection;
        close: void;
        cancel: void;
    }>();

    // Subscribe reactively to `catalogData` so the picker re-renders when
    // a claim/unclaim triggers `loadCatalog(true)` elsewhere (e.g. from
    // SegmentsTab._refreshTask). The previous imperative `reciters = snap`
    // assignment after `await loadCatalog()` froze the picker at open-time
    // state — claiming Husary while the picker was open left it filtered
    // out by `allowedBuckets` until the user closed and reopened.
    $: reciters = $catalogData.reciters;
    let descriptor: SchemaDescriptor | null = null;
    let loading = true;
    let error: string | null = null;

    // Per-slug claim overlay (claimer login + marked-ready), populated only for
    // callers who hold `reviews.view` — the same data the Admin → Reviews tab
    // shows. Empty for everyone else, who still get the READY badge via the
    // public `delivery.marked_ready` flag but never the username.
    interface ReviewStatus {
        login: string | null;
        markedReady: boolean;
    }
    let reviewStatus = new Map<string, ReviewStatus>();

    // READY comes from the public catalog flag (visible to everyone); the
    // claimer's login only from the `reviews.view` overlay above.
    $: statusFor = (c: Combo): ReviewStatus | null => {
        const admin = reviewStatus.get(c.delivery.slug);
        const markedReady = (admin?.markedReady ?? false) || c.delivery.marked_ready === true;
        const login = admin?.login ?? null;
        return markedReady || login ? { login, markedReady } : null;
    };

    let search = initialFilter.search ?? '';
    let activeBucket: PublicBucket | null = initialFilter.bucket ?? null;
    let activeFilters: Record<string, Set<string>> = {};
    let focusedIdx = -1;

    let searchInputEl: SearchInput | null = null;
    let listEl: HTMLDivElement | null = null;

    // Group order for non-pinned rows.
    const GROUP_ORDER: PublicBucket[] = [
        'available_for_review',
        'under_review',
        'published',
    ];

    onMount(() => {
        void load();
    });

    async function load(): Promise<void> {
        loading = true;
        error = null;
        try {
            await loadCatalog();
            const snap = get(catalogData);
            if (snap.error) {
                throw new Error(snap.error);
            }
            // `reciters` / `stats` flow through the reactive `$:` bindings
            // above; we only need to (re)build the descriptor here.
            const allDeliveries = snap.reciters.flatMap((r) => r.deliveries);
            descriptor = buildSchemaDescriptor(allDeliveries);
            for (const axis of descriptor.axes) {
                activeFilters[axis.key] = new Set();
            }
            activeFilters = activeFilters;
            await tick();
            searchInputEl?.focus();
        } catch (e) {
            error = (e as Error).message ?? m.common_picker_load_error_fallback();
        } finally {
            loading = false;
        }
        void loadReviewStatus();
    }

    // Lazy claim/marked-ready overlay — fired only when the caller holds
    // `reviews.view`. Failures (e.g. a 403 from a cap mismatch) leave the map
    // empty and never break the picker; the catalog still renders.
    async function loadReviewStatus(): Promise<void> {
        if (!hasCapability(get(currentUser), 'reviews.view')) return;
        try {
            const resp = await fetchAdminReviews();
            const next = new Map<string, ReviewStatus>();
            for (const row of resp.rows ?? []) {
                if (row.open_claim) {
                    next.set(row.slug, {
                        login: row.open_claim.login ?? null,
                        markedReady: (row.open_claim.marked_ready_at ?? null) !== null,
                    });
                }
            }
            reviewStatus = next;
        } catch {
            /* admin overlay is best-effort — picker works without it */
        }
    }

    // Filter axes to the three relevant for combinations (no status,
    // no mushaf coverage, no recording context) — the state tabs already
    // cover bucket; the rail keeps cross-cutting taxonomy axes only.
    $: railAxes = (descriptor?.axes ?? []).filter((a) =>
        a.key === 'riwayah' || a.key === 'style' || a.key === 'channel',
    );

    // Flatten reciters into combos, keeping only deliveries whose own
    // bucket is in allowedBuckets — a reciter can show up twice if its
    // deliveries land in different buckets.
    $: allCombos = reciters.flatMap<Combo>((r) =>
        r.deliveries
            .filter((d) => allowedBuckets.includes(d.bucket))
            .map((d) => ({ reciter: r, delivery: d })),
    );

    $: bucketScoped = activeBucket
        ? allCombos.filter((c) => c.delivery.bucket === activeBucket)
        : allCombos;

    $: facetSpecs = railAxes.map<FacetSpec<Combo>>((axis: Axis) => ({
        key: axis.key,
        tagsOf: (c: Combo) => [...axis.tagsOf(c.delivery)],
    }));

    $: facetResult = recomputeFacets(bucketScoped, activeFilters, facetSpecs);
    $: facetVisible = bucketScoped.filter((_, i) => facetResult.rowVisibility[i]);
    $: visible = filterByFields(
        facetVisible,
        search,
        (c) => [c.reciter.name, c.reciter.name_ar],
    );

    // Split into pinned (active claims) + groups by bucket.
    // Owners can hold multiple simultaneous claims — pin all of them.
    $: mineSlugs = new Set($currentUser?.active_claims ?? ($currentUser?.active_claim ? [$currentUser.active_claim] : []));
    $: mineRows = visible.filter((c) => mineSlugs.has(c.delivery.slug));
    $: restRows = mineRows.length > 0
        ? visible.filter((c) => !mineSlugs.has(c.delivery.slug))
        : visible;
    $: groupedRest = (() => {
        const out: Array<{ bucket: PublicBucket; rows: Combo[] }> = [];
        for (const b of GROUP_ORDER) {
            const rows = restRows.filter((c) => c.delivery.bucket === b);
            if (rows.length > 0) out.push({ bucket: b, rows });
        }
        return out;
    })();

    // Flat list of all visible rows in display order — drives keyboard nav.
    $: orderedRows = mineRows.length > 0
        ? [...mineRows, ...groupedRest.flatMap((g) => g.rows)]
        : groupedRest.flatMap((g) => g.rows);

    // Tab pills count deliveries (combos), so they agree with the per-bucket
    // section head-counts and the SearchInput total. `/api/public/stats` counts
    // reciters by primary_bucket (one per reciter); a reciter with deliveries in
    // several buckets would make those pills disagree with the rows the picker
    // actually lists. Derive from allCombos so every count surface matches.
    $: tabCounts = allCombos.reduce<Partial<BucketCounts>>((acc, c) => {
        acc[c.delivery.bucket] = (acc[c.delivery.bucket] ?? 0) + 1;
        return acc;
    }, {});

    $: searchPlaceholder = tr($localeStore, m.common_picker_search_placeholder());
    $: loadingLabel = tr($localeStore, m.common_picker_loading());
    $: noMatchesLabel = tr($localeStore, m.common_picker_no_matches());
    $: mineSectionLabel = tr($localeStore, localizeDigits(m.common_picker_section_your_active_claims({ count: mineRows.length })));

    function toggleFacet(detail: { axis: string; tag: string }): void {
        const set = new Set(activeFilters[detail.axis] ?? []);
        if (set.has(detail.tag)) set.delete(detail.tag);
        else set.add(detail.tag);
        activeFilters = { ...activeFilters, [detail.axis]: set };
        focusedIdx = -1;
    }

    function selectBucket(b: PublicBucket | null): void {
        activeBucket = b;
        focusedIdx = -1;
    }

    function commit(combo: Combo): void {
        dispatch('select', {
            kind: 'combination',
            reciter: combo.reciter,
            delivery: combo.delivery,
        });
        dispatch('close');
    }

    function onSearchInput(value: string): void {
        search = value;
        focusedIdx = orderedRows.length > 0 ? 0 : -1;
    }

    function onKey(e: KeyboardEvent): void {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            focusedIdx = Math.min(focusedIdx + 1, orderedRows.length - 1);
            if (focusedIdx < 0 && orderedRows.length > 0) focusedIdx = 0;
            scrollFocusedIntoView();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            focusedIdx = Math.max(focusedIdx - 1, 0);
            scrollFocusedIntoView();
        } else if (e.key === 'Enter') {
            const c = orderedRows[focusedIdx];
            if (c) {
                e.preventDefault();
                commit(c);
            }
        } else if (e.key === '/') {
            const active = document.activeElement as HTMLElement | null;
            if (active?.tagName !== 'INPUT') {
                e.preventDefault();
                searchInputEl?.focus();
            }
        } else if (e.key === 'Escape') {
            e.preventDefault();
            dispatch('cancel');
            dispatch('close');
        }
    }

    function scrollFocusedIntoView(): void {
        if (!listEl) return;
        const el = listEl.querySelector<HTMLElement>('.combo-row.focused');
        el?.scrollIntoView({ block: 'nearest' });
    }

    function onClose(): void {
        dispatch('cancel');
        dispatch('close');
    }

    function deliveryMeta(d: PublicDelivery): string {
        const parts: string[] = [];
        if (d.riwayah) parts.push(tagLabel(descriptor, 'riwayah', d.riwayah));
        if (d.style) parts.push(tagLabel(descriptor, 'style', d.style));
        if (d.recording_year) parts.push(String(d.recording_year));
        if (d.channel_name) parts.push(d.channel_name);
        else if (d.channel) parts.push(tagLabel(descriptor, 'channel', d.channel));
        return parts.filter(Boolean).join(' · ');
    }
</script>

<Modal {open} {title} on:close={onClose}>
    <div
        class="picker modal-shell"
        role="dialog"
        aria-label={title}
        on:keydown={onKey}
        tabindex="-1"
    >
        <div class="picker-controls">
            <div class="picker-controls-search">
                <SearchInput
                    bind:this={searchInputEl}
                    value={search}
                    placeholder={searchPlaceholder}
                    count={orderedRows.length}
                    total={allCombos.length}
                    on:input={(e) => onSearchInput(e.detail)}
                />
            </div>
            <div class="picker-controls-tabs">
                <PickerStateTabs
                    {activeBucket}
                    {allowedBuckets}
                    totalCount={allCombos.length}
                    counts={tabCounts}
                    on:select={(e) => selectBucket(e.detail)}
                />
            </div>
        </div>

        <div class="body">
            {#if descriptor}
                <PickerFilterRail
                    axes={railAxes}
                    {activeFilters}
                    perFacetCounts={facetResult.perFacetCounts}
                    on:toggle={(e) => toggleFacet(e.detail)}
                />
            {/if}

            <div class="list" bind:this={listEl}>
                {#if loading}
                    <div class="state">{loadingLabel}</div>
                {:else if error}
                    <div class="state error">{error}</div>
                {:else if orderedRows.length === 0}
                    <div class="state">{noMatchesLabel}</div>
                {:else}
                    {#if mineRows.length > 0}
                        <div class="picker-section-head mine-head">
                            <span class="mine-dot" aria-hidden="true"></span>
                            {mineSectionLabel}
                        </div>
                        {#each mineRows as c (c.delivery.slug)}
                            {@const idx = orderedRows.indexOf(c)}
                            {@const st = statusFor(c)}
                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                            <div
                                class="combo-row"
                                class:focused={idx === focusedIdx}
                                role="button"
                                tabindex="0"
                                on:click={() => commit(c)}
                                on:mouseenter={() => (focusedIdx = idx)}
                            >
                                <ReciterChip
                                    name={c.reciter.name}
                                    nameAr={c.reciter.name_ar}
                                    country={c.reciter.country}
                                    subline={deliveryMeta(c.delivery)}
                                    bucket={c.delivery.bucket}
                                    variant="compact"
                                />
                                {#if st}
                                    <PickerReviewStatus login={st.login} markedReady={st.markedReady} />
                                {/if}
                                <div class="row-figures">
                                    <span class="coverage">{compactCoverageLabel(c.delivery)}</span>
                                    <span class="dur">{compactHoursLabel(c.delivery)}</span>
                                </div>
                            </div>
                        {/each}
                    {/if}
                    {#each groupedRest as group (group.bucket)}
                        <div class="picker-section-head bucket-head">
                            <StatePill state={group.bucket} size="sm" />
                            <span class="head-count">{tr($localeStore, localizeDigits(group.rows.length))}</span>
                        </div>
                        {#each group.rows as c (c.delivery.slug)}
                            {@const idx = orderedRows.indexOf(c)}
                            {@const st = statusFor(c)}
                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                            <div
                                class="combo-row"
                                class:focused={idx === focusedIdx}
                                role="button"
                                tabindex="0"
                                on:click={() => commit(c)}
                                on:mouseenter={() => (focusedIdx = idx)}
                            >
                                <!-- No per-row state pill here: the section head
                                     already groups by bucket, so repeating it on
                                     every row is redundant noise. -->
                                <ReciterChip
                                    name={c.reciter.name}
                                    nameAr={c.reciter.name_ar}
                                    country={c.reciter.country}
                                    subline={deliveryMeta(c.delivery)}
                                    bucket={null}
                                    variant="compact"
                                />
                                {#if st}
                                    <PickerReviewStatus login={st.login} markedReady={st.markedReady} />
                                {/if}
                                <div class="row-figures">
                                    <span class="coverage">{compactCoverageLabel(c.delivery)}</span>
                                    <span class="dur">{compactHoursLabel(c.delivery)}</span>
                                </div>
                            </div>
                        {/each}
                    {/each}
                {/if}
            </div>
        </div>

        <footer class="foot">
            <PickerFooter on:cancel={onClose} />
        </footer>
    </div>
</Modal>

<style>
    .picker {
        display: flex;
        flex-direction: column;
        height: 100%;
        min-height: 0;
        outline: none;
    }
    .body {
        display: grid;
        grid-template-columns: minmax(160px, max-content) 1fr;
        flex: 1;
        min-height: 0;
    }
    .list {
        overflow-y: auto;
        padding: var(--s-2) 0 var(--s-4);
    }
    .state {
        padding: var(--s-12) var(--s-6);
        text-align: center;
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }
    .state.error { color: var(--state-error-fg); }

    .picker-controls-search {
        width: 360px;
        flex-shrink: 0;
    }
    .picker-controls-tabs {
        flex: 1;
        min-width: 0;
    }
    .picker-controls-tabs :global(.tabs) {
        padding: 0;
        border-bottom: none;
    }

    .foot {
        padding: var(--s-3) var(--s-6);
        border-top: 1px solid var(--border-quiet);
        flex-shrink: 0;
    }
</style>
