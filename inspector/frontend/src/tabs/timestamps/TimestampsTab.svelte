<script lang="ts">
    /**
     * TimestampsTab — content surface for the redesigned Timestamps tab.
     *
     * Playback is owned by the SHARED shell player (dashPort + playerContext),
     * not this tab — the tab is published-by_surah-only and rides the same
     * continuous-chapter transport the Dashboard uses, so switching tabs keeps
     * audio / animation / waveform cursor in sync (the player never unmounts).
     *
     * This tab renders only: the waveform + the mega analysis display
     * (words / letters / phonemes). The word-by-word animation
     * lives in the shared NowReciting bar; the footer controls (reciter picker,
     * shuffles, analysis toggles, loop, bookmark, shortcuts) live in the shared
     * BottomPlayer's slots (TimestampsFooterLeft / TimestampsFooterRight).
     *
     * `loadedVerse` is repurposed as the FOCUS verse — the verse under the
     * playhead (or clicked) that the analysis + waveform inspect. It follows the
     * shared playhead; it does not drive audio.
     */

    import { onDestroy, onMount } from 'svelte';
    import { get } from 'svelte/store';

    import * as m from '$lib/paraglide/messages';

    import { fetchSurahsForDelivery } from '../../lib/api/audio-surahs';
    import { setAdoptedSource } from '../../lib/playback/adopt-signal';
    import { adjacentAyahStartMs } from '../../lib/playback/ayah-seek';
    import { signalDashSeekIntent } from '../../lib/playback/dash-buffering';
    import { ensureDashCovering, ensureDashCoveringRange } from '../../lib/playback/dash-covering';
    import { dashPort } from '../../lib/playback/dash-port';
    import { dashProxyUrl } from '../../lib/playback/dash-prewarm';
    import { probeDirectPlayable } from '../../lib/playback/play-url';
    import { recycleAsShadow } from '../../lib/playback/shadow-audio';
    import {
        clearShuffle,
        consumeShuffle,
        type ConsumedShuffle,
        primeShuffle,
    } from '../../lib/playback/shuffle-prewarm';
    import {
        recitationConfigStore,
        recitationFocus,
    } from '../../lib/recitation-animation/recitation-settings';
    import { hasCapability } from '../../lib/stores/capabilities';
    import { currentUser } from '../../lib/stores/current-user';
    import { pendingTsNavigation } from '../../lib/stores/navigation';
    import { playerContext, setIsLoading, setIsPlaying } from '../../lib/stores/player-context';
    import type { TsConfigResponse } from '../../lib/types/generated/schemas';
    import { getActiveTab, activeTab as activeTabStore } from '../../lib/utils/active-tab';
    import { modelForTheme, resolveHighlightVars } from '../../lib/utils/highlight-model';
    import { themeStore, THEME_CHANGE_EVENT } from '../../lib/stores/theme.svelte';
    import { LS_KEYS, TAB_NAMES } from '../../lib/utils/constants';
    import { shouldHandleKey } from '../../lib/utils/keyboard-guard';
    import { prewarmVersePeaks } from '../../lib/utils/peaks-fetch';
    import { wordBoundaryScan } from '../../lib/utils/word-boundary';
    import { loadCatalog as loadPublicCatalog, catalogData } from '../dashboard/stores/catalog-data';
    import TimestampsWaveform from './components/TimestampsWaveform.svelte';
    import ReportControlStrip from './components/report/ReportControlStrip.svelte';
    import { exitReportMode, reportContext, reportModeActive } from './stores/report-mode';
    import { loadVerseReports } from './stores/ts-reports';
    import TsValidationPanel from './components/TsValidationPanel.svelte';
    import TimedAnalysisRow from './components/TimedAnalysisRow.svelte';
    import WordTimedRow from './components/WordTimedRow.svelte';
    import {
        assembleOccasion,
        assembleWaslGroup,
        chapterVerseRefs,
        getRandomTarget,
        loadChapterShard,
        loadConfig,
        loadDk,
        loadManifest,
        loadQpc,
        loadTsValidation,
        loadVbrChapters,
        reciterAudioFromManifest,
        shardOccasions,
        type TsReciterAudio,
    } from './services/ts_client';
    import { isWordShard } from '../../lib/types/ts-client';
    import { editionFontStack, ensureEditionFont } from '../../lib/refs/edition-font';
    import { DEFAULT_SDK_RIWAYAH, toInspectorSlug } from '../../lib/riwayat';
    import type { ChapterOccasion } from '../../lib/recitation-data/occasions';
    import { isInWaslGroup, waslGroupOf } from '../../lib/recitation-data/wasl';
    import { findTsEntryBySlug, isTsCapable, resolveTsDeliveries } from './services/ts-published';
    import {
        deliveryRiwayah,
        showLetters,
        showPhonemes,
        wordProfile,
        tsConfig,
    } from './stores/display';
    import { tsLoading } from './stores/loading';
    import { initTajweedSettings } from './stores/tajweed-settings';
    import { exitLoop, loopTarget } from './stores/playback';
    import { manualShuffleRequest, shuffleAyah, shuffleMode } from './stores/shuffle';
    import { tsValidation } from './stores/validation';
    import {
        focusWaslGroup,
        loadedVerse,
        selectedChapter,
        selectedReciter,
        selectedVerse,
        type TsFocusWaslGroup,
        type TsLoadedVerse,
    } from './stores/verse';
    import { occasionIndexAt, resolveShuffleTick, shouldFireShuffle } from './utils/shuffle-tick';
    import { setupZoomLifecycle } from './utils/zoom';

    // ---- Local display constants ----
    const TS_EASING_NONE = 'none';
    const TS_EASING_DEFAULT = 'linear';
    const TS_UNIFIED_DISPLAY_MAX_HEIGHT_PX = 800;
    /** Fire the ayah-end shuffle this many ms before the exact verse end so the
     *  jump lands without a beat of the next ayah leaking in. */
    const SHUFFLE_END_GUARD_MS = 40;

    // ---- Component refs ----
    // Either analysis row — the two are interchangeable to the tab, which only
    // drives the per-frame highlight and the scroll-into-view.
    let unifiedEl: { updateHighlights: () => void; scrollActiveIntoView: () => void } | undefined;
    let waveformTabEl: TimestampsWaveform;

    // ---- Chapter focus data ----
    /** One contiguous recitation of a verse (a verse may recur → several
     *  occasions). The waveform + analysis focus one occasion at a time. */
    interface ChapOccasion {
        ref: string;
        startMs: number;
        endMs: number;
        lv: TsLoadedVerse;
        /** The raw occasion carries native connected-reading adjacency. */
        occ: ChapterOccasion;
        /** The cross-verse waṣl group this occasion belongs to (by_surah only),
         *  precomputed at chapter load; null/undefined when standalone. */
        waslGroup?: TsFocusWaslGroup;
    }
    /** Every occasion in the chapter, in audio order (a verse ref may repeat). */
    let chapterOccasions: ChapOccasion[] = [];
    /** First-occasion start ms per DISTINCT verse, ascending — feeds the
     *  prev/next-ayah keyboard nav (which steps verses, not occasions). */
    let distinctVerseStartMs: number[] = [];
    /** Distinct verse refs in audio order — paired with `distinctVerseStartMs`. */
    let distinctVerseRefs: string[] = [];
    let loadedChapterKey = ''; // `${slug}:${chapter}` currently assembled
    let focusIdx = -1; // index into `chapterOccasions` of the focused occasion
    let focusRef = ''; // ref of the focused occasion (for display / verse nav)
    let manifestSlugs = new Set<string>();
    /** Set when a context switch should seek to a specific verse once the new
     *  chapter's data + audio are ready (shuffle / validation jump / entry). */
    let pendingSeekRef: string | null = null;
    let shuffleFiredForIdx = -1; // guard so the shuffle fires once per occasion

    /** Loop is anchored to the occasion that was in focus when the loop was
     *  engaged — NOT the live focus occasion. Captured on the loopTarget null→set
     *  transition so a ~1-frame overshoot past the occasion boundary can't drift
     *  the focus to the next occasion and (a) flip the analysis text + (b) blow up
     *  `endAbs` so the seek-back never fires again. Null while not looping. */
    let loopAnchor: ChapOccasion | null = null;
    let loopAnchorIdx = -1;

    // ---------------------------------------------------------------------
    // Colors (shared accent → analysis highlight vars)
    // ---------------------------------------------------------------------
    // One resolve owns every highlight CSS var (idle/active cells, both modes,
    // the karaoke track, light/dark) so the surfaces can't drift. See
    // `lib/utils/highlight-model.ts`.
    $: cfg = $tsConfig;
    // The highlight model is theme-conditional (light gets a darker legible band
    // + auto ink). Mirror the runes theme store into a legacy-reactive local so
    // the `$:` below recomputes the analysis vars on a theme flip.
    let curTheme = themeStore.current;
    $: hlVars = resolveHighlightVars($recitationConfigStore.highlightColor, modelForTheme(curTheme));
    $: hlVarsText = Object.entries(hlVars)
        .map(([k, v]) => `${k}: ${v}`)
        .join('; ');
    $: wordDur =
        cfg && cfg.anim_transition_easing !== TS_EASING_NONE
            ? `${cfg.anim_word_transition_duration}s`
            : '0s';
    $: charDur =
        cfg && cfg.anim_transition_easing !== TS_EASING_NONE
            ? `${cfg.anim_char_transition_duration}s`
            : '0s';
    $: easing =
        cfg && cfg.anim_transition_easing !== TS_EASING_NONE
            ? cfg.anim_transition_easing
            : TS_EASING_DEFAULT;
    $: wordTransition = `opacity ${wordDur} ${easing}`;
    $: charTransition = `opacity ${charDur} ${easing}`;

    // ---------------------------------------------------------------------
    // Initial load
    // ---------------------------------------------------------------------
    async function init(): Promise<void> {
        loadConfig().then((c) => tsConfig.set(c as TsConfigResponse));
        void loadQpc().catch(() => {});
        void loadDk().catch(() => {});

        // Display prefs (letters/phonemes) hydrate regardless of
        // the (now-removed) view mode.
        const sL = localStorage.getItem(LS_KEYS.TS_SHOW_LETTERS);
        const sP = localStorage.getItem(LS_KEYS.TS_SHOW_PHONEMES);
        if (sL !== null) showLetters.set(sL === 'true');
        if (sP !== null) showPhonemes.set(sP === 'true');
        initTajweedSettings();

        try {
            const [, manifest] = await Promise.all([loadPublicCatalog(), loadManifest()]);
            manifestSlugs = new Set(Object.keys(manifest.reciters ?? {}));
        } catch (e) {
            console.error('TS: catalog/manifest load failed', e);
        }

        // The `syncChapter` reactive already fired once, at mount, while
        // `manifestSlugs` was still empty — so it bailed at the published-set
        // gate. Nothing re-triggers it when the continuity path below keeps
        // `playerContext` as-is, which is exactly the cold-load case (the tab
        // opens on a delivery restored from the last session). Kick it here,
        // now that the gate is open; it is idempotent via `loadedChapterKey`.
        const restored = get(playerContext);
        void syncChapter(restored.delivery?.slug ?? '', restored.surahNum ?? 0);

        // A bookmark deep-link owns the first load — let its consumer run.
        if (navHandled || get(pendingTsNavigation)) return;
        await resolveEntry(getActiveTab() === TAB_NAMES.TIMESTAMPS);
    }

    /** All published, by_surah, timestamped (reciter, delivery) pairs. */
    function tsEntries() {
        return resolveTsDeliveries(get(catalogData).reciters, manifestSlugs);
    }

    // ---------------------------------------------------------------------
    // Entry / continuity / cache resolution
    // ---------------------------------------------------------------------
    async function resolveEntry(autoplay: boolean): Promise<void> {
        const ctx = get(playerContext);
        // 1. Continuity: the shared player is already on a TS-capable reciter.
        if (isTsCapable(ctx.delivery, manifestSlugs) && ctx.surahNum) {
            cacheReciter(ctx.delivery!.slug);
            return; // the reactive chapter-load picks it up
        }
        // 2. Cache: last published TS-capable reciter the user had here. Resolve
        //    a valid (chapter, ayah) from the manifest — the reciter may not have
        //    chapter 1 timestamps, so never default to 1.
        const cached = localStorage.getItem(LS_KEYS.TS_RECITER) ?? '';
        const cachedEntry = findTsEntryBySlug(get(catalogData).reciters, manifestSlugs, cached);
        if (cachedEntry) {
            const t = await getRandomTarget({ reciter: cachedEntry.delivery.slug }).catch(() => null);
            if (t) {
                await startEntry(cachedEntry.reciter, cachedEntry.delivery, { chapter: t.chapter, verseRef: t.verseRef }, autoplay);
                return;
            }
            // Cached reciter has no usable shard (stale/broken) — drop it and
            // fall through to a random working reciter.
            localStorage.removeItem(LS_KEYS.TS_RECITER);
        }
        // 3. First entry / fallback: a random reciter that actually has a usable
        //    shard. Some manifest reciters are stale (listed but shards 404), so
        //    probe a few and take the first that yields a target.
        const entries = shuffleArr(tsEntries());
        for (const e of entries.slice(0, 8)) {
            const target = await getRandomTarget({ reciter: e.delivery.slug }).catch(() => null);
            if (target) {
                await startEntry(e.reciter, e.delivery, { chapter: target.chapter, verseRef: target.verseRef }, autoplay);
                return;
            }
        }
    }

    function shuffleArr<T>(arr: T[]): T[] {
        const a = [...arr];
        for (let i = a.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [a[i], a[j]] = [a[j]!, a[i]!];
        }
        return a;
    }

    function cacheReciter(slug: string): void {
        if (slug) localStorage.setItem(LS_KEYS.TS_RECITER, slug);
    }

    /** Point the shared player at a TS reciter+chapter and (optionally) queue a
     *  seek to a specific verse once its data lands. */
    async function startEntry(
        reciter: import('../../lib/types/generated/schemas').PublicReciter,
        delivery: import('../../lib/types/generated/schemas').PublicDelivery,
        target: { chapter: number; verseRef: string } | null,
        autoplay: boolean,
    ): Promise<void> {
        cacheReciter(delivery.slug);
        const chapter = target?.chapter ?? 1;
        pendingSeekRef = target?.verseRef ?? null;
        _autoplayPending = autoplay;
        playerContext.update((s) => ({
            ...s,
            reciter,
            delivery,
            surahNum: chapter,
            positionMs: 0,
            isPlaying: autoplay || s.isPlaying,
        }));
    }

    let _autoplayPending = false;

    // ---------------------------------------------------------------------
    // Chapter focus data — react to the shared player's reciter + surah
    // ---------------------------------------------------------------------
    $: void syncChapter($playerContext.delivery?.slug ?? '', $playerContext.surahNum ?? 0);

    // Load the focus verse's reports (in-grid public flags + report-mode seeds)
    // on every verse change, and discard an active report session if the verse
    // moves out from under it (report mode is verse-scoped).
    let _verseReportsKey = '';
    $: void _syncVerseReports($playerContext.delivery?.slug ?? '', $selectedVerse);
    function _syncVerseReports(slug: string, verseKey: string): void {
        const key = `${slug}|${verseKey}`;
        if (key === _verseReportsKey) return;
        _verseReportsKey = key;
        const ctx = get(reportContext);
        if (ctx && (ctx.slug !== slug || ctx.verseKey !== verseKey)) exitReportMode();
        void loadVerseReports(slug, verseKey);
    }

    // Arm the whole-verse playback lock when a report session starts (exit clears
    // the loop itself). With the playhead pinned, the focus verse only changes on a
    // genuine manual nav — which _syncVerseReports above turns into exit + discard.
    let _reportLockArmed = false;
    $: _syncReportLock($reportModeActive);
    function _syncReportLock(active: boolean): void {
        if (active === _reportLockArmed) return;
        _reportLockArmed = active;
        if (active) armVerseLock();
    }

    /**
     * Forget which edition and profile the last shard carried.
     *
     * Both stores are module-level and outlive one delivery. Left set, a Hafs
     * delivery whose shard never loads keeps the previous Warsh one's proxy
     * badge, its disabled letter/tajweed controls, its font — and renders the
     * word row against an empty reading, i.e. a blank analysis grid.
     */
    function resetShardEdition(): void {
        wordProfile.set(false);
        deliveryRiwayah.set(DEFAULT_SDK_RIWAYAH);
    }

    async function syncChapter(slug: string, chapter: number): Promise<void> {
        if (!slug || !chapter) return;
        if (!manifestSlugs.has(slug)) return; // non-published reciter on dashboard
        const key = `${slug}:${chapter}`;
        if (key === loadedChapterKey) return;
        if (get(tsLoading)) return;
        // Guard: this reciter may not have timestamps for `chapter` (its
        // ts_chapters is a subset of all 114). Loading the shard would 404.
        // Correct the shared surah to a valid chapter — that also re-points the
        // audio and re-runs this reactive. Cheap: manifest is a warm singleton.
        const manifest = await loadManifest();
        const block = manifest.reciters?.[slug];
        if (!block) {
            resetShardEdition();
            return;
        }
        const blockChapters = block.ts_chapters ?? [];
        if (!blockChapters.includes(chapter)) {
            resetShardEdition();
            const valid = blockChapters[0];
            if (valid && valid !== chapter) {
                pendingSeekRef = null;
                playerContext.update((s) => ({ ...s, surahNum: valid, positionMs: 0 }));
            }
            return;
        }
        tsLoading.set(true);
        try {
            // Canonical per-chapter audio URL comes from /api/audio/surahs (the
            // audio-manifest sidecar) — same source playback uses, usually warm
            // from the player; fetched in parallel with the shard.
            const [shard, qpc, dk, surahs] = await Promise.all([
                loadChapterShard(slug, chapter),
                loadQpc(),
                loadDk(),
                fetchSurahsForDelivery(block.source ?? '', slug).catch(
                    (): Awaited<ReturnType<typeof fetchSurahsForDelivery>> => ({}),
                ),
            ]);
            // The shard itself decides whether this view has letters, phonemes
            // and cell geometry — see `wordProfile` in stores/display.
            wordProfile.set(isWordShard(shard));
            // The shard also names the edition whose script it carries, which
            // is what the tab must typeset it in — see `--font-quran` below.
            const wordShard = isWordShard(shard) ? shard : null;
            deliveryRiwayah.set(wordShard?._meta.riwayah ?? DEFAULT_SDK_RIWAYAH);
            ensureEditionFont(toInspectorSlug(wordShard?._meta.riwayah));
            const reciterAudio = reciterAudioFromManifest(manifest, slug);
            if (!reciterAudio) return;
            const chapterUrl = surahs[String(chapter)]?.url ?? '';
            const occasions: ChapOccasion[] = [];
            for (const occ of shardOccasions(shard)) {
                const data = assembleOccasion(slug, occ, qpc, dk, reciterAudio, chapterUrl);
                occasions.push({
                    ref: occ.ref,
                    startMs: data.time_start_ms,
                    endMs: data.time_end_ms,
                    lv: { data, tsSegOffset: data.time_start_ms / 1000, tsSegEnd: data.time_end_ms / 1000 },
                    occ,
                });
            }
            occasions.sort((a, b) => a.startMs - b.startMs);
            attachWaslGroups(occasions, slug, qpc, dk, reciterAudio, chapterUrl);
            chapterOccasions = occasions;
            // New chapter's occasions just replaced the array — invalidate the
            // focus cursor so the focus pass below ALWAYS re-points `loadedVerse`.
            // Without this, `focusAt` no-ops when the new target lands on the same
            // index as the previous chapter's focus (e.g. surah A:1 → surah B:1,
            // both occasion 0), leaving the analysis view pinned to the old verse
            // while audio + teleprompter (driven off playerContext) move on.
            focusIdx = -1;
            focusRef = '';
            // First-occasion start per distinct verse (ascending) feeds the
            // prev/next-ayah keyboard nav via `adjacentAyahStartMs` (which trusts
            // sorted input) — stepping verses, not occasions.
            const seenRefs = new Set<string>();
            const verseStarts: number[] = [];
            const verseRefs: string[] = [];
            for (const o of occasions) {
                if (seenRefs.has(o.ref)) continue;
                seenRefs.add(o.ref);
                verseStarts.push(o.startMs);
                verseRefs.push(o.ref);
            }
            distinctVerseStartMs = verseStarts;
            distinctVerseRefs = verseRefs;
            loadedChapterKey = key;
            selectedReciter.set(slug);
            selectedChapter.set(String(chapter));

            // Owner/maintainer: load validation flags for this reciter.
            if (hasCapability(get(currentUser), 'timestamps.view_validation')) {
                void loadTsValidation(slug).then((doc) => {
                    if (get(selectedReciter) === slug) tsValidation.set(doc);
                });
            } else {
                tsValidation.set(null);
            }

            // Apply a queued seek (entry / shuffle / validation jump), else focus
            // the verse under the current playhead.
            if (pendingSeekRef) {
                const i = firstOccasionIndexOfRef(pendingSeekRef);
                pendingSeekRef = null;
                if (i >= 0) {
                    const v = chapterOccasions[i]!;
                    setFocusByIndex(i);
                    ensureDashCoveringRange(v.startMs, v.endMs);
                    dashPort.seek(v.startMs);
                    signalDashSeekIntent();
                    if (_autoplayPending) {
                        _autoplayPending = false;
                        try { dashPort.play(); } catch { /* autoplay policy */ }
                    }
                }
            } else {
                focusAt(dashPort.currentTimeMs());
            }
            // Warm the next shuffle target now that the chapter is loaded.
            void primeShuffleSlot();
        } catch (e) {
            console.error('TS: chapter sync failed', e);
        } finally {
            tsLoading.set(false);
        }
    }

    /** Precompute the cross-verse waṣl group for each member occasion (by_surah
     *  only) so focusing any member shows the whole continuous span. The merged
     *  `TsVerseData` is built once per group and shared across its members (only
     *  the focus ref differs); a standalone occasion gets no `waslGroup`. Group
     *  members stay adjacent after the start-ms sort (a waṣl is gapless), so the
     *  walk over the sorted raw occasions matches native waṣl adjacency. */
    function attachWaslGroups(
        occasions: ChapOccasion[],
        reciter: string,
        qpc: Record<string, { text?: string }>,
        dk: Record<string, { text?: string }>,
        reciterAudio: TsReciterAudio,
        chapterUrl: string,
    ): void {
        if (reciterAudio.audio_category !== 'by_surah') return; // span needs one chapter file
        const raw = occasions.map((o) => o.occ);
        for (let i = 0; i < occasions.length; i++) {
            if (!isInWaslGroup(raw, i)) continue;
            const grp = waslGroupOf(raw, i);
            // The merged cells are 0-anchored to the group start; the analysis +
            // waveform key off `span[0]` as the display offset.
            const data = assembleWaslGroup(
                reciter, raw.slice(grp.fromIdx, grp.toIdx + 1),
                occasions[i]!.ref, qpc, dk, reciterAudio, chapterUrl,
            );
            occasions[i]!.waslGroup = {
                data, span: [grp.startMs, grp.endMs], refs: grp.refs, focusRef: occasions[i]!.ref,
            };
        }
    }

    /** Focus the occasion at index `i` in `chapterOccasions`. */
    function setFocusByIndex(i: number): void {
        const v = chapterOccasions[i];
        if (!v) return;
        focusIdx = i;
        focusRef = v.ref;
        loadedVerse.set(v.lv);
        // The whole continuous waṣl span (read-only context) when the focus is a
        // group member; null for a standalone verse.
        focusWaslGroup.set(v.waslGroup ?? null);
        selectedVerse.set(v.ref);
        // Publish to the shell-level focus store so NowReciting's filmstrip
        // bookmark button can mirror the focus without running its own rAF.
        // Verse refs are "surah:ayah"; parse defensively.
        const [s, a] = v.ref.split(':');
        const surah = Number(s);
        const ayah = Number(a);
        if (Number.isFinite(surah) && Number.isFinite(ayah) && surah > 0 && ayah > 0) {
            recitationFocus.set({ surah, ayah });
        }
    }

    /** Index of the FIRST occasion of `ref` (audio order), or -1 if absent. */
    function firstOccasionIndexOfRef(ref: string): number {
        return chapterOccasions.findIndex((o) => o.ref === ref);
    }

    /** Focus the occasion containing `ms` (chapter-absolute), else the nearest
     *  preceding one. No-op if the focus is unchanged. */
    function focusAt(ms: number): void {
        const i = occasionIndexAt(chapterOccasions, ms);
        if (i >= 0 && i !== focusIdx) setFocusByIndex(i);
    }

    /** Lock playback to the focused occasion for a report session: a whole-verse
     *  loop covering its trailing silence up to the next verse's start. Pins the
     *  playhead inside the verse so free play can't auto-advance (or fire shuffle)
     *  out of the session; selecting a timing cell narrows this to the cell loop. */
    function armVerseLock(): void {
        const occ = chapterOccasions[focusIdx];
        if (!occ) return;
        exitLoop(); // drop any prior loop so the anchor re-captures this verse
        loopTarget.set({ kind: 'word', startSec: 0, endSec: (occ.endMs - occ.startMs) / 1000, wordIndex: -1 });
    }

    /** True while a cross-source jump is mid-swap: the shared player already points
     *  at (and may already be playing) the new chapter, but `chapterOccasions` /
     *  `focusRef` / `loadedVerse` still describe the previous chapter until
     *  syncChapter finishes loading and sets `loadedChapterKey`. `loadedChapterKey`
     *  trails `playerContext` across the whole window (incl. before `tsLoading`
     *  flips at line ~289), so it — not `tsLoading` — is the reliable guard. tick()
     *  and the media-clock backstop both no-op during this window so the new
     *  playhead time isn't read against stale verses (wrong-verse focus +
     *  double-fire). */
    function chapterSwapInFlight(): boolean {
        const ctx = get(playerContext);
        return `${ctx.delivery?.slug ?? ''}:${ctx.surahNum ?? 0}` !== loadedChapterKey;
    }

    // ---------------------------------------------------------------------
    // Per-frame tick (focus + highlights + waveform cursor + loop + shuffle)
    // ---------------------------------------------------------------------
    let _raf: number | null = null;

    function startTick(): void {
        if (_raf !== null) return;
        const loop = (): void => {
            tick();
            _raf = requestAnimationFrame(loop);
        };
        _raf = requestAnimationFrame(loop);
    }
    function stopTick(): void {
        if (_raf !== null) { cancelAnimationFrame(_raf); _raf = null; }
    }

    function tick(): void {
        const ms = dashPort.currentTimeMs();

        // While a report session is active the focus verse must stay looped so
        // playback can't advance out of the session. Re-arm the whole-verse loop
        // whenever it's missing — covers an entry-time no-op (occasions not yet
        // loaded) and any cleared narrowed cell/gap loop.
        if (get(reportModeActive) && !get(loopTarget)) armVerseLock();

        // rAF seek-back loop (no kill-switch on the shared port; ≤1 frame
        // overshoot). The loop window is computed against the CAPTURED loop
        // anchor offset, never the live focus verse — a boundary-frame
        // overshoot must not drift the focus and so re-base endAbs onto the
        // next ayah (which would break the loop permanently). While looping,
        // focus stays PINNED to the loop verse (no focusAt advance).
        const loop = get(loopTarget);
        if (loop && loopAnchor) {
            // The loop target's start/end are relative to the DISPLAY base — the
            // waṣl group start when the anchor is a group member, else its own
            // occasion start (matches TimedAnalysisRow's display offset).
            const offsetSec = (loopAnchor.waslGroup ? loopAnchor.waslGroup.span[0] : loopAnchor.startMs) / 1000;
            const startAbs = (loop.startSec + offsetSec) * 1000;
            const endAbs = (loop.endSec + offsetSec) * 1000;
            if (ms >= endAbs) {
                ensureDashCovering(startAbs);
                dashPort.seek(startAbs);
            }
            // Keep focus on the loop's occasion regardless of where the playhead
            // momentarily sits (the seek-back is async).
            if (focusIdx !== loopAnchorIdx) setFocusByIndex(loopAnchorIdx);
            refreshDisplays();
            return;
        }

        // Resolve the ayah-end shuffle boundary BEFORE advancing focus. Advancing
        // focus first (the old order) would flip the verse the boundary is measured
        // against to the NEXT ayah the instant the playhead crosses a contiguous
        // seam — so a frame that overshoots the seam (heavy repaint right after a
        // jump starves rAF, most visible when the just-loaded ayah is short) re-bases
        // onto the next ayah and leaks it in full. Deciding fire-vs-focus together,
        // against the auditioned ayah's captured end, bounds the leak to one frame.
        const fv = get(loadedVerse);
        const outcome = resolveShuffleTick({
            occasions: chapterOccasions,
            ms,
            swapInFlight: chapterSwapInFlight(),
            armed: getActiveTab() === TAB_NAMES.TIMESTAMPS && !get(loopTarget) && get(shuffleAyah) && !get(reportModeActive),
            focusEndMs: fv ? fv.tsSegEnd * 1000 : null,
            guardMs: SHUFFLE_END_GUARD_MS,
            firedForCurrentFocus: shuffleFiredForIdx === focusIdx,
        });
        // Mid-swap: freeze focus + display (no refresh) so the new playhead time
        // isn't drawn against the old chapter's occasion; syncChapter resumes us.
        if (outcome.kind === 'idle') return;
        if (outcome.kind === 'fire') {
            shuffleFiredForIdx = focusIdx;
            void shuffleJump(); // sets the new focus itself; hold focus this frame
        } else if (outcome.kind === 'focus' && outcome.idx >= 0 && outcome.idx !== focusIdx) {
            setFocusByIndex(outcome.idx);
        }
        refreshDisplays();
    }

    // Media-clock backstop for the ayah-end shuffle: fire the jump when the playhead
    // reaches the auditioned ayah's end. Driven off onTimeUpdate/onEnded so the
    // boundary is still caught when rAF is throttled (backgrounded tab) — where the
    // tick (and so focus) isn't advancing, so the focus verse IS the auditioned one.
    // Gated to the active Timestamps tab so the shared dashPort backstop can't hijack
    // Dashboard playback (its own gapless advance owns dashPort.onEnded when active).
    function maybeFireShuffle(ms: number): boolean {
        if (getActiveTab() !== TAB_NAMES.TIMESTAMPS) return false;
        if (get(reportModeActive)) return false; // verse is locked during a report session
        // Mid-swap the timeupdate clock is the NEW chapter's but loadedVerse is the
        // OLD one — measuring against it would fire against the wrong ayah. tick()
        // also freezes focus here, so this is belt-and-braces, not the sole guard.
        if (chapterSwapInFlight()) return false;
        const fv = get(loadedVerse);
        const fire = shouldFireShuffle({
            armed: !get(loopTarget) && get(shuffleAyah),
            ms,
            focusEndMs: fv ? fv.tsSegEnd * 1000 : null,
            guardMs: SHUFFLE_END_GUARD_MS,
            firedForCurrentFocus: shuffleFiredForIdx === focusIdx,
        });
        if (fire) {
            shuffleFiredForIdx = focusIdx;
            void shuffleJump();
            return true;
        }
        return false;
    }

    function refreshDisplays(): void {
        unifiedEl?.updateHighlights();
        waveformTabEl?.drawOverlays();
    }

    // ---------------------------------------------------------------------
    // Shuffle / navigation jumps
    // ---------------------------------------------------------------------
    async function shuffleJump(autoplay = true): Promise<void> {
        exitLoop();
        const cur = get(playerContext);
        const curSlug = cur.delivery?.slug ?? '';
        // The jump kind is the active mode at fire time: both → random reciter,
        // else (ayah) → random ayah of the same reciter.
        const randomReciter = get(shuffleMode) === 2;
        // Prefer the pre-rolled (warm) target so the jump can adopt gaplessly;
        // else roll a fresh one (gapped fallback).
        const consumed = consumeShuffle();
        // Warm miss → the jump must roll a target and load a new source (both
        // awaited, and slow off the bucket) while nothing else stops the current
        // chapter, so it would keep playing into the next verse until the new source
        // lands — the short-verse leak. Pause now so the gap is silence; the landing
        // (seekFocus / syncChapter's pendingSeek) resumes playback on the target.
        // Warm hits stay gapless: adoptGapless swaps + plays synchronously.
        const wasPlaying = !consumed && !dashPort.paused;
        if (!consumed) dashPort.pause();
        const target = consumed?.target
            ?? await getRandomTarget(randomReciter ? {} : { reciter: curSlug }).catch(() => null);
        if (!target) {
            if (consumed) discardWarm(consumed.el);
            else if (wasPlaying) dashPort.play(); // no target to jump to — restore playback
            return;
        }

        const sameSource = target.reciter === curSlug && target.chapter === cur.surahNum;
        if (sameSource) {
            // In-chapter jump is already gapless (plain seek) — drop the warm el.
            if (consumed) discardWarm(consumed.el);
            const i = firstOccasionIndexOfRef(target.verseRef);
            if (i >= 0) seekFocus(i, autoplay);
        } else if (consumed) {
            adoptGapless(consumed, autoplay);
        } else {
            // Cross-source look-ahead miss → gapped fallback.
            await jumpToTarget(target.reciter, target.chapter, target.verseRef, autoplay);
        }
        void primeShuffleSlot(); // re-arm the next jump
    }

    function discardWarm(el: HTMLAudioElement): void {
        try { el.pause(); el.removeAttribute('src'); el.load(); el.remove(); } catch { /* ignore */ }
    }

    /** Gapless cross-source jump: adopt the prewarmed element onto dashPort and
     *  start it WITHOUT a fresh load, then update playerContext (guarded by the
     *  adopt signal so BottomPlayer.reactToContext doesn't re-load + re-decode). */
    function adoptGapless(c: ConsumedShuffle, autoplay: boolean): void {
        const entry = findTsEntryBySlug(get(catalogData).reciters, manifestSlugs, c.target.reciter);
        if (!entry) { discardWarm(c.el); return; }
        const shouldPlay = autoplay || !dashPort.paused;
        const startMs = c.seekSec * 1000;

        // Match the triple BottomPlayer would build so its eventual setSource is
        // a guaranteed no-op (belt-and-braces alongside the adopt signal).
        dashPort.setSource({ audioUrl: c.rawUrl, cbrSrc: c.proxyUrl, reciter: c.target.reciter, vbr: false });
        setAdoptedSource({ deliverySlug: c.target.reciter, surahNum: c.target.chapter, srcUrl: c.proxyUrl });

        const oldEl = dashPort.element;
        dashPort.adoptElement(c.el, c.proxyUrl);
        if (oldEl && oldEl !== c.el) recycleAsShadow(oldEl, 'any');

        if (shouldPlay) dashPort.seekAndPlay(startMs); else dashPort.seek(startMs);
        setIsLoading(false);
        setIsPlaying(shouldPlay);

        pendingSeekRef = c.target.verseRef; // syncChapter focuses it once data lands
        _autoplayPending = false;           // already playing the adopted element
        playerContext.update((s) => ({
            ...s,
            reciter: entry.reciter,
            delivery: entry.delivery,
            surahNum: c.target.chapter,
            positionMs: startMs,
            isPlaying: shouldPlay,
        }));
    }

    /** Look-ahead: warm the single target the current shuffle mode will jump to,
     *  so the next jump can adopt it gaplessly. Off → nothing warm. Builds the
     *  SAME proxy URL the shared player would, so the warm element matches on
     *  consume. Range-windowed (the warm-seek triggers an HTTP Range request) —
     *  not a full-chapter download. Clears any stale (old-mode) target first so a
     *  verse-end racing the re-warm misses cleanly instead of jumping wrong. */
    async function primeShuffleSlot(): Promise<void> {
        clearShuffle();
        const mode = get(shuffleMode);
        if (mode === 0) return; // off → no look-ahead (no jump happens)
        const randomReciter = mode === 2;
        const curSlug = get(playerContext).delivery?.slug ?? '';
        const target = await getRandomTarget(randomReciter ? {} : { reciter: curSlug }).catch(() => null);
        if (!target) return;
        const entry = findTsEntryBySlug(get(catalogData).reciters, manifestSlugs, target.reciter);
        if (!entry) return;
        let urls: Awaited<ReturnType<typeof fetchSurahsForDelivery>>;
        try {
            urls = await fetchSurahsForDelivery(entry.delivery.source, target.reciter);
        } catch {
            return;
        }
        const u = urls[String(target.chapter)];
        if (!u) return;
        const vbrChapters = await loadVbrChapters(target.reciter);
        if (vbrChapters.includes(target.chapter)) return;
        const rawUrl = u.url;
        // Direct CDN when the host passes the CORS + Range probe, else proxy —
        // MUST match what BottomPlayer builds (`dashProxyUrl`) for the adopt.
        await probeDirectPlayable(rawUrl);
        const proxyUrl = dashProxyUrl(target.reciter, rawUrl);
        // Verse start (chapter-absolute seconds) → warm-seek position.
        let seekSec = 0;
        try {
            const [shard, qpc, dk, manifest] = await Promise.all([
                loadChapterShard(target.reciter, target.chapter),
                loadQpc(), loadDk(), loadManifest(),
            ]);
            const ra = reciterAudioFromManifest(manifest, target.reciter);
            const occs = ra ? shardOccasions(shard) : [];
            const idx = occs.findIndex((o) => o.ref === target.verseRef);
            const occ = idx >= 0 ? occs[idx] : undefined;
            const data = ra && occ
                ? assembleOccasion(target.reciter, occ, qpc, dk, ra, rawUrl)
                : null;
            if (data && ra) {
                seekSec = data.time_start_ms / 1000;
                // Warm the whole waṣl group span when the target is in one
                // (by_surah) so the merged waveform + analysis land ready; else
                // just the target verse. Baked tier or ffmpeg/CDN fallback.
                let warmStartMs = Math.max(0, Math.round(data.time_start_ms));
                let warmEndMs = Math.round(data.time_end_ms);
                if (ra.audio_category === 'by_surah' && isInWaslGroup(occs, idx)) {
                    const grp = waslGroupOf(occs, idx);
                    warmStartMs = Math.max(0, Math.round(grp.startMs));
                    warmEndMs = Math.round(grp.endMs);
                }
                void prewarmVersePeaks(
                    target.reciter,
                    target.chapter,
                    data.audio_url ?? rawUrl,
                    warmStartMs,
                    warmEndMs,
                );
            }
        } catch { /* seek 0 is an acceptable fallback */ }
        primeShuffle({ target, proxyUrl, rawUrl, seekSec });
    }

    /** Jump to (reciter, chapter, verseRef). Same chapter → seek in place.
     *  Different chapter/reciter → switch the shared context + queue the seek. */
    async function jumpToTarget(
        slug: string,
        chapter: number,
        verseRef: string,
        autoplay = true,
    ): Promise<void> {
        const curSlug = get(playerContext).delivery?.slug ?? '';
        const curChapter = get(playerContext).surahNum ?? 0;
        if (slug === curSlug && chapter === curChapter) {
            const i = firstOccasionIndexOfRef(verseRef);
            if (i >= 0) seekFocus(i, autoplay);
            return;
        }
        const entry = findTsEntryBySlug(get(catalogData).reciters, manifestSlugs, slug);
        if (!entry) return;
        pendingSeekRef = verseRef;
        _autoplayPending = autoplay || !dashPort.paused; // keep or create playback across the jump
        playerContext.update((s) => ({
            ...s,
            reciter: entry.reciter,
            delivery: entry.delivery,
            surahNum: chapter,
            positionMs: 0,
            isPlaying: _autoplayPending,
        }));
    }

    function tryPlay(): void {
        try { dashPort.play(); } catch { /* autoplay policy */ }
    }

    function seekFocus(i: number, autoplay = true): void {
        const v = chapterOccasions[i];
        if (!v) return;
        const wasPlaying = !dashPort.paused;
        setFocusByIndex(i);
        ensureDashCoveringRange(v.startMs, v.endMs);
        dashPort.seek(v.startMs);
        // Raise the buffering spinner (debounced) — clears on the first audible
        // frame, no-ops if the occasion start is already buffered.
        signalDashSeekIntent();
        if (autoplay || wasPlaying) tryPlay();
        refreshDisplays();
    }

    function seekMsAndResume(targetMs: number): void {
        ensureDashCovering(targetMs);
        dashPort.seek(targetMs);
        signalDashSeekIntent();
        tryPlay();
        focusAt(targetMs);
        refreshDisplays();
    }

    /** Step to the prev/next DISTINCT verse in the current chapter (keyboard
     *  [ / ]) — seeks to that verse's first occasion. */
    function navigateVerse(delta: number): void {
        const vi = distinctVerseRefs.indexOf(focusRef);
        if (vi < 0) return;
        const ni = vi + delta;
        if (ni < 0 || ni >= distinctVerseRefs.length) return;
        exitLoop();
        const i = firstOccasionIndexOfRef(distinctVerseRefs[ni]!);
        if (i >= 0) seekFocus(i);
    }

    /** Validation panel click — jump the focus (+ playhead) to a flagged verse,
     *  switching chapter if needed. */
    function jumpToFlaggedVerse(verseKey: string): void {
        const slug = get(playerContext).delivery?.slug ?? '';
        const surah = parseInt(verseKey.split(':')[0] ?? '0', 10);
        if (!slug || !surah) return;
        void jumpToTarget(slug, surah, verseKey);
    }

    // ---------------------------------------------------------------------
    // Bookmark deep-link (from BookmarksPanel) — load a specific verse with a
    // random TS-capable reciter and seek there.
    // ---------------------------------------------------------------------
    let navHandled = false;
    $: if ($pendingTsNavigation) consumePendingNav($pendingTsNavigation);

    function consumePendingNav(nav: {
        surah: number;
        ayah: number;
        autoplay: boolean;
        slug?: string;
    }): void {
        navHandled = true;
        pendingTsNavigation.set(null);
        if (nav.slug) {
            // Flag-notification redirect — go to that exact reciter + verse.
            void jumpToTarget(nav.slug, nav.surah, `${nav.surah}:${nav.ayah}`, nav.autoplay);
        } else {
            void loadBookmarkedVerse(nav.surah, nav.ayah, nav.autoplay);
        }
    }

    async function loadBookmarkedVerse(surah: number, ayah: number, autoplay: boolean): Promise<void> {
        const verseRef = `${surah}:${ayah}`;
        for (const e of tsEntries()) {
            // Try the first reciter that has this verse in its shard.
            try {
                const shard = await loadChapterShard(e.delivery.slug, surah);
                if (!chapterVerseRefs(shard).includes(verseRef)) continue;
                pendingSeekRef = verseRef;
                _autoplayPending = autoplay;
                playerContext.update((s) => ({
                    ...s,
                    reciter: e.reciter,
                    delivery: e.delivery,
                    surahNum: surah,
                    positionMs: 0,
                    isPlaying: autoplay || s.isPlaying,
                }));
                return;
            } catch {
                continue;
            }
        }
    }

    // Prewarm the next sequential verse so it renders instantly on advance:
    // peaks always (baked tier or ffmpeg/CDN fallback). Within-chapter; the
    // cross-chapter / random next is warmed by primeShuffleSlot. All calls are
    // idempotent (shared caches).
    $: prewarmNext($loadedVerse);
    function prewarmNext(lv: typeof $loadedVerse): void {
        if (!lv) return;
        const next = focusIdx >= 0 ? chapterOccasions[focusIdx + 1] : undefined;
        if (!next) return;
        void prewarmVersePeaks(
            next.lv.data.reciter,
            next.lv.data.chapter,
            next.lv.data.audio_url ?? '',
            Math.max(0, Math.round(next.startMs)),
            Math.round(next.endMs),
        );
    }

    // (The once-per-occasion shuffle guard resets implicitly: `shuffleFiredForIdx`
    //  is compared against `focusIdx`, so a focus change re-arms it.)

    // ---------------------------------------------------------------------
    // Keyboard (operates on the shared dashPort)
    // ---------------------------------------------------------------------
    function onKeydown(e: KeyboardEvent): void {
        if (!shouldHandleKey(e, 'timestamps')) return;
        if (getActiveTab() !== TAB_NAMES.TIMESTAMPS) return;
        const cur = dashPort.currentTimeMs() / 1000;
        const lv = get(loadedVerse);
        switch (e.code) {
            case 'Space':
                e.preventDefault();
                if (dashPort.paused) tryPlay(); else dashPort.pause();
                break;
            case 'ArrowLeft': {
                e.preventDefault();
                exitLoop();
                const t = adjacentAyahStartMs(distinctVerseStartMs, cur * 1000, -1);
                if (t !== null) seekMsAndResume(t);
                break;
            }
            case 'ArrowRight': {
                e.preventDefault();
                exitLoop();
                const t = adjacentAyahStartMs(distinctVerseStartMs, cur * 1000, 1);
                if (t !== null) seekMsAndResume(t);
                break;
            }
            case 'ArrowUp': {
                e.preventDefault();
                exitLoop();
                if (!lv) break;
                const t = cur - lv.tsSegOffset;
                const prev = wordBoundaryScan(lv.data.words, t, 'up');
                seekMsAndResume(((prev ?? 0) + lv.tsSegOffset) * 1000);
                break;
            }
            case 'ArrowDown': {
                e.preventDefault();
                exitLoop();
                if (!lv) break;
                const t = cur - lv.tsSegOffset;
                const next = wordBoundaryScan(lv.data.words, t, 'down');
                if (next !== null) seekMsAndResume((next + lv.tsSegOffset) * 1000);
                break;
            }
            case 'KeyR':
                void shuffleJump();
                break;
            case 'BracketLeft':
                navigateVerse(-1);
                break;
            case 'BracketRight':
                navigateVerse(+1);
                break;
            case 'KeyJ':
                e.preventDefault();
                unifiedEl?.scrollActiveIntoView();
                break;
            case 'KeyL':
                e.preventDefault();
                showLetters.update((v) => !v);
                break;
            case 'KeyP':
                e.preventDefault();
                showPhonemes.update((v) => !v);
                break;
        }
    }

    // ---------------------------------------------------------------------
    // Lifecycle — run the tick only while this tab is active.
    // ---------------------------------------------------------------------
    $: if ($activeTabStore === TAB_NAMES.TIMESTAMPS) startTick(); else stopTick();

    let _primedOnce = false;

    onMount(() => {
        setupZoomLifecycle();
        void init();
        // Re-resolve continuity when the tab regains focus (e.g. after the
        // dashboard switched to a non-published reciter).
        const unsubTab = activeTabStore.subscribe((t) => {
            if (t === TAB_NAMES.TIMESTAMPS && manifestSlugs.size > 0 && !navHandled) {
                void resolveEntry(false);
            }
        });
        // Re-warm the look-ahead when the shuffle MODE flips (the warm slot is
        // mode-specific). Subscribe to the derived `shuffleMode` (deduped by
        // integer) so one cycle step = one reprime — not the double-fire two
        // boolean subscriptions caused. Skip the initial fire — syncChapter
        // primes the first slot once data is loaded.
        const reprime = (): void => { if (_primedOnce) void primeShuffleSlot(); };
        const unsubShuf = shuffleMode.subscribe(reprime);
        let lastManualShuffle = get(manualShuffleRequest);
        const unsubManualShuffle = manualShuffleRequest.subscribe((n) => {
            if (n === lastManualShuffle) return;
            lastManualShuffle = n;
            if (!_primedOnce) return;
            void shuffleJump(true);
        });
        // Capture the loop's verse anchor on engage (null→set) and drop it on
        // exit. The loop setters (footer button, dbl-click, waveform click) all
        // run against the CURRENT focus verse, so the live `loadedVerse` at the
        // transition IS the loop verse. tick() reads `loopAnchor`, not the live
        // focus, so focus drift can't corrupt the loop window.
        const unsubLoop = loopTarget.subscribe((lt) => {
            if (lt && !loopAnchor) {
                loopAnchor = chapterOccasions[focusIdx] ?? null;
                loopAnchorIdx = loopAnchor ? focusIdx : -1;
            } else if (!lt) {
                loopAnchor = null;
                loopAnchorIdx = -1;
            }
        });
        // Media-clock backstop for the ayah-end shuffle: timeupdate/ended fire off
        // the audio clock (even when the tab is backgrounded and rAF is throttled),
        // so the boundary can't overshoot by seconds. Deduped against the rAF tick
        // by the once-per-verse guard.
        const offTimeUpdate = dashPort.onTimeUpdate((fileMs) => maybeFireShuffle(fileMs));
        const offEnded = dashPort.onEnded(() => maybeFireShuffle(Number.POSITIVE_INFINITY));
        // Recompute the analysis highlight vars when the theme flips.
        const onTheme = (): void => { curTheme = themeStore.current; };
        window.addEventListener(THEME_CHANGE_EVENT, onTheme);
        _primedOnce = true;
        return () => {
            unsubTab(); unsubShuf(); unsubManualShuffle(); unsubLoop();
            offTimeUpdate(); offEnded();
            window.removeEventListener(THEME_CHANGE_EVENT, onTheme);
        };
    });

    onDestroy(() => {
        stopTick();
        // Don't leak this tab's last focus to other surfaces (Dashboard's
        // NowReciting subscribes to it).
        recitationFocus.set(null);
        resetShardEdition();
    });
