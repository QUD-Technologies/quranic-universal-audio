<script lang="ts">
    /**
     * SegmentsTab — top-level Svelte component for the Segments tab.
     *
     * Owns reciter/chapter/verse dropdowns, filter bar, navigation banner,
     * segment list rendering, CSS-var config, and tab-level keyboard shortcuts.
     * Mounts validation, history, and save-preview panels as Svelte children.
     */

    import { onDestroy, onMount, tick } from 'svelte';
    import { get, type Readable } from 'svelte/store';

    import { fetchJson } from '../../lib/api';
    import { release } from '../../lib/api/claims-client';
    import { getReciterTaskStore, type ReciterTask,refreshReciterTask } from '../../lib/api/reciter-task';
    import { localeStore, tr } from '../../lib/i18n/locale-store';
    import * as m from '../../lib/paraglide/messages';
    import { clearQuranRefs, loadQuranRefs } from '../../lib/refs/quran-refs';
    import { currentUser, loadCurrentUser } from '../../lib/stores/current-user';
    import { setEditingMode, syncEditingMode } from '../../lib/stores/editing-mode';
    import { openGuidesGate } from '../../lib/stores/guides-gate';
    import type { SegReciter } from '../../lib/types/generated/schemas';
    import { LS_KEYS } from '../../lib/utils/constants';
    import { pendingSegmentsDeepLink, type SegmentsDeepLink } from '../../lib/utils/goto-segments';
    import { surahInfoReady } from '../../lib/utils/surah-info';
    import { editionFontStack, ensureEditionFont } from '../../lib/refs/edition-font';
    import { isSupportedRiwayah } from '../../lib/riwayat';
    import { catalogData, deliveryRiwayah, loadCatalog, startCatalogPolling } from '../dashboard/stores/catalog-data';
    import EditOverlay from './components/edit/EditOverlay.svelte';
    import FiltersBar from './components/filters/FiltersBar.svelte';
    import SegmentsFooter from './components/footer/SegmentsFooter.svelte';
    import HistoryPanel from './components/history/HistoryPanel.svelte';
    import MarkReadyReviewModal from './components/footer/MarkReadyReviewModal.svelte';
    import SegmentsList from './components/list/SegmentsList.svelte';
    import SamplesPanel from './components/samples/SamplesPanel.svelte';
    import SavePreview from './components/save/SavePreview.svelte';
    import AccordionGuideModal from './components/validation/AccordionGuideModal.svelte';
    import GuidesGateModal from './components/validation/GuidesGateModal.svelte';
    import ValidationPanel from './components/validation/ValidationPanel.svelte';
    import { allGuidesRead } from './guides/registry';
    import { clearAccordionPin } from './stores/accordion-pin';
    import { autoSaveEnabled } from './stores/autosave';
    import {
        getChapterSegments,
        pickerDisplayChapter,
        type SegAllState,
        segAllData,
        segAllReciters,
        selectedChapter,
        selectedReciter,
        selectedVerse,
    } from './stores/chapter';
    import { dirtyTick,isDirtyStore } from './stores/dirty';
    import { activeFilters } from './stores/filters';
    import { closeGuideModal, guideModal } from './stores/guides';
    import { historyVisible } from './stores/history';
    import { savedFilterView, targetSegmentIndex } from './stores/navigation';
    import { segAudioElement, segListElement, waveformContainer } from './stores/playback';
    import {
        canManageSamples,
        isSampleMode,
        isSampleSlug,
        sampleEditingMode,
        samples,
        segmentsSubTab,
        type SegmentsSubTab,
        visibleSubTabs,
    } from './stores/samples';
    import { savePreviewVisible } from './stores/save';
    import { accordionViewActive, valUiOpenCategory } from './stores/validation';
    import { loadChapterData } from './utils/data/chapter-actions';
    import { clearPerReciterState } from './utils/data/clear-per-reciter-state';
    import { clearSegConfig, loadSegConfig } from './utils/data/config-loader';
    import { reloadCurrentReciter } from './utils/data/reciter-actions';
    import { handleSegmentsKey } from './utils/keyboard';
    import { playFromSegment } from './utils/playback/playback';
    import { executeSave } from './utils/save/actions';

    // Audio element ref published by SegmentsFooter's onMount into the
    // `segAudioElement` store. EditOverlay still wants the raw element as
    // a marker prop, so we subscribe instead of binding directly.
    $: segAudioEl = $segAudioElement;

    $: guideEntryLabel = tr($localeStore, m.segments_guide_entry_label());
    $: guideUnreadAriaLabel = tr($localeStore, m.segments_guide_unread_aria_label());
    $: subTabLabel = (id: SegmentsSubTab) => tr(
        $localeStore,
        id === 'samples' ? m.segments_subtab_samples() : m.segments_subtab_editor(),
    );
    $: subTabs = visibleSubTabs($canManageSamples);
    $: showSegmentEditor = $segmentsSubTab === 'editor'
        || ($segmentsSubTab === 'samples' && $isSampleMode);
    // Snap back if the samples tab disappears under a live capability change.
    $: if (!subTabs.includes($segmentsSubTab)) segmentsSubTab.set('editor');

    // Reciter-task subscription: bound to the selected reciter. The store
    // self-polls every 30 s while subscribed; we replace the binding when
    // the user switches reciter so only one task is polled at a time.
    let reciterTaskStore: Readable<ReciterTask | null> | null = null;
    let reciterTask: ReciterTask | null = null;
    let _taskUnsubscribe: (() => void) | null = null;

    function _bindTask(slug: string | null) {
        if (_taskUnsubscribe) {
            _taskUnsubscribe();
            _taskUnsubscribe = null;
        }
        reciterTaskStore = slug ? getReciterTaskStore(slug) : null;
        reciterTask = null;
        if (reciterTaskStore) {
            _taskUnsubscribe = reciterTaskStore.subscribe((v) => {
                reciterTask = v;
            });
        }
    }

    // Recompute the edit gate whenever the user (incl. guides_read) OR the task
    // changes. Threading allGuidesRead here is what lifts the first-edit
    // onboarding gate the instant the user opens the final guide — no task
    // poll needed. Replaces the imperative setEditingMode calls that used to
    // live in the task subscription / refresh paths.
    // Samples have no claim/lifecycle row: the gate is the capability alone.
    $: setEditingMode(
        $isSampleMode
            ? sampleEditingMode($currentUser)
            : syncEditingMode($currentUser, reciterTask, allGuidesRead($currentUser.guides_read)),
    );

    // Refresh task immediately after a state-mutating action; the polling
    // tick still fires every 30 s but acting users shouldn't wait for it.
    async function _refreshTask() {
        const slug = get(selectedReciter);
        if (!slug) return;
        const fresh = await refreshReciterTask(slug);
        reciterTask = fresh;
        // editingMode recomputes via the reactive statement on reciterTask.
        // /api/me's active_claim derives from state — pull a fresh copy too.
        void loadCurrentUser();
        // Force a catalog refetch so every surface lands the new bucket
        // without a page refresh. The footer chip's StatePill + the picker
        // modal both derive their bucket from `$catalogData` reactively, so
        // when this lands the claim flip (awaiting_review → under_review, or
        // back on unclaim) re-flows everywhere with no manual re-resolve.
        void loadCatalog(true);
    }

    // Out-of-band reciter changes: the admin Reviews tab's Segments deep-link
    // sets ``$selectedReciter`` directly (no picker event), so a reactive
    // subscription is what triggers the same _bindTask + onReciterChange
    // flow the picker fires. ``onPickerChange`` and ``loadReciters`` set
    // ``_lastBoundReciter`` BEFORE updating the store so this block skips
    // the work they've already done — no double-load.
    let _lastBoundReciter: string | null = null;
    let lastNormalReciter = '';
    $: if (typeof $selectedReciter === 'string' && $selectedReciter && $selectedReciter !== _lastBoundReciter) {
        _lastBoundReciter = $selectedReciter;
        if (!isSampleSlug($selectedReciter)) {
            lastNormalReciter = $selectedReciter;
            segmentsSubTab.set('editor');
        }
        _bindTask(isSampleSlug($selectedReciter) ? null : $selectedReciter);
        void onReciterChange($selectedReciter);
    }

    // Mark-ready review deep-link (from the owner "marked ready — reviewer left
    // notes" notification): once the reciter is switched in, open the read-only
    // review modal and consume the pending intent. Carries no openFlagged, so
    // ValidationPanel's flag-deep-link consumer ignores it.
    let markReadyReviewOpen = false;
    let markReadyReviewSlug: string | null = null;
    $: if (
        $pendingSegmentsDeepLink?.openMarkReadyReview &&
        typeof $selectedReciter === 'string' &&
        $selectedReciter
    ) {
        markReadyReviewSlug = $selectedReciter;
        markReadyReviewOpen = true;
        pendingSegmentsDeepLink.set(null);
    }

    // "Open in Segments" verse deep-link (Timestamps footer redirect): once the
    // target reciter's corpus is loaded, switch to the verse's chapter and
    // scroll its first segment row into view. `segAllData !== null` is the
    // "fresh corpus for the current reciter" signal — reloadCurrentReciter nulls
    // it (clearPerReciterState) before refetching — and the `slug` guard rejects
    // a stale corpus mid-switch. `tick()` defers past the synchronous reactive
    // flush so a concurrent switch (null → refetch) has settled before we act.
    // Consumed once, then cleared.
    let _verseDeepLinkDone = '';
    $: void maybeConsumeVerseDeepLink($pendingSegmentsDeepLink, $selectedReciter, $segAllData);

    async function maybeConsumeVerseDeepLink(
        dl: SegmentsDeepLink | null,
        reciter: string,
        all: SegAllState | null,
    ): Promise<void> {
        const fv = dl?.focusVerse;
        if (!fv || !all || reciter !== fv.slug) return;
        const key = `${fv.slug}:${fv.chapter}:${fv.verse}`;
        if (_verseDeepLinkDone === key) return;
        await tick();
        // Re-validate after the flush: only act when the fresh corpus for this
        // slug is resident and the intent hasn't been superseded (the pending-
        // store check is the mutex against concurrent reactive fires).
        if (get(segAllData) === null || get(selectedReciter) !== fv.slug) return;
        if (get(pendingSegmentsDeepLink) !== dl) return;
        _verseDeepLinkDone = key;
        pendingSegmentsDeepLink.set(null);
        await _focusVerse(fv.chapter, fv.verse);
    }

    async function _focusVerse(chapter: number, verse: number): Promise<void> {
        const chStr = String(chapter);
        if (get(selectedChapter) !== chStr) {
            // Mirror the manual Sura pick: collapse any open accordion + clear
            // the picker-display override, then load the chapter.
            valUiOpenCategory.set(null);
            clearAccordionPin();
            pickerDisplayChapter.set(null);
            selectedChapter.set(chStr);
            await loadChapterData(get(selectedReciter), chStr);
        }
        await tick();
        // Find the verse's first segment and scroll its row into view — the same
        // path Go-To / verse-jump use (SegmentRow watches targetSegmentIndex).
        const segs = getChapterSegments(chapter);
        const first = segs.find((s) => s.matched_ref?.startsWith(`${chapter}:${verse}:`));
        if (!first) return;
        selectedVerse.set(String(verse));
        targetSegmentIndex.set({ chapter: first.chapter ?? chapter, index: first.index });
    }

    $: filterBarHidden = $segAllData === null;

    // Inline header actions — Unclaim and Mark-ready operate on the
    // active claim's slug. We don't need busy spinners here; the network
    // round-trip refreshes the task and the UI flips accordingly.
    let chipActionBusy: '' | 'unclaim' | 'mark' = '';
    async function _unclaim(): Promise<void> {
        const slug = $selectedReciter;
        if (!slug || chipActionBusy) return;
        chipActionBusy = 'unclaim';
        try {
            await release(slug);
            await _refreshTask();
        } catch { /* toast already surfaced */ }
        finally { chipActionBusy = ''; }
    }
    async function _markReady(): Promise<void> {
        // SegmentsFooter mounts MarkReadyModal locally; the modal POSTs
        // the submission itself, then dispatches ``markReady`` purely as
        // a "refresh task" signal. This handler used to drive the POST.
        await _refreshTask();
    }

    let cssFontSize: string = '';
    let cssWordSpacing: string = '';

    async function loadReciters(): Promise<void> {
        try {
            const rs = await fetchJson<SegReciter[]>('/api/seg/reciters');
            segAllReciters.set(rs);
            const saved = localStorage.getItem(LS_KEYS.SEG_RECITER);
            // The Editor always restores a normal recitation. A sample is a
            // temporary selection inside Samples, never the persisted Editor.
            const validSaved = saved && !isSampleSlug(saved) && rs.some((r) => r.slug === saved) ? saved : null;
            if (!validSaved && saved) {
                // Drop the stale slug so we don't keep hammering 404 endpoints
                // every reload. The user picks a fresh reciter from the list.
                localStorage.removeItem(LS_KEYS.SEG_RECITER);
            }
            if (validSaved) {
                lastNormalReciter = validSaved;
                // The catalog FIRST, and awaited. `onReciterChange` resolves
                // the delivery's edition to pick its refs bundle, and
                // `deliveryRiwayah` answers Hafs for a slug whose roster has
                // not loaded — so kicking this after the await hydrated every
                // segment's `matched_text` from the HAFS script on a Warsh
                // delivery, and left those strings in the client model for the
                // session (the reactive block below moves the store, not the
                // rows already hydrated). Segments is the tab that mounts
                // without DashboardTab, so nothing else has fetched it.
                // Tolerated on failure: a Hafs guess renders wrong, an empty
                // tab renders nothing.
                await loadCatalog().catch((e) =>
                    console.error('catalog fetch failed before reciter bind:', e),
                );
                // Mark this slug as handled before updating the store so the
                // out-of-band reactive subscription below skips it (we run
                // _bindTask + onReciterChange imperatively right here).
                _lastBoundReciter = validSaved;
                selectedReciter.set(validSaved);
                _bindTask(validSaved);
                await onReciterChange(validSaved);
            }
        } catch (e) { console.error('Error loading seg reciters:', e); }
    }

    // Footer chip identity + bucket derive REACTIVELY from the shared catalog
    // snapshot keyed on the selected reciter. Deriving (not imperatively
    // assigning) is what keeps the chip's StatePill live: a claim/unclaim
    // calls `loadCatalog(true)`, and when the fresh snapshot lands this block
    // re-flows the new bucket automatically. The previous imperative resolver
    // read `get(catalogData)` synchronously — before the forced refetch landed
    // — so the footer pill stayed stale until a manual reload even though the
    // picker modal (which subscribes to `$catalogData`) updated correctly.
    $: ctxDelivery = (() => {
        const slug = $selectedReciter;
        if (!slug) return null;
        for (const r of $catalogData.reciters) {
            const d = r.deliveries.find((x) => x.slug === slug);
            if (d) return { reciter: r, delivery: d };
        }
        return null;
    })();
    $: contextName = ctxDelivery?.reciter.name ?? null;
    $: contextNameAr = ctxDelivery?.reciter.name_ar ?? null;
    $: contextCountry = ctxDelivery?.reciter.country ?? null;
    $: contextBucket = ctxDelivery?.delivery.bucket ?? null;
    $: contextRiwayah = ctxDelivery?.delivery.riwayah ?? null;
    $: contextStyle = ctxDelivery?.delivery.style ?? null;

    function onPickerChange(
        ev: CustomEvent<{
            slug: string;
            name: string;
            nameAr: string | null;
            country: string | null;
            bucket: import('../../lib/types/public-bucket').PublicBucket;
            riwayah: string;
            style: string;
        }>,
    ): void {
        // Identity + bucket flow through the reactive `ctxDelivery` derivation
        // keyed on `selectedReciter` (the picker reads the same catalog
        // snapshot), so we only set the slug and rebind here. ``_lastBoundReciter``
        // is set first so the out-of-band reactive subscription skips the
        // work we run imperatively below — no double-load.
        const { slug } = ev.detail;
        lastNormalReciter = slug;
        _lastBoundReciter = slug || null;
        selectedReciter.set(slug);
        _bindTask(slug || null);
        onReciterChange(slug);
    }
    async function onReciterChange(reciter: string): Promise<void> {
        if (reciter && !isSampleSlug(reciter)) {
            lastNormalReciter = reciter;
            localStorage.setItem(LS_KEYS.SEG_RECITER, reciter);
        }
        await reloadCurrentReciter();
    }
    /** Leaving a sample restores the normal Editor selection. */
    function leaveSample(tab: SegmentsSubTab): void {
        if (get(isSampleMode)) {
            _lastBoundReciter = lastNormalReciter || null;
            selectedReciter.set(lastNormalReciter);
            _bindTask(lastNormalReciter || null);
            if (lastNormalReciter) void onReciterChange(lastNormalReciter);
            else {
                selectedChapter.set('');
                clearPerReciterState();
            }
        }
        segmentsSubTab.set(tab);
    }
    /** Open a sample within Samples, straight on its one pseudo-chapter. */
    function openSample(slug: string): void {
        if (!isSampleSlug(get(selectedReciter))) lastNormalReciter = get(selectedReciter);
        _lastBoundReciter = slug;
        selectedReciter.set(slug);
        _bindTask(null);
        segmentsSubTab.set('samples');
        const chapter = get(samples).find((x) => x.slug === slug)?.pseudo_chapter;
        void onReciterChange(slug).then(() => {
            if (!chapter || get(selectedReciter) !== slug) return;
            selectedChapter.set(String(chapter));
            void loadChapterData(slug, String(chapter));
        });
    }
    function onChapterChange(ev: CustomEvent<string>): void {
        const v = ev.detail;
        // Manual Sura pick is the user's explicit gesture to view that
        // chapter — collapse any open accordion, clear the programmatic
        // picker-display override, then load.
        valUiOpenCategory.set(null);
        clearAccordionPin();
        pickerDisplayChapter.set(null);
        selectedChapter.set(v);
        void loadChapterData(get(selectedReciter), v);
    }
    async function _loadChapter(chapter: string): Promise<void> {
        await loadChapterData(get(selectedReciter), chapter);
    }
    function onVerseJump(ev: CustomEvent<string>): void {
        const v = ev.detail;
        if (!v) return;
        const chStr = get(selectedChapter);
        const chapter = parseInt(chStr);
        if (!chapter) return;
        const segs = getChapterSegments(chapter);
        const prefix = `${chapter}:${v}:`;
        const first = segs.find((s) => s.matched_ref?.startsWith(prefix));
        if (first) {
            // Reflect the jump target in the footer's Ayah trigger so the
            // user can see what they jumped to. `selectedVerse` is purely a
            // picker-label store now — the segments list is no longer
            // narrowed to the chosen ayah (see `filters.ts::computeDisplayed`),
            // so the user keeps every other card in view.
            selectedVerse.set(v);
            // Scroll the first matching row into view. SegmentRow watches
            // `targetSegmentIndex` and calls `scrollIntoView` when its row
            // matches — same path Go-To uses.
            targetSegmentIndex.set({ chapter: first.chapter ?? chapter, index: first.index });
            playFromSegment(first.index, first.chapter ?? chapter);
        }
    }

    async function onNavigationRestore(): Promise<void> {
        const saved = get(savedFilterView);
        if (!saved) return;
        savedFilterView.set(null);
        activeFilters.set(saved.filters);

        if (saved.chapter !== get(selectedChapter)) {
            selectedChapter.set(saved.chapter);
            await _loadChapter(saved.chapter);
        }
        selectedVerse.set(saved.verse);

        await tick();
        const listEl = get(segListElement);
        if (listEl) listEl.scrollTop = saved.scrollTop;
    }

    // Keep chapter-segment cache hot after chapter changes.
    $: if ($segAllData) { void getChapterSegments($selectedChapter || 0); }

    let autoSaveTimer: ReturnType<typeof setTimeout> | null = null;
    // Samples always autosave — there is no manual save flow for them.
    $: if (($autoSaveEnabled || $isSampleMode) && $dirtyTick > 0 && $isDirtyStore) {
        if (autoSaveTimer) clearTimeout(autoSaveTimer);
        autoSaveTimer = setTimeout(() => {
            if ($isDirtyStore) {
                void executeSave(true);
            }
        }, 1000); // 1s debounce
    }

    function onKeydown(e: KeyboardEvent): void {
        if (handleSegmentsKey(e)) e.preventDefault();
    }

    // The edition everything on this tab is rendered in. `segAllData.riwayah` is
    // the server's authoritative answer for the loaded delivery; the catalog is
    // the fast path used before that payload lands. `null` means the delivery
    // names a riwayah this build cannot serve — the tab then shows nothing
    // edition-specific rather than the previous delivery's words under this
    // one's coordinates.
    $: tabRiwayah = $segAllData?.riwayah ?? deliveryRiwayah($selectedReciter);
    $: editionRiwayah = isSupportedRiwayah(tabRiwayah) ? tabRiwayah : null;
    $: fontStack = editionFontStack(editionRiwayah);
    $: if (editionRiwayah) ensureEditionFont(editionRiwayah);

    // A cross-edition switch invalidates the coordinate vocabularies (muqattaat
    // openings, standalone allow-lists) the accordion and ref editor read, so
    // re-fetch them — and the reference bundle — for the new edition. An
    // unservable edition clears both instead: stale tables answer questions
    // ("is this one-word segment legitimate?") for a different mushaf.
    let _configRiwayah: string | null = null;
    $: if (editionRiwayah && editionRiwayah !== _configRiwayah) {
        _configRiwayah = editionRiwayah;
        void loadQuranRefs(editionRiwayah);
        void loadSegConfig(editionRiwayah).then((cfg) => {
            cssFontSize = cfg.fontSize;
            cssWordSpacing = cfg.wordSpacing;
        });
    } else if (!editionRiwayah && _configRiwayah !== null) {
        _configRiwayah = null;
        clearQuranRefs();
        clearSegConfig();
    }

    onMount(async () => {
        // Fire-and-forget the ~2.4 MB quran-refs bundle that only Segments
        // consumers (SegmentRow, ReferenceEditor, split/merge/auto-fix) need.
        // Idempotent — reciter-actions awaits this same promise before
        // hydrating per-segment matched_text, and the reactive block above
        // supersedes it once the delivery's own edition is known.
        // `null` means unservable, not "use Hafs" — the reactive block above
        // fetches for the real edition once the delivery is known.
        const known = deliveryRiwayah($selectedReciter);
        if (known) {
            void loadQuranRefs(known);
            await surahInfoReady;
            const cfg = await loadSegConfig(known);
            cssFontSize = cfg.fontSize;
            cssWordSpacing = cfg.wordSpacing;
        } else {
            await surahInfoReady;
        }
        await loadReciters();
        stopCatalogPoll = startCatalogPolling();
    });

    let stopCatalogPoll: (() => void) | null = null;
    onDestroy(() => stopCatalogPoll?.());
