<script lang="ts">
    import { onMount } from 'svelte';

    import { common_nav_tab_dashboard } from './lib/paraglide/messages/common_nav_tab_dashboard';
    import { common_nav_tab_segments } from './lib/paraglide/messages/common_nav_tab_segments';
    import { common_nav_tab_timestamps } from './lib/paraglide/messages/common_nav_tab_timestamps';
    import { localeStore, tr } from './lib/i18n/locale-store';
    import AuthControls from './lib/components/AuthControls.svelte';
    import BookmarksPanel from './lib/components/BookmarksPanel.svelte';
    import ClaimConfirmModal from './lib/components/ClaimConfirmModal.svelte';
    import EditAffordancePopover from './lib/components/EditAffordancePopover.svelte';
    import ExternalLinks from './lib/components/ExternalLinks.svelte';
    import InfoModal from './lib/components/info/InfoModal.svelte';
    import LocaleSwitcher from './lib/components/LocaleSwitcher.svelte';
    import BottomPlayer from './lib/components/player/BottomPlayer.svelte';
    import NowReciting from './lib/components/player/NowReciting.svelte';
    import PlayerMetaChip from './lib/components/player/PlayerMetaChip.svelte';
    import SignInModal from './lib/components/SignInModal.svelte';
    import ThemeToggle from './lib/components/ThemeToggle.svelte';
    import ToastHost from './lib/components/ToastHost.svelte';
    import { dashPort } from './lib/playback/dash-port';
    import { loadCurrentUser } from './lib/stores/current-user';
    import { playerContext } from './lib/stores/player-context';
    import type { PublicDelivery } from './lib/types/generated/schemas';
    import { activeTab as activeTabStore, getActiveTab, setActiveTab } from './lib/utils/active-tab';
    import { LS_KEYS, TAB_NAMES } from './lib/utils/constants';
    import DashboardTab from './tabs/dashboard/DashboardTab.svelte';
    import SegmentsTab from './tabs/segments/SegmentsTab.svelte';
    import { segPort } from './tabs/segments/stores/playback';
    import TimestampsFooterAnalysis from './tabs/timestamps/components/TimestampsFooterAnalysis.svelte';
    import TimestampsFooterLeft from './tabs/timestamps/components/TimestampsFooterLeft.svelte';
    import TimestampsFooterOpenSegments from './tabs/timestamps/components/TimestampsFooterOpenSegments.svelte';
    import TimestampsFooterReport from './tabs/timestamps/components/TimestampsFooterReport.svelte';
    import TimestampsTab from './tabs/timestamps/TimestampsTab.svelte';

    // `activeTab` follows the shared store so external navigation (e.g. the
    // Bookmarks sidebar calling setActiveTab) switches tabs here too.
    let activeTab = getActiveTab();
    $: activeTab = $activeTabStore;
    // Lazy-mount tabs: defer Timestamps/Segments mount until the user actually
    // visits them. Once visited, the tab stays in the DOM (hidden) so its
    // state (loaded reciter, scroll position, edits) survives tab switches.
    // Dashboard-only visitors avoid the cold-load shard prefetch, segment-peaks
    // POST, quran-refs.json, etc.
    let mountedTabs = new Set<string>([activeTab]);
    $: if (activeTab && !mountedTabs.has(activeTab)) {
        mountedTabs = new Set([...mountedTabs, activeTab]);
    }
    // Tab side-effects run on every change — whether triggered by a tab-bar
    // button or external setActiveTab. Persist the choice and pause the ports
    // of the tabs being left (pause is a no-op when nothing's playing).
    $: applyTabSideEffects(activeTab);

    // Localized tab labels. Gating the message reads on `$localeStore` is the
    // legacy Svelte-4 reactivity idiom (mirrors `$: activeTab = $activeTabStore`
    // above): `tr` makes the statement depend on the store so the copy
    // re-evaluates when the locale switches in-place.
    $: dashboardTabLabel = tr($localeStore, common_nav_tab_dashboard());
    $: timestampsTabLabel = tr($localeStore, common_nav_tab_timestamps());
    $: segmentsTabLabel = tr($localeStore, common_nav_tab_segments());

    function applyTabSideEffects(tab: string): void {
        if (!tab) return;
        localStorage.setItem(LS_KEYS.ACTIVE_TAB, tab);
        // The shared shell player (dashPort) now drives BOTH Dashboard and
        // Timestamps, so we deliberately DON'T pause it on a Dashboard↔Timestamps
        // switch — that continuity (audio + animation + waveform cursor) is the
        // whole point. Segments owns its own transport (segPort) and shows its
        // own footer, so we silence + hide the shared player there.
        if (tab === TAB_NAMES.SEGMENTS) dashPort.pause();
        if (tab !== TAB_NAMES.SEGMENTS) segPort.pause();
    }

    function onCombinationSelect(ev: CustomEvent<PublicDelivery>): void {
        const d = ev.detail;
        playerContext.update((s) => ({ ...s, delivery: d, positionMs: 0, isPlaying: true }));
    }

    function cleanupLegacyAudioKeys(): void {
        // Phase 6 removes the Audio tab. Sweep any legacy insp_aud_* keys
        // so they don't linger and confuse future feature work.
        for (let i = localStorage.length - 1; i >= 0; i -= 1) {
            const k = localStorage.key(i);
            if (k && k.startsWith('insp_aud_')) localStorage.removeItem(k);
        }
    }

    onMount(() => {
        cleanupLegacyAudioKeys();
        // Quran refs (dk_words + verse_word_counts) is a 2.4 MB decoded
        // bundle used only by the Segments tab. Deferred to SegmentsTab's
        // onMount so Dashboard/Timestamps-only visitors don't pay the cost.
        const savedTab = localStorage.getItem(LS_KEYS.ACTIVE_TAB);
        const validTabs: string[] = [
            TAB_NAMES.DASHBOARD,
            TAB_NAMES.TIMESTAMPS,
            TAB_NAMES.SEGMENTS,
        ];
        if (savedTab && validTabs.includes(savedTab)) {
            setActiveTab(savedTab);
        } else {
            // First-time visitors and legacy `insp_active_tab='audio'`
            // users land on Dashboard.
            setActiveTab(TAB_NAMES.DASHBOARD);
        }
        void loadCurrentUser();

        // Re-resolve identity + capabilities when the tab regains focus, so an
        // owner's permission toggle (and role changes) reflect in the UI
        // without a manual reload — the backend already enforces immediately;
        // this keeps the affordances in sync for an idle, already-loaded user.
        const onVisible = (): void => {
            if (document.visibilityState === 'visible') void loadCurrentUser();
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    });
</script>

<div class="container">
    <header class:rail-aligned={activeTab === TAB_NAMES.DASHBOARD}>
        <ExternalLinks />
        <div class="tab-bar">
            <button class="tab-btn" class:active={activeTab === TAB_NAMES.DASHBOARD} data-tab={TAB_NAMES.DASHBOARD} on:click={() => setActiveTab(TAB_NAMES.DASHBOARD)}>{dashboardTabLabel}</button>
            <button class="tab-btn" class:active={activeTab === TAB_NAMES.TIMESTAMPS} data-tab={TAB_NAMES.TIMESTAMPS} on:click={() => setActiveTab(TAB_NAMES.TIMESTAMPS)}>{timestampsTabLabel}</button>
            <button class="tab-btn" class:active={activeTab === TAB_NAMES.SEGMENTS} data-tab={TAB_NAMES.SEGMENTS} on:click={() => setActiveTab(TAB_NAMES.SEGMENTS)}>{segmentsTabLabel}</button>
        </div>
        <div class="auth-controls">
            <LocaleSwitcher />
            <ThemeToggle />
            <AuthControls />
        </div>
    </header>

    <!-- ============ Dashboard Tab ============ -->
    {#if mountedTabs.has(TAB_NAMES.DASHBOARD)}
        <div hidden={activeTab !== TAB_NAMES.DASHBOARD}>
            <DashboardTab />
        </div>
    {/if}

    <!-- ============ Timestamps Tab ============ -->
    {#if mountedTabs.has(TAB_NAMES.TIMESTAMPS)}
        <div hidden={activeTab !== TAB_NAMES.TIMESTAMPS}>
            <TimestampsTab />
        </div>
    {/if}

    <!-- ============ Segments Tab ============ -->
    {#if mountedTabs.has(TAB_NAMES.SEGMENTS)}
        <div hidden={activeTab !== TAB_NAMES.SEGMENTS}>
            <SegmentsTab />
        </div>
    {/if}

    <!-- ============ Shared shell player ============
         One BottomPlayer + NowReciting for the whole app so playback,
         animation, and the waveform cursor survive Dashboard↔Timestamps
         switches (the player never unmounts). Hidden on Segments, which owns
         its own footer/transport. The Timestamps tab fills the player's
         `meta` (shuffle cycle + published-only reciter picker) and
         `center-trail` (analysis row) slots; Dashboard uses the default chip. -->
    <div hidden={activeTab === TAB_NAMES.SEGMENTS}>
        <NowReciting />
        <BottomPlayer>
            <svelte:fragment slot="meta">
                {#if activeTab === TAB_NAMES.TIMESTAMPS}
                    <TimestampsFooterLeft />
                {:else}
                    <PlayerMetaChip
                        reciter={$playerContext.reciter}
                        delivery={$playerContext.delivery}
                        on:select={onCombinationSelect}
                    />
                {/if}
            </svelte:fragment>
            <svelte:fragment slot="loc-lead">
                {#if activeTab === TAB_NAMES.TIMESTAMPS}
                    <TimestampsFooterReport />
                {/if}
            </svelte:fragment>
            <svelte:fragment slot="center-trail">
                {#if activeTab === TAB_NAMES.TIMESTAMPS}
                    <TimestampsFooterAnalysis />
                {/if}
            </svelte:fragment>
            <svelte:fragment slot="download-lead">
                {#if activeTab === TAB_NAMES.TIMESTAMPS}
                    <TimestampsFooterOpenSegments />
                {/if}
            </svelte:fragment>
        </BottomPlayer>
    </div>

</div>

<!-- Single global popover surfaced by `editGate` for non-editor clicks
     on edit affordances. -->
<EditAffordancePopover />

<!-- Root-mounted sign-in modal + toast host (Phase 3). -->
<SignInModal />
<ClaimConfirmModal />
<ToastHost />

<!-- Project-overview modal — opened from the dashboard ⓘ and the segments
     guides gate; single host so it looks identical everywhere. -->
<InfoModal />

<!-- Browser-local bookmarks sidebar. -->
<BookmarksPanel />

<style>
    header {
        display: grid;
        grid-template-columns: 1fr auto 1fr;
        align-items: center;
        gap: 12px;
    }
    .tab-bar {
        display: flex;
        align-items: center;
        gap: 0;
        grid-column: 2;
        justify-self: center;
    }
    .auth-controls {
        display: flex;
        align-items: center;
        gap: 8px;
        grid-column: 3;
        justify-self: end;
    }
    /* Dashboard lays its content out as two 320px rails inset one --gutter from
       the page edge. Match that in the header (Dashboard only, since the other
       tabs are full-bleed): the project-link icons line up with the left rail's
       text column, and the auth/theme cluster with the right rail's edge. */
    header.rail-aligned :global(.link-rail) {
        /* the rail's labels (e.g. "Status") sit 16px inside the rail box and the
           icon carries 8px of inner padding, so glyph offset = gutter + 16 - 8 */
        padding-inline-start: calc(var(--gutter) + 8px);
    }
    header.rail-aligned .auth-controls {
        padding-inline-end: var(--gutter);
    }
</style>