</script>

<svelte:window on:keydown={onKeydown} />

<div
    id="timestamps-panel"
    style={hlVarsText}
    style:--font-quran={editionFontStack(toInspectorSlug($deliveryRiwayah))}
    style:--unified-display-max-height="{cfg?.unified_display_max_height ?? TS_UNIFIED_DISPLAY_MAX_HEIGHT_PX}px"
    style:--anim-word-transition={wordTransition}
    style:--anim-char-transition={charTransition}
    style:--anim-word-spacing={cfg?.anim_word_spacing ?? ''}
    style:--anim-line-height={cfg?.anim_line_height ?? ''}
    style:--anim-font-size={cfg?.anim_font_size ?? ''}
    style:--analysis-word-font-size={cfg?.analysis_word_font_size ?? ''}
    style:--analysis-letter-font-size={cfg?.analysis_letter_font_size ?? ''}
>
    <main>
        {#if $tsValidation}
            <div class="ts-validation-row">
                <TsValidationPanel
                    doc={$tsValidation}
                    activeVerse={$selectedVerse}
                    onselect={jumpToFlaggedVerse}
                />
            </div>
        {/if}

        <div class="waveform-words-row" class:ts-region-loading={$tsLoading}>
            {#if $reportModeActive}
                <ReportControlStrip />
            {:else}
                <TimestampsWaveform bind:this={waveformTabEl} />
            {/if}
            {#if $wordProfile}
                <WordTimedRow bind:this={unifiedEl} />
            {:else}
                <TimedAnalysisRow bind:this={unifiedEl} />
            {/if}
        </div>
    </main>
</div>

<style>
    #timestamps-panel {
        /* Reserve space for the shell-owned footer + now-reciting bar. */
        padding-bottom: calc(var(--player-h, 72px) + var(--now-reciting-h, 0px));
    }
    .waveform-words-row {
        transition: opacity 0.12s ease;
    }
    .waveform-words-row.ts-region-loading {
        opacity: 0.55;
        pointer-events: none;
    }
    .ts-validation-row {
        width: 100%;
        margin: 0 0 8px;
    }
</style>