</script>

<svelte:window on:keydown={onKeydown} />

<div
    id="segments-panel-inner"
    style:--seg-font-size={cssFontSize || null}
    style:--seg-word-spacing={cssWordSpacing || null}
    style:--font-quran={fontStack}
>
    {#if $canManageSamples && !$historyVisible && !$savePreviewVisible}
        <nav class="seg-subtabs" aria-label="Segments sections">
            {#each subTabs as id (id)}
                <button
                    type="button"
                    class="seg-subtab"
                    class:active={$segmentsSubTab === id}
                    on:click={() => leaveSample(id)}
                >
                    {subTabLabel(id)}
                </button>
            {/each}
        </nav>
    {/if}

    {#if $segmentsSubTab === 'samples' && $canManageSamples && !$isSampleMode && !$historyVisible && !$savePreviewVisible}
        <SamplesPanel onOpen={openSample} />
    {/if}

    {#if showSegmentEditor && !$historyVisible && !$savePreviewVisible}
        <!-- Persistent entry point to the review guides + shortcuts. Opens the same
             modal the first-edit gate uses, in voluntary `browse` mode — available
             any time, not just when a blocked edit triggers the gate. The cyan
             unread dot mirrors the old per-accordion ? badge: shown only to a
             signed-in user who still has guides left to read. -->
        <div class="seg-guide-bar">
            <button
                type="button"
                class="seg-guide-entry"
                class:unread={$currentUser.hf_user_id != null
                    && !allGuidesRead($currentUser.guides_read)}
                on:click={() => openGuidesGate('browse')}
            >
                <span class="seg-guide-entry-icon" aria-hidden="true">📖</span>
                <span>{guideEntryLabel}</span>
                {#if $currentUser.hf_user_id != null && !allGuidesRead($currentUser.guides_read)}
                    <span class="seg-guide-entry-dot" aria-label={guideUnreadAriaLabel}></span>
                {/if}
            </button>
        </div>
    {/if}

    <!-- StatsPanel transitively imports chart.js (~85 KB br). Lazy-load so
         the charts chunk only ships when a maintainer/owner actually views
         the Segments tab — Dashboard / non-admin visitors never pay this cost. -->
    {#if ($currentUser.role === 'maintainer' || $currentUser.role === 'owner') && $segmentsSubTab === 'editor' && !$isSampleMode && !$historyVisible && !$savePreviewVisible}
        {#await import('./components/stats/StatsPanel.svelte') then mod}
            <svelte:component this={mod.default} />
        {/await}
    {/if}

    {#if showSegmentEditor && !$historyVisible && !$savePreviewVisible}
        <!-- The validation accordion is a GLOBAL view — always all chapters,
             never filtered by `selectedChapter`. Chapter-scoped review happens
             through the chapter-cards `<SegmentsList>` below; the accordion
             is the place to see every outstanding issue across the reciter
             regardless of which Sura is currently selected in the picker. -->
        <!-- Samples surface their signals as row chips instead of the accordion. -->
        <div id="seg-validation" class="seg-validation" use:waveformContainer>
            {#if !$isSampleMode}
                <ValidationPanel chapter={null} />
            {/if}
        </div>

        <FiltersBar hidden={filterBarHidden} />

        {#if !$accordionViewActive}
            <SegmentsList onRestore={onNavigationRestore} />
        {/if}

        <EditOverlay audioElRef={segAudioEl} />
    {/if}

    <HistoryPanel />

    <SavePreview />

    <SegmentsFooter
        {reciterTask}
        {chipActionBusy}
        {contextName}
        {contextNameAr}
        {contextCountry}
        {contextBucket}
        {contextRiwayah}
        {contextStyle}
        on:reciterChange={onPickerChange}
        on:backToSamples={() => leaveSample('samples')}
        on:chapterChange={onChapterChange}
        on:verseJump={onVerseJump}
        on:unclaim={_unclaim}
        on:markReady={_markReady}
        on:claimed={_refreshTask}
    />

    <!-- Guide modal host: a single instance driven by the `guideModal` store.
         A guide opens from the guide-index checklist (top entry point / gate),
         which must reach guides even for reciters that surface no accordion. -->
    {#if $guideModal}
        <AccordionGuideModal
            category={$guideModal.category}
            opener={$guideModal.opener}
            on:close={closeGuideModal}
        />
    {/if}

    <!-- First-edit onboarding gate / browsable guide index. Self-subscribes to
         the `guidesGate` store; opens guides via the host above. -->
    <GuidesGateModal />

    <!-- Read-only mark-ready review modal, opened by the owner notification
         deep-link (openMarkReadyReview). -->
    {#if markReadyReviewOpen && markReadyReviewSlug}
        <MarkReadyReviewModal
            slug={markReadyReviewSlug}
            onClose={() => (markReadyReviewOpen = false)}
        />
    {/if}
</div>

<style>
    /* Reserve space below the pinned SegmentsFooter so the list, validation
       panels, and edit overlay never disappear behind it on scroll. The
       footer min-height is `--seg-footer-h` (60px); add a token cushion. */
    #segments-panel-inner {
        padding-bottom: calc(var(--seg-footer-actual-h, var(--seg-footer-h, 60px)) + var(--s-3));
    }

    /* Editor / Samples sub-tab strip (maintainers only). */
    .seg-subtabs {
        display: flex;
        align-items: stretch;
        gap: var(--s-1);
        height: 32px;
        margin-bottom: var(--s-2, 8px);
        border-bottom: 1px solid var(--border-quiet);
    }
    .seg-subtab {
        position: relative;
        display: inline-flex;
        align-items: center;
        padding: 0 var(--s-3);
        background: transparent;
        border: 0;
        color: var(--text-muted);
        font: inherit;
        font-size: var(--fs-body);
        cursor: pointer;
    }
    .seg-subtab:hover:not(.active) { color: var(--text-secondary); }
    .seg-subtab.active { color: var(--text-primary); }
    .seg-subtab.active::after {
        content: '';
        position: absolute;
        left: var(--s-3);
        right: var(--s-3);
        bottom: -1px;
        height: 2px;
        background: var(--accent);
        border-radius: 2px 2px 0 0;
    }

    /* Persistent guide/shortcuts entry point at the top of the tab. */
    .seg-guide-bar {
        display: flex;
        justify-content: flex-start;
        margin-bottom: var(--s-2, 8px);
    }
    .seg-guide-entry {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        padding: 6px 12px;
        border: 1px solid var(--border-default);
        border-radius: 8px;
        background: var(--surface-sunken);
        color: var(--text-secondary);
        font-size: 0.84rem;
        cursor: pointer;
        transition: background 0.15s ease, border-color 0.15s ease;
    }
    .seg-guide-entry:hover,
    .seg-guide-entry:focus-visible {
        background: var(--panel-2);
        border-color: var(--border-strong);
        color: var(--text-primary);
        outline: none;
    }
    .seg-guide-entry-icon { font-size: 0.95rem; line-height: 1; }
    /* Unread: a signed-in reviewer still has required guides to read. Mirror the
       old per-accordion ? badge — cyan accent border + halo. */
    .seg-guide-entry.unread {
        border-color: var(--accent);
        box-shadow: 0 0 0 1px var(--accent-tint);
    }
    .seg-guide-entry-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--accent);
        box-shadow: 0 0 0 2px var(--accent-tint);
    }
</style>
