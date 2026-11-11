<script lang="ts">
    /**
     * Admin Releases compartment — the single operator lifecycle home.
     *
     * Three zones (mirrors the Reviews compartment for visual + interaction
     * parity):
     *   1. ``ReleasesSummaryCard`` — current GH release + "what's next" cut
     *      preview + Cut button + in-flight alert strip.
     *   2. Sticky filter bar — search + facet chips + sort + clear.
     *   3. Seven action-needed sections (priority order): In progress /
     *      Failed to publish / Ready to generate / Behind edits /
     *      Republish to HF / Publish to HF / Published & current. A row
     *      belongs to exactly one bucket.
     *
     * Each row carries a single in-row expansion (button-switched: TS settings,
     * Timeline, Reviewers, Past jobs). Only ONE expansion is open across the
     * whole list at a time (``expanded`` is compartment-level state).
     *
     * Single bulk fetch via ``/api/admin/releases/status`` + FE-side bucketing.
     * 30 s poll picks up new in-flight state; launches invalidate the server
     * cache so the next poll reflects new state immediately.
     */
    import { untrack } from 'svelte';
    import {
        cancelReleaseJob,
        fetchReleasePreview,
        fetchReleasesStatus,
        publishHfBatch,
        refreshHfCatalog,
        type InFlightJob,
        type ReleaseSettings,
        type ReleasePreviewResponse,
        type ReleaseStatusRow,
        type ReleasesStatusResponse,
    } from '../../../../../lib/api/admin-releases';
    import { sendBackToUnderReview } from '../../../../../lib/api/admin-reviews';
    import { can } from '../../../../../lib/stores/capabilities';
    import { releasesStore } from '../../../../../lib/stores/releases.svelte';
    import AutomationSection from './AutomationSection.svelte';
    import CutReleaseModal from './CutReleaseModal.svelte';
    import ReleasesActionBar from './ReleasesActionBar.svelte';
    import ReleasesRow, { type ReleasesBucket } from './ReleasesRow.svelte';
    import type { RowExpandMode } from './ReleasesRowExpansion.svelte';
    import ReleasesSummaryCard from './ReleasesSummaryCard.svelte';

    let resp = $state<ReleasesStatusResponse | null>(null);
    let loading = $state(true);
    let error = $state<string | null>(null);
    let preview = $state<ReleasePreviewResponse | null>(null);

    let cutModalOpen = $state(false);
    // Shared release settings (clip-edge pads) — one source of truth for BOTH
    // the HF publish (action bar) and the GH cut (modal), mirrors server defaults.
    let releaseSettings = $state<ReleaseSettings>({ pad_start: 100, pad_end: 300, min_gap: 100 });
    let rowError = $state<{ slug: string; message: string } | null>(null);
    let sendBackBusySlug = $state<string | null>(null);

    // Single in-row expansion across the whole list + the job id to poll live
    // in a row's Past-jobs view after a launch.
    let expanded = $state<{ slug: string; mode: RowExpandMode } | null>(null);
    let activeJobBySlug = $state<Record<string, string>>({});

    function toggleMode(slug: string, mode: RowExpandMode): void {
        if (expanded && expanded.slug === slug && expanded.mode === mode) {
            expanded = null;
        } else {
            expanded = { slug, mode };
        }
    }
    /** After a gen/regen launch: pin the job for live polling + switch the
     *  row's expansion to Past jobs, then refetch so it re-buckets. */
    function onLaunched(slug: string, jobId: string): void {
        activeJobBySlug = { ...activeJobBySlug, [slug]: jobId };
        expanded = { slug, mode: 'jobs' };
        refetch();
    }

    async function onSendBack(slug: string): Promise<void> {
        if (sendBackBusySlug !== null) return;
        const reason = window.prompt('Reason for sending back to Under review (≥10 chars):');
        if (reason === null) return;
        if (reason.trim().length < 10) {
            rowError = { slug, message: 'Reason must be at least 10 characters' };
            return;
        }
        sendBackBusySlug = slug;
        rowError = null;
        try {
            await sendBackToUnderReview(slug, reason.trim());
            if (expanded?.slug === slug) expanded = null;
            refetch();
        } catch (e) {
            rowError = { slug, message: (e as Error).message ?? 'Send back failed' };
        } finally {
            sendBackBusySlug = null;
        }
    }

    // Batch-publish selection + action state.
    let selected = $state<Set<string>>(new Set());
    let batchBusy = $state(false);
    let batchError = $state<string | null>(null);
    let cancelingJob = $state<string | null>(null);
    let refreshBusy = $state(false);
    // Dismissed batch-summary banners, keyed by job id (localStorage-backed).
    let dismissedBatches = $state<Set<string>>(loadDismissedBatches());

    const BATCH_DISMISS_LS_KEY = 'insp_releases_batch_dismissed';
    function loadDismissedBatches(): Set<string> {
        try {
            const raw = localStorage.getItem(BATCH_DISMISS_LS_KEY);
            return raw ? new Set(JSON.parse(raw) as string[]) : new Set();
        } catch {
            return new Set();
        }
    }

    // Trigger refetch from internal mutations (publish action) without
    // touching the store's refreshSeq.
    let refetchSeq = $state(0);
    let listAreaEl: HTMLElement | null = $state(null);

    /** Fetch on mount and every 30 s while the compartment is alive.
     *  AbortController cancels stale fetches on tab switch / unmount. */
    $effect(() => {
        refetchSeq;            // tracked dep (manual bump)
        releasesStore.refreshSeq;   // tracked dep (external — e.g. cut modal)
        const ac = new AbortController();
        // untrack: reading resp here must NOT make it a dep, else setting
        // resp in .then re-triggers this effect → infinite fetch loop.
        loading = untrack(() => resp) === null;   // spinner only on cold load
        error = null;
        fetchReleasesStatus(ac.signal)
            .then((r) => {
                if (ac.signal.aborted) return;
                resp = r;
                loading = false;
            })
            .catch((e: unknown) => {
                if (ac.signal.aborted) return;
                error = (e as Error).message ?? 'Failed to load releases';
                loading = false;
            });
        return () => ac.abort();
    });

    $effect(() => {
        const id = window.setInterval(() => { refetchSeq += 1; }, 30_000);
        return () => window.clearInterval(id);
    });

    // Header "what's next" preview — same cadence as the status fetch (no
    // faster than 30 s). Best-effort: a failure just hides the preview cluster,
    // never the current-release line. ``release.cut_gh``-gated server-side; a
    // 403 for a maintainer leaves preview null (the cluster simply omits).
    $effect(() => {
        refetchSeq;
        releasesStore.refreshSeq;
        const ac = new AbortController();
        fetchReleasePreview(ac.signal)
            .then((p) => { if (!ac.signal.aborted) preview = p; })
            .catch(() => { if (!ac.signal.aborted) preview = null; });
        return () => ac.abort();
    });

    function refetch(): void { refetchSeq += 1; }

    const allRows = $derived(resp?.recitations ?? []);
    const inFlight = $derived(resp?.in_flight ?? []);
    const summary = $derived(resp?.summary ?? null);

    // ---- bucketing predicates ----
    const inFlightSlugs = $derived(new Set(
        inFlight.map((j) => j.slug).filter((s): s is string => !!s),
    ));
    function jobForSlug(slug: string): InFlightJob | null {
        return inFlight.find((j) => j.slug === slug) ?? null;
    }

    // Bucketing — priority-first; a row lands in exactly one. Mirrors
    // ``_is_bucketable`` server-side. "Publish to HF" (first publish) and
    // "Republish to HF" (stale) are deliberately distinct sections.
    function bucketOf(r: ReleaseStatusRow): ReleasesBucket | null {
        if (inFlightSlugs.has(r.slug)) return 'in_flight';
        if (r.publish_error) return 'failed';
        if (r.marked_ready && r.ts === null) return 'ready_to_generate';
        if (r.ts?.stale_since) return 'behind_edits';
        if (r.hf?.stale_since) return 'republish_hf';
        if (r.state === 'released' && r.hf === null && r.ts !== null) return 'publish_hf';
        if (r.hf !== null && !r.hf.stale_since) return 'published_current';
        return null;   // fresh / inert — hide
    }

    // Buckets whose rows can be selected for a batch (re)publish.
    const SELECTABLE_BUCKETS: ReadonlySet<ReleasesBucket> = new Set<ReleasesBucket>([
        'publish_hf', 'republish_hf', 'failed', 'published_current',
    ]);
    function isSelectable(r: ReleaseStatusRow): boolean {
        const b = bucketOf(r);
        return b !== null && SELECTABLE_BUCKETS.has(b);
    }

    // ---- filter + sort ----
    function matchFilter(r: ReleaseStatusRow): boolean {
        const f = releasesStore.filters;
        const q = f.q.trim().toLowerCase();
        if (q) {
            const ar = (r.name_ar ?? '').toLowerCase();
            const en = (r.name_en ?? '').toLowerCase();
            if (!ar.includes(q) && !en.includes(q)) return false;
        }
        if (f.riwayah && r.riwayah !== f.riwayah) return false;
        if (f.style && r.style !== f.style) return false;
        if (f.channel && r.channel !== f.channel) return false;
        return true;
    }

    function compareRows(a: ReleaseStatusRow, b: ReleaseStatusRow): number {
        if (releasesStore.sortBy === 'name') {
            return (a.name_en ?? a.slug).localeCompare(b.name_en ?? b.slug);
        }
        // "stalest first" — HF stale_since ASC, then HF produced_at ASC,
        // then slug. Rows without HF sort by ts.produced_at, surfacing the
        // longest-waiting-to-publish first.
        const aKey = a.hf?.stale_since ?? a.hf?.produced_at ?? a.ts?.produced_at ?? '';
        const bKey = b.hf?.stale_since ?? b.hf?.produced_at ?? b.ts?.produced_at ?? '';
        if (aKey !== bKey) return aKey.localeCompare(bKey);
        return a.slug.localeCompare(b.slug);
    }

    const filteredRows = $derived(allRows.filter(matchFilter).sort(compareRows));
    const narrowedToZero = $derived(
        allRows.length > 0
            && filteredRows.length === 0
            && releasesStore.hasActiveFilters,
    );

    // ---- facet value lists (with counts from the full unfiltered set) ----
    function countBy(rows: ReleaseStatusRow[], key: 'riwayah' | 'style' | 'channel'): Array<[string, number]> {
        const m = new Map<string, number>();
        for (const r of rows) {
            const v = r[key];
            if (!v) continue;
            m.set(v, (m.get(v) ?? 0) + 1);
        }
        return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    }
    const riwayahValues = $derived(countBy(allRows, 'riwayah'));
    const styleValues = $derived(countBy(allRows, 'style'));
    const channelValues = $derived(countBy(allRows, 'channel'));

    // ---- bucket-grouped rows ----
    type Section = {
        key: ReleasesBucket;
        label: string;
        mark: string;
        defaultCollapsed: boolean;
        hideWhenEmpty: boolean;
    };
    const SECTIONS: Section[] = [
        { key: 'in_flight',         label: 'In progress',         mark: 'inflight',  defaultCollapsed: false, hideWhenEmpty: true },
        { key: 'failed',            label: 'Failed to publish',   mark: 'failed',    defaultCollapsed: false, hideWhenEmpty: true },
        { key: 'ready_to_generate', label: 'Ready to generate',   mark: 'ready',     defaultCollapsed: false, hideWhenEmpty: true },
        { key: 'behind_edits',      label: 'Behind edits',        mark: 'edited',    defaultCollapsed: false, hideWhenEmpty: true },
        { key: 'republish_hf',      label: 'Republish to HF',     mark: 'stale',     defaultCollapsed: false, hideWhenEmpty: false },
        { key: 'publish_hf',        label: 'Publish to HF',       mark: 'waiting',   defaultCollapsed: false, hideWhenEmpty: false },
        { key: 'published_current', label: 'Published & current', mark: 'published', defaultCollapsed: true,  hideWhenEmpty: false },
    ];

    const bucketed = $derived.by(() => {
        const out: Record<ReleasesBucket, ReleaseStatusRow[]> = {
            in_flight: [], failed: [], ready_to_generate: [], behind_edits: [],
            republish_hf: [], publish_hf: [], published_current: [],
        };
        for (const r of filteredRows) {
            const b = bucketOf(r);
            if (b !== null) out[b].push(r);
        }
        return out;
    });

    /** Number of rows that COULD land in the next GH cut — released + has
     *  TS + (new OR refreshable). Surfaced in the summary card's "No releases
     *  yet · N ready" state and as Cut-button context. */
    const readyCount = $derived(
        allRows.filter((r) =>
            r.state === 'released'
            && r.ts !== null
            && (r.gh === null || !!r.hf?.stale_since || (r.ts && summary?.produced_at && r.ts.produced_at > summary.produced_at)),
        ).length,
    );

    /** Disabled reason for the Cut button — null when enabled. */
    const cutDisabledReason = $derived.by(() => {
        if (inFlight.some((j) => j.kind === 'cut_release')) {
            return 'A cut is already in flight';
        }
        // Nothing-changed: there IS a current release AND no row has TS
        // newer than the cut's produced_at AND no member is stale.
        if (summary && summary.produced_at) {
            const cutAt = summary.produced_at;
            const anyFresher = allRows.some((r) =>
                r.ts !== null
                && r.ts.produced_at > cutAt,
            );
            const anyStaleMember = allRows.some(
                (r) => !!r.gh?.stale_since,
            );
            const anyAdded = allRows.some(
                (r) =>
                    r.state === 'released'
                    && r.ts !== null
                    && r.gh === null,
            );
            if (!anyFresher && !anyStaleMember && !anyAdded) {
                return 'Nothing changed since last cut';
            }
        }
        return null;
    });

    // ---- collapse persistence ----
    const COLLAPSE_LS_KEY = 'insp_releases_collapsed';
    function loadCollapsed(): Record<ReleasesBucket, boolean> {
        const fallback: Record<ReleasesBucket, boolean> = {
            in_flight: false, failed: false, ready_to_generate: false,
            behind_edits: false, republish_hf: false, publish_hf: false,
            published_current: true,
        };
        try {
            const raw = localStorage.getItem(COLLAPSE_LS_KEY);
            if (!raw) return fallback;
            const parsed = JSON.parse(raw) as Partial<Record<ReleasesBucket, boolean>>;
            return {
                in_flight: parsed.in_flight ?? fallback.in_flight,
                failed: parsed.failed ?? fallback.failed,
                ready_to_generate: parsed.ready_to_generate ?? fallback.ready_to_generate,
                behind_edits: parsed.behind_edits ?? fallback.behind_edits,
                republish_hf: parsed.republish_hf ?? fallback.republish_hf,
                publish_hf: parsed.publish_hf ?? fallback.publish_hf,
                published_current: parsed.published_current ?? fallback.published_current,
            };
        } catch {
            return fallback;
        }
    }
    let collapsed = $state<Record<ReleasesBucket, boolean>>(loadCollapsed());

    function toggle(key: ReleasesBucket): void {
        collapsed[key] = !collapsed[key];
        try { localStorage.setItem(COLLAPSE_LS_KEY, JSON.stringify(collapsed)); }
        catch { /* ignore */ }
    }

    // ---- selection ----
    function toggleSelect(slug: string): void {
        const next = new Set(selected);
        if (next.has(slug)) next.delete(slug);
        else next.add(slug);
        selected = next;
    }
    function clearSelection(): void {
        selected = new Set();
    }
    /** Select every publishable row currently visible (filtered). */
    function selectAll(): void {
        const next = new Set<string>();
        for (const r of filteredRows) {
            if (isSelectable(r)) next.add(r.slug);
        }
        selected = next;
    }
    /** Toggle every row in one section — select all if any unselected, else clear. */
    function toggleSectionSelect(rows: ReleaseStatusRow[]): void {
        const next = new Set(selected);
        const allSel = rows.length > 0 && rows.every((r) => next.has(r.slug));
        for (const r of rows) {
            if (allSel) next.delete(r.slug);
            else next.add(r.slug);
        }
        selected = next;
    }

    /** Count of selectable rows in the current filtered view. */
    const selectableCount = $derived(filteredRows.filter(isSelectable).length);

    // ---- actions ----
    async function onPublishBatch(): Promise<void> {
        if (batchBusy || selected.size === 0) return;
        batchBusy = true;
        batchError = null;
        try {
            await publishHfBatch([...selected], releaseSettings);
            clearSelection();
            refetch();   // the server cache is already busted; this gets the new in_flight
        } catch (e) {
            batchError = (e as Error).message ?? 'Batch publish failed';
        } finally {
            batchBusy = false;
        }
    }

    /** Launch the global mushafs-catalog refresh — the cheap remediation for
     *  catalog-metadata drift. The launch busts the server in-flight cache, so
     *  the refetch surfaces the refresh job in the strip; on completion the
     *  catalog_edit HF staleness clears and the drift count drops to 0. */
    async function onRefreshCatalog(): Promise<void> {
        if (refreshBusy) return;
        refreshBusy = true;
        batchError = null;
        try {
            await refreshHfCatalog();
            refetch();
        } catch (e) {
            batchError = (e as Error).message ?? 'Catalog refresh failed';
        } finally {
            refreshBusy = false;
        }
    }

    async function onCancelJob(jobId: string): Promise<void> {
        if (cancelingJob !== null) return;
        if (!window.confirm('Cancel this job? Any in-progress work will be lost.')) return;
        cancelingJob = jobId;
        try {
            await cancelReleaseJob(jobId);
            refetch();
        } catch (e) {
            batchError = (e as Error).message ?? 'Cancel failed';
        } finally {
            cancelingJob = null;
        }
    }

    // ---- batch summary banner ----
    const lastBatch = $derived(resp?.last_batch ?? null);
    const showBanner = $derived(
        lastBatch !== null && !dismissedBatches.has(lastBatch.job_id),
    );
    function dismissBanner(): void {
        if (!lastBatch) return;
        const next = new Set(dismissedBatches);
        next.add(lastBatch.job_id);
        dismissedBatches = next;
        try { localStorage.setItem(BATCH_DISMISS_LS_KEY, JSON.stringify([...next])); }
        catch { /* ignore */ }
    }

    function onCutComplete(): void {
        cutModalOpen = false;
        refetch();
    }

    function jumpToSection(key: ReleasesBucket): void {
        // Expand the section first (it's only mounted when non-empty, but
        // could be collapsed by the operator) and then scroll into view.
        collapsed[key] = false;
        try { localStorage.setItem(COLLAPSE_LS_KEY, JSON.stringify(collapsed)); }
        catch { /* ignore */ }
        // Defer one tick so the {#if} mounts before the scroll. Locate the
        // section by data attribute — Svelte 5 doesn't allow a conditional
        // bind:this inside an each-loop.
        queueMicrotask(() => {
            const el = listAreaEl?.querySelector<HTMLElement>(`[data-section="${key}"]`);
            el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    }
    const scrollToInFlight = () => jumpToSection('in_flight');
    const canManageAutomation = can('release.manage_automation');
</script>

<div class="releases">
    {#if loading}
        <div class="state">Loading…</div>
    {:else if error}
        <div class="state error" role="alert">{error}</div>
    {:else}
        <ReleasesSummaryCard
            summary={summary}
            preview={preview}
            inFlight={inFlight}
            readyCount={readyCount}
            onCut={() => (cutModalOpen = true)}
            cutDisabledReason={cutDisabledReason}
            onJumpToInFlight={scrollToInFlight}
            cancelingJob={cancelingJob}
            onCancelJob={onCancelJob}
            catalogDriftCount={resp?.catalog_drift_count ?? 0}
            onRefreshCatalog={onRefreshCatalog}
        />

        {#if showBanner && lastBatch}
            <div class="batch-banner" class:has-failures={lastBatch.failed_count > 0} role="status">
                <span class="banner-text">
                    Batch publish finished —
                    <strong>{lastBatch.published_count}</strong> published{#if lastBatch.failed_count > 0},
                        <strong class="fail">{lastBatch.failed_count}</strong> failed{/if}.
                </span>
                {#if lastBatch.failed_count > 0}
                    <button class="banner-link" type="button" onclick={() => jumpToSection('failed')}>
                        View failures
                    </button>
                {/if}
                <button class="banner-dismiss" type="button" onclick={dismissBanner} aria-label="Dismiss">×</button>
            </div>
        {/if}

        {#if allRows.length === 0}
            <div class="list-area" bind:this={listAreaEl}>
                {#if $canManageAutomation}<AutomationSection />{/if}
                <div class="zero-state">
                    <h3>No reciters in the release pipeline yet</h3>
                    <p>
                        Reciters land here once they finish review and timestamp
                        generation. Open the <strong>Reviews</strong> tab to
                        claim a reciter, mark it ready, and run
                        <em>Generate timestamps</em> — successful runs auto-release
                        the reciter and surface them here as ready to publish.
                    </p>
                </div>
            </div>
        {:else}
        <div class="list-area" bind:this={listAreaEl}>
            {#if $canManageAutomation}<AutomationSection />{/if}
            <!-- Filter bar -->
        <div class="filter-bar">
            <span class="search">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
                    <circle cx="7" cy="7" r="5" /><line x1="11" y1="11" x2="14.5" y2="14.5" />
                </svg>
                <input
                    type="text"
                    placeholder="Search reciter (Arabic or Latin)"
                    value={releasesStore.filters.q}
                    oninput={(e) => releasesStore.setQ((e.currentTarget as HTMLInputElement).value)}
                />
            </span>

            <div class="facet-group">
                <span class="facet-label">Riwayah</span>
                {#each riwayahValues as [val, count] (val)}
                    <button class="chip" class:active={releasesStore.filters.riwayah === val}
                        type="button" onclick={() => releasesStore.toggleFacet('riwayah', val)}>
                        {val} <span class="c-count">{count}</span>
                    </button>
                {/each}
            </div>

            <div class="facet-group">
                <span class="facet-label">Style</span>
                {#each styleValues as [val, count] (val)}
                    <button class="chip" class:active={releasesStore.filters.style === val}
                        type="button" onclick={() => releasesStore.toggleFacet('style', val)}>
                        {val} <span class="c-count">{count}</span>
                    </button>
                {/each}
            </div>

            <div class="facet-group">
                <span class="facet-label">Channel</span>
                {#each channelValues as [val, count] (val)}
                    <button class="chip" class:active={releasesStore.filters.channel === val}
                        type="button" onclick={() => releasesStore.toggleFacet('channel', val)}>
                        {val} <span class="c-count">{count}</span>
                    </button>
                {/each}
            </div>

            <div class="filter-trail">
                <label class="sort">
                    <span>sort</span>
                    <select
                        value={releasesStore.sortBy}
                        onchange={(e) => releasesStore.setSort(
                            (e.currentTarget as HTMLSelectElement).value as 'stalest' | 'name',
                        )}
                    >
                        <option value="stalest">stalest first</option>
                        <option value="name">name</option>
                    </select>
                </label>
                {#if releasesStore.hasActiveFilters}
                    <button class="clear-btn" type="button" onclick={() => releasesStore.clearFilters()}>
                        Clear
                    </button>
                {/if}
            </div>
        </div>

            {#if narrowedToZero}
                <div class="narrowed">
                    <span>No recitations match the active filter.</span>
                    <button class="clear-link" type="button" onclick={() => releasesStore.clearFilters()}>
                        Clear filters
                    </button>
                </div>
            {/if}

            {#each SECTIONS as section (section.key)}
                {@const rows = bucketed[section.key]}
                {@const sectionSelectable = SELECTABLE_BUCKETS.has(section.key)}
                {@const allSel = rows.length > 0 && rows.every((r) => selected.has(r.slug))}
                {#if !(section.hideWhenEmpty && rows.length === 0)}
                    <section
                        class="state-section"
                        class:collapsed={collapsed[section.key]}
                        data-section={section.key}
                    >
                        <div class="state-head-row">
                            {#if sectionSelectable && rows.length > 0}
                                <label class="head-select" title="Select all in this section">
                                    <input
                                        type="checkbox"
                                        checked={allSel}
                                        onchange={() => toggleSectionSelect(rows)}
                                    />
                                </label>
                            {:else}
                                <span class="head-select-spacer" aria-hidden="true"></span>
                            {/if}
                            <button
                                class="state-head"
                                type="button"
                                aria-expanded={!collapsed[section.key]}
                                onclick={() => toggle(section.key)}
                            >
                                <span class="state-mark mark-{section.mark}"></span>
                                <span class="state-name">{section.label}</span>
                                <span class="state-count">{rows.length}</span>
                                <span class="state-toggle" aria-hidden="true">▾</span>
                            </button>
                        </div>
                        {#if !collapsed[section.key]}
                            <div class="state-body">
                                {#if rows.length === 0}
                                    <div class="empty-line">No items.</div>
                                {:else}
                                    <div class="row-list">
                                        {#each rows as row (row.slug)}
                                            <ReleasesRow
                                                row={row}
                                                bucket={section.key}
                                                inFlightJob={section.key === 'in_flight' ? jobForSlug(row.slug) : null}
                                                selectable={sectionSelectable}
                                                selected={selected.has(row.slug)}
                                                expandedMode={expanded?.slug === row.slug ? expanded.mode : null}
                                                activeJobId={activeJobBySlug[row.slug] ?? null}
                                                canceling={cancelingJob === jobForSlug(row.slug)?.job_id}
                                                sendBackBusy={sendBackBusySlug === row.slug}
                                                errorMessage={rowError?.slug === row.slug ? rowError.message : null}
                                                onToggleSelect={toggleSelect}
                                                onToggleMode={toggleMode}
                                                onLaunched={onLaunched}
                                                onCancel={onCancelJob}
                                                onSendBack={onSendBack}
                                            />
                                        {/each}
                                    </div>
                                {/if}
                            </div>
                        {/if}
                    </section>
                {/if}
            {/each}
        </div>

        <ReleasesActionBar
            count={selected.size}
            selectableCount={selectableCount}
            busy={batchBusy}
            error={batchError}
            bind:settings={releaseSettings}
            onPublish={onPublishBatch}
            onSelectAll={selectAll}
            onClear={clearSelection}
        />
        {/if}
    {/if}

    {#if cutModalOpen}
        <CutReleaseModal
            bind:settings={releaseSettings}
            onclose={() => (cutModalOpen = false)}
            onsuccess={onCutComplete}
        />
    {/if}
</div>

<style>
    .releases {
        position: relative;
        display: flex;
        flex-direction: column;
        padding: var(--s-3) var(--s-5) 0;
        gap: var(--s-3);
        height: 100%;
        overflow: hidden;
    }
    .list-area {
        flex: 1;
        min-height: 0;
        overflow-y: auto;
        padding-bottom: var(--s-5);
        display: flex;
        flex-direction: column;
        gap: var(--s-3);
    }

    .state {
        padding: var(--s-12) 0;
        text-align: center;
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }
    .state.error { color: var(--state-error-fg); }

    .state-head-row {
        display: flex;
        align-items: stretch;
        gap: var(--s-2);
        border-bottom: 1px solid var(--border-quiet);
    }
    .head-select,
    .head-select-spacer {
        width: 16px;
        flex: 0 0 16px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding-left: var(--s-3);
    }
    .head-select { cursor: pointer; }
    .head-select input { accent-color: var(--accent); cursor: pointer; margin: 0; }
    .state-head {
        display: flex;
        align-items: center;
        gap: var(--s-3);
        padding: var(--s-2) var(--s-1);
        background: transparent;
        border: 0;
        flex: 1;
        min-width: 0;
        cursor: pointer;
        font: inherit;
        color: var(--text-primary);
        text-align: left;
        user-select: none;
    }
    .state-head:hover { background: var(--panel); }

    /* Batch-publish summary banner — sits under the summary card. */
    .batch-banner {
        display: flex;
        align-items: center;
        gap: var(--s-3);
        padding: var(--s-2) var(--s-3);
        background: var(--accent-tint-soft);
        border: 1px solid var(--accent-tint);
        border-radius: var(--r-1);
        font-size: var(--fs-meta);
        color: var(--text-secondary);
    }
    .batch-banner.has-failures {
        background: var(--state-error-bg);
        border-color: oklch(0.86 0.130 75 / 0.4);
    }
    .banner-text strong { color: var(--text-primary); font-variant-numeric: tabular-nums; }
    .banner-text strong.fail { color: var(--state-error-fg); }
    .banner-link {
        background: transparent;
        border: 0;
        color: var(--accent);
        font: inherit;
        font-size: var(--fs-meta);
        cursor: pointer;
        padding: 0;
    }
    .banner-link:hover { color: var(--accent-strong); text-decoration: underline; }
    .banner-dismiss {
        margin-left: auto;
        background: transparent;
        border: 0;
        color: var(--text-faint);
        font-size: 16px;
        line-height: 1;
        cursor: pointer;
        padding: 0 var(--s-1);
    }
    .banner-dismiss:hover { color: var(--text-primary); }

    .state-mark {
        width: 8px;
        height: 8px;
        border-radius: 1px;
        flex: 0 0 auto;
    }
    /* Section mark colours — reuse Reviews palette to keep the visual family
     * coherent across compartments. */
    .mark-inflight  { background: var(--state-under-review-fg); }
    .mark-ready     { background: var(--state-under-review-fg); }
    .mark-stale     { background: var(--state-error-fg); }
    .mark-edited    { background: var(--state-error-fg); }
    .mark-failed    { background: var(--state-error-fg); }
    .mark-waiting   { background: var(--state-available-fg); }
    .mark-published { background: var(--state-published-fg); }

    .state-name {
        font-size: var(--fs-row);
        font-weight: 500;
        color: var(--text-primary);
    }
    .state-count {
        color: var(--text-faint);
        font-family: var(--font-mono);
        font-size: var(--fs-meta);
        font-variant-numeric: tabular-nums;
    }
    .state-toggle {
        margin-left: auto;
        color: var(--text-faint);
        font-family: var(--font-mono);
        font-size: 11px;
        transition: transform var(--t-fast) var(--ease-out-quart);
    }
    .state-section.collapsed .state-toggle { transform: rotate(-90deg); }

    .state-body { padding: 0; }
    .empty-line {
        font-size: var(--fs-body);
        color: var(--text-faint);
        padding: var(--s-3) var(--s-1);
    }
    .row-list { display: flex; flex-direction: column; }

    /* filter bar — same primitives as ReviewsCompartment. */
    .filter-bar {
        display: flex;
        align-items: center;
        gap: var(--s-3);
        flex-wrap: wrap;
        padding: var(--s-1) var(--s-1) var(--s-3);
        border-bottom: 1px solid var(--border-quiet);
        margin-bottom: var(--s-2);
    }
    .search {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        height: 28px;
        padding: 0 var(--s-3);
        min-width: 220px;
        color: var(--text-muted);
        flex: 0 0 auto;
    }
    .search svg { width: 14px; height: 14px; flex-shrink: 0; }
    .search input {
        flex: 1;
        background: transparent;
        border: 0;
        color: var(--text-primary);
        font: inherit;
        font-size: var(--fs-meta);
        outline: 0;
        min-width: 120px;
    }
    .search input::placeholder { color: var(--text-faint); }
    .search:focus-within {
        border-color: var(--accent-tint);
        box-shadow: 0 0 0 2px var(--accent-tint-soft);
    }

    .facet-group {
        display: flex;
        align-items: center;
        gap: 6px;
        max-width: 100%;
        flex-wrap: wrap;
    }
    .facet-label {
        font-size: 10.5px;
        color: var(--text-faint);
        font-family: var(--font-mono);
        letter-spacing: 0.04em;
        text-transform: uppercase;
        margin-right: 2px;
    }
    .chip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        height: 24px;
        padding: 0 8px;
        background: transparent;
        border: 1px solid var(--border-quiet);
        border-radius: 999px;
        color: var(--text-secondary);
        font: inherit;
        font-size: 11px;
        cursor: pointer;
        white-space: nowrap;
        transition: color var(--t-fast), border-color var(--t-fast), background-color var(--t-fast);
    }
    .chip:hover { border-color: var(--border-default); color: var(--text-primary); }
    .chip.active {
        color: var(--text-primary);
        border-color: var(--accent-tint);
        background: var(--accent-tint-soft);
    }
    .c-count {
        font-family: var(--font-mono);
        color: var(--text-faint);
        font-size: 10px;
        font-variant-numeric: tabular-nums;
    }
    .chip.active .c-count { color: var(--accent-strong); }

    .filter-trail {
        display: inline-flex;
        align-items: center;
        gap: var(--s-3);
        margin-left: auto;
    }
    .sort {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 10.5px;
        color: var(--text-faint);
        font-family: var(--font-mono);
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .sort select {
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        color: var(--text-secondary);
        font: inherit;
        font-size: 11px;
        padding: 3px 6px;
        border-radius: var(--r-1);
        cursor: pointer;
    }
    .sort select:focus { outline: 0; border-color: var(--accent-tint); }

    .clear-btn {
        background: transparent;
        border: 1px solid var(--border-quiet);
        color: var(--text-muted);
        font: inherit;
        font-size: 11px;
        padding: 3px 10px;
        border-radius: var(--r-1);
        cursor: pointer;
        transition: color var(--t-fast), border-color var(--t-fast);
    }
    .clear-btn:hover { color: var(--text-primary); border-color: var(--border-default); }

    .narrowed {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--s-3);
        padding: var(--s-3) var(--s-1);
        color: var(--text-muted);
        font-size: var(--fs-meta);
        border-bottom: 1px solid var(--border-quiet);
        margin-bottom: var(--s-2);
    }
    .clear-link {
        background: transparent;
        border: 0;
        color: var(--accent);
        font: inherit;
        font-size: var(--fs-meta);
        cursor: pointer;
        padding: 0;
    }
    .clear-link:hover { color: var(--accent-strong); text-decoration: underline; }

    .zero-state {
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
        padding: var(--s-6) var(--s-4);
        margin-top: var(--s-3);
        background: var(--panel-2);
        border: 1px dashed var(--border-quiet);
        border-radius: var(--r-1);
        color: var(--text-secondary);
    }
    .zero-state h3 {
        margin: 0;
        font-size: var(--fs-body);
        font-weight: 500;
        color: var(--text-primary);
    }
    .zero-state p {
        margin: 0;
        font-size: var(--fs-meta);
        line-height: 1.5;
        max-width: 64ch;
    }
    .zero-state strong { color: var(--text-primary); font-weight: 500; }
    .zero-state em { font-style: normal; color: var(--accent); }
</style>
