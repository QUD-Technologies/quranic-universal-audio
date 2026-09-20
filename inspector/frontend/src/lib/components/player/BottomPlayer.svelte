<script lang="ts">
    /**
     * Dashboard-scoped persistent BottomPlayer.
     *
     * Pinned to the viewport bottom while the Dashboard tab is active.
     * The App-shell `hidden` cascade hides it when the Dashboard tab is
     * inactive (transport keeps its position; explicit play required on
     * tab return).
     *
     * Owns the dashPort, binds the <audio> element, subscribes to
     * player-context for source changes, drives controls via dashPort,
     * and updates positionMs / isPlaying via port event subscriptions.
     */
    import { onDestroy, onMount } from 'svelte';

    import { localizeDigits } from '$lib/i18n/format';
    import { localeStore, tr } from '$lib/i18n/locale-store';
    import * as m from '$lib/paraglide/messages';

    import { clickOutside } from '../../actions/click-outside';
    import { fetchSurahsForDelivery, type SurahEntry } from '../../api/audio-surahs';
    import { setAdoptedSource, takeAdoptedSource } from '../../playback/adopt-signal';
    import { ensureAudioContextRunning } from '../../playback/audio-graph';
    import { installDashBuffering, signalDashSeekIntent } from '../../playback/dash-buffering';
    import {
        adjacentAyahStartFromIndex,
        adjacentAyahStartMs,
        nearestAyahStartMs,
    } from '../../playback/ayah-seek';
    import {
        clearDashPrewarm,
        consumeDashCommitted,
        dashProxyUrl,
        primeDashCommitted,
        primeDashSpeculative,
    } from '../../playback/dash-prewarm';
    import { dashPort } from '../../playback/dash-port';
    import { probeDirectPlayable } from '../../playback/play-url';
    import { exitLoop, loopTarget } from '../../playback/loop';
    import { recycleAsShadow } from '../../playback/shadow-audio';
    import { vbrCoveringRangeFor } from '../../playback/vbr-covering';
    import {
        recitationAyahAt,
        recitationAyahs,
        recitationAyahStarts,
        recitationConfigStore,
    } from '../../recitation-animation/recitation-settings';
    import { accentVarText } from '../../utils/accent-override';
    import { theme$ } from '../../stores/theme.svelte';
    import { loadVbrChapters } from '../../recitation-data/ts-source';
    import {
        loadPersistedSlice,
        persistSlice,
        playerContext,
        setDuration,
        setIsLoading,
        setIsPlaying,
        setPosition,
        setSpeed,
    } from '../../stores/player-context';
    import { progressHoverMs, progressScrubMs } from '../../stores/progress-hover';
    import type { PublicDelivery } from '../../types/generated/schemas';
    import { getActiveTab } from '../../utils/active-tab';
    import { TAB_NAMES } from '../../utils/constants';
    import { DASHBOARD_SPEEDS } from '../../utils/speed-control';
    import { getSurahInfo, surahInfoReady } from '../../utils/surah-info';
    import HighlightColorPicker from './HighlightColorPicker.svelte';
    import PlayerControls from './PlayerControls.svelte';
    import PlayerMetaChip from './PlayerMetaChip.svelte';
    import PlayerProgress from './PlayerProgress.svelte';
    import SurahPicker from './SurahPicker.svelte';

    let audioEl: HTMLAudioElement | null = null;
    let urls: Record<string, SurahEntry> = {};
    let lastDeliverySlug: string | null = null;
    let lastSurahNum: number | null = null;
    let vbrChapters = new Set<number>();
    let surahPopoverOpen = false;
    // WS6 intent-prewarm state (helpers + constants below reactToContext).
    let _warmDebounce: ReturnType<typeof setTimeout> | null = null;
    let _nearEndWarmedSurah: number | null = null;

    onMount(() => {
        dashPort.attachElement(audioEl);
        const slice = loadPersistedSlice();
        if (slice.speed && slice.speed !== 1) setSpeed(slice.speed);
        dashPort.setPlaybackRate(slice.speed);

        const unsubPlay = dashPort.onPlay(() => setIsPlaying(true));
        const unsubPause = dashPort.onPause(() => setIsPlaying(false));
        const unsubLoad = dashPort.onLoad(() => {
            // Read the LIVE element off the port, not `audioEl` — a gapless
            // shuffle adopt swaps `dashPort.element` to a prewarmed element,
            // leaving the template-bound `audioEl` stale.
            const dur = dashPort.element?.duration ?? 0;
            const win = dashPort.window;
            if (win?.isClip) {
                setPosition(win.startMs);
                return;
            }
            setPosition(0, Number.isFinite(dur) ? dur * 1000 : 0);
            // NOTE: canplay (readyState 3) is NOT audible — clearing the ring
            // here stops it 1-3s before sound. `onPlaying` is the single
            // steady-state clear (actual audible resume); see it below.
        });
        const unsubTime = dashPort.onTimeUpdate((fileMs) => {
            const dur = dashPort.element?.duration;
            setPosition(fileMs, dashPort.window?.isClip ? undefined : dur ? dur * 1000 : undefined);
            maybeWarmNext(fileMs, dur ? dur * 1000 : 0);
        });
        // Buffering spinner: a single controller owns `isLoading` for the shared
        // port. It raises on `waiting`/seek-intent (debounced so in-buffer seeks
        // don't flicker) and clears on `playing` (first audible frame) / pause /
        // ended / error. Every Dashboard + Timestamps seek funnels through the
        // port, so the spinner tracks actual playback — not the click. Seek-
        // initiation raises (the `signalDashSeekIntent()` calls below + in
        // TimestampsTab/Waveform) make it appear immediately on a cold (re)load.
        const disposeBuffering = installDashBuffering(setIsLoading);
        // Chapter-end gapless auto-advance (Dashboard tab only — on Timestamps,
        // the shuffle handler owns end-of-chapter; see TimestampsTab onEnded).
        const unsubEnded = dashPort.onEnded(() => advanceGaplessOnEnd());

        // Speculative warm around the hovered / scrubbed position of the current
        // chapter (warms canplay so a cold first seek isn't a 1-3s stall). Both
        // share the same debounce; null (hover-leave / scrub-release) no-ops.
        const unsubHover = progressHoverMs.subscribe((ms) => warmAtPositionDebounced(ms));
        const unsubScrub = progressScrubMs.subscribe((ms) => warmAtPositionDebounced(ms));

        return () => {
            unsubPlay();
            unsubPause();
            unsubLoad();
            unsubTime();
            disposeBuffering();
            unsubEnded();
            unsubHover();
            unsubScrub();
            if (_warmDebounce) clearTimeout(_warmDebounce);
            clearDashPrewarm();
            dashPort.attachElement(null);
        };
    });

    onDestroy(() => {
        dashPort.pause();
    });

    $: void reactToContext($playerContext);

    async function reactToContext(ctx: typeof $playerContext): Promise<void> {
        const delivery = ctx.delivery;
        const surahNum = ctx.surahNum;
        if (!delivery || surahNum === null) {
            if (lastDeliverySlug !== null) {
                dashPort.setSource(null);
                lastDeliverySlug = null;
                lastSurahNum = null;
                urls = {};
            }
            return;
        }
        const wasPlaying = ctx.isPlaying;
        const deliverySwitched = delivery.slug !== lastDeliverySlug;
        // True only when switching away from an already-loaded combination
        // (not on initial load from null). Used to auto-play on paused switch.
        const isActiveCombinationSwitch = deliverySwitched && lastDeliverySlug !== null;

        if (deliverySwitched) {
            try {
                const [nextUrls, nextVbrChapters] = await Promise.all([
                    fetchSurahsForDelivery(delivery.source, delivery.slug),
                    loadVbrChapters(delivery.slug),
                ]);
                urls = nextUrls;
                vbrChapters = new Set(nextVbrChapters);
                // Warm the host verdict (CORS + Range) off the first chapter so
                // the per-chapter probe at play time is a single head fetch.
                const probeUrl = Object.values(nextUrls)[0]?.url;
                if (probeUrl) await probeDirectPlayable(probeUrl);
            } catch {
                urls = {};
                vbrChapters = new Set();
            }
            lastDeliverySlug = delivery.slug;
            persistSlice({
                deliverySlug: delivery.slug,
                surahNum,
                speed: ctx.speed,
            });
        }

        // Fallback: this combo may not carry the currently-selected surah
        // (default 1 when entering a reciter for the first time, or the
        // user's prior pick carried across a reciter switch). Pick the
        // first available chapter and re-enter via setSurah — the store
        // change retriggers this function with a valid surahNum.
        if (!urls[String(surahNum)]) {
            const available = Object.keys(urls)
                .map(Number)
                .filter(Number.isFinite)
                .sort((a, b) => a - b);
            if (available.length > 0 && available[0] !== surahNum) {
                playerContext.update((s) => ({ ...s, surahNum: available[0]!, positionMs: 0 }));
                return;
            }
        }

        if (surahNum !== lastSurahNum || deliverySwitched) {
            // Gapless-shuffle adopt: the Timestamps fire path already swapped a
            // prewarmed element onto dashPort and started it, then set this
            // signal before mutating playerContext. The source is already
            // satisfied — do bookkeeping only and return, so we DON'T pause +
            // re-decode (which would reintroduce the gap we just avoided).
            // `urls`/`lastDeliverySlug`/`persistSlice` were already handled by
            // the `deliverySwitched` block above.
            const adopted = takeAdoptedSource();
            if (adopted
                && adopted.deliverySlug === delivery.slug
                && adopted.surahNum === surahNum
                && dashPort.window?.src === adopted.srcUrl) {
                lastSurahNum = surahNum;
                setIsLoading(false);
                setIsPlaying(true);
                return;
            }
            // Real source switch (not the adopt fast-path above): cancel any
            // in-flight speculative/committed warm so a stale warm element can
            // never adopt onto the new source. Mirrors shuffle's clearShuffle.
            clearDashPrewarm();
            _nearEndWarmedSurah = null;
            // Stop the previous chapter immediately. Without this, the
            // old MP3 keeps playing until _swapTo writes el.src for the
            // new source — audible as a chunk of the wrong reciter when
            // the user changes combination mid-playback. setIsPlaying(false)
            // flips the glyph to ▶ for the duration of the load (re-flips
            // to ⏸ when `playing` fires on the new source if wasPlaying or
            // isActiveCombinationSwitch).
            if (wasPlaying || isActiveCombinationSwitch) {
                dashPort.pause();
                setIsPlaying(false);
            }
            const entry = urls[String(surahNum)];
            if (entry) {
                const url = entry.url;
                // Per-URL probe (host CORS+Range AND native-seek header check —
                // see play-url.ts) so a Xing-tagged chapter stays proxied.
                await probeDirectPlayable(url);
                const cbrSrc = dashProxyUrl(delivery.slug, url);
                dashPort.setSource({
                    audioUrl: url,
                    cbrSrc,
                    reciter: delivery.slug,
                    vbr: vbrChapters.has(surahNum),
                });
                // Seed duration from the manifest so the progress bar shows
                // total length before <audio> fetches MP3 headers (which
                // doesn't happen until play with preload="none").
                if (entry.durationMs && entry.durationMs > 0) {
                    setDuration(entry.durationMs);
                }
                if (wasPlaying || isActiveCombinationSwitch) {
                    signalDashSeekIntent();
                    await ensureAudioContextRunning();
                    dashPort.loadCovering(...coveringRangeFor(0));
                    dashPort.play();
                } else {
                    // Paused chapter-select: warm the decoder + canplay off the
                    // play-click critical path so first-play isn't a 1-3s cold
                    // start (fetch + header parse + decode). No-op for VBR;
                    // dashboard sources are always CBR. Fire-and-forget.
                    void dashPort.prewarm();
                }
            }
            lastSurahNum = surahNum;
            // Persist the settled reciter+surah so a refresh resumes here. The
            // `deliverySwitched` block above persists with the *incoming* surah,
            // which may be corrected to the first available chapter; this final
            // write captures the surah actually loaded (and surah-only changes).
            persistSlice({ deliverySlug: delivery.slug, surahNum, speed: ctx.speed });
        }
    }

    // ---------------------------------------------------------------------
    // Intent-driven prewarm (WS6). Depth 1, single 'dash' shadow slot.
    // Speculative (hover) = range-windowed warm; committed (near-end) = the
    // imminent gapless-next chapter, adopted on `ended`. Cancel-on-switch
    // lives in reactToContext. See lib/playback/dash-prewarm.ts.
    // ---------------------------------------------------------------------
    /** Warm the committed next chapter once the current one is within this much
     *  of its end, so the chapter-end handoff can adopt it gaplessly. */
    const NEAR_END_WARM_MS = 12_000;
    /** Debounce window for the speculative position-warm (progress hover / scrub
     *  settle) so a fast scrub doesn't stack proxy fetches. */
    const POSITION_WARM_DEBOUNCE_MS = 150;

    /** Play URL for a given surah's chapter MP3 (current delivery), or null
     *  when the surah isn't in the loaded set. Probes the chapter first so the
     *  warm src is the same one `reactToContext` will build at play time
     *  (direct vs proxy) — a mismatched warm is a wasted fetch. */
    async function surahProxyUrl(n: number): Promise<string | null> {
        const delivery = $playerContext.delivery;
        const entry = urls[String(n)];
        if (!delivery || !entry) return null;
        await probeDirectPlayable(entry.url);
        return dashProxyUrl(delivery.slug, entry.url);
    }

    function nextSurahNum(): number | null {
        const cur = $playerContext.surahNum;
        if (cur === null) return null;
        const idx = surahNums.indexOf(cur);
        return idx >= 0 && idx < surahNums.length - 1 ? surahNums[idx + 1]! : null;
    }

    function prevSurahNum(): number | null {
        const cur = $playerContext.surahNum;
        if (cur === null) return null;
        const idx = surahNums.indexOf(cur);
        return idx > 0 ? surahNums[idx - 1]! : null;
    }

    /** Speculative: warm a whole surah's start (next/prev button + popover hover). */
    function warmSurah(n: number | null): void {
        if (n === null) return;
        void surahProxyUrl(n).then((proxy) => {
            if (proxy) primeDashSpeculative(proxy, 0);
        });
    }

    /** Speculative: warm the CURRENT chapter at a hovered/scrubbed file-ms
     *  position (debounced). No-op once the chapter is already loaded — the
     *  shadow dedupe + warm only helps the cold first seek. */
    function warmAtPositionDebounced(fileMs: number | null): void {
        if (fileMs === null || !Number.isFinite(fileMs)) return;
        const cur = $playerContext.surahNum;
        if (cur === null) return;
        if (_warmDebounce) clearTimeout(_warmDebounce);
        _warmDebounce = setTimeout(() => {
            void surahProxyUrl(cur).then((proxy) => {
                if (proxy) primeDashSpeculative(proxy, Math.max(0, fileMs / 1000));
            });
        }, POSITION_WARM_DEBOUNCE_MS);
    }

    /** Near-end: commit-warm the next chapter so `ended` can adopt it gaplessly.
     *  Dashboard tab only (Timestamps' shuffle owns end-of-chapter). Skips while
     *  looping (the chapter repeats) and after the next chapter is already warm. */
    function maybeWarmNext(fileMs: number, durationMs: number): void {
        if (getActiveTab() !== TAB_NAMES.DASHBOARD) return;
        if ($loopTarget || durationMs <= 0) return;
        const cur = $playerContext.surahNum;
        if (cur === null || _nearEndWarmedSurah === cur) return;
        if (fileMs < durationMs - NEAR_END_WARM_MS) return;
        const nextN = nextSurahNum();
        const delivery = $playerContext.delivery;
        if (nextN === null || !delivery) return;
        const entry = urls[String(nextN)];
        if (!entry) return;
        _nearEndWarmedSurah = cur;
        void probeDirectPlayable(entry.url).then(() => {
            primeDashCommitted({
                deliverySlug: delivery.slug,
                surahNum: nextN,
                rawUrl: entry.url,
                proxyUrl: dashProxyUrl(delivery.slug, entry.url),
            });
        });
    }

    /** Chapter-end handoff (Dashboard tab only). If the next chapter is warm,
     *  adopt its element onto dashPort and start it gaplessly, then advance
     *  playerContext (guarded by the adopt signal so reactToContext does
     *  bookkeeping only). Else fall back to a plain (gapped) auto-advance. */
    function advanceGaplessOnEnd(): void {
        if (getActiveTab() !== TAB_NAMES.DASHBOARD) return;
        if ($loopTarget) return;
        const delivery = $playerContext.delivery;
        const nextN = nextSurahNum();
        _nearEndWarmedSurah = null;
        if (!delivery || nextN === null) return;
        const entry = urls[String(nextN)];
        if (!entry) return;
        const proxyUrl = dashProxyUrl(delivery.slug, entry.url);

        const consumed = consumeDashCommitted(proxyUrl);
        if (consumed && consumed.surahNum === nextN) {
            // Match the triple reactToContext would build so its eventual
            // setSource is a guaranteed no-op alongside the adopt signal.
            dashPort.setSource({
                audioUrl: consumed.rawUrl, cbrSrc: consumed.proxyUrl,
                reciter: delivery.slug, vbr: vbrChapters.has(nextN),
            });
            setAdoptedSource({ deliverySlug: delivery.slug, surahNum: nextN, srcUrl: proxyUrl });
            const oldEl = dashPort.element;
            dashPort.adoptElement(consumed.el, proxyUrl);
            if (oldEl && oldEl !== consumed.el) recycleAsShadow(oldEl, 'any');
            dashPort.seekAndPlay(0);
            setIsLoading(false);
            setIsPlaying(true);
            playerContext.update((s) => ({
                ...s, surahNum: nextN, positionMs: 0, isPlaying: true,
            }));
            return;
        }
        // Look-ahead miss → plain (gapped) advance.
        setSurahAndResume(nextN);
    }

    async function togglePlay(): Promise<void> {
        if ($playerContext.isPlaying) {
            dashPort.pause();
            return;
        }
        await resumePlayback();
    }

    async function resumePlayback(): Promise<void> {
        await ensureAudioContextRunning();
        if (dashPort.source) {
            dashPort.loadCovering(...coveringRangeFor(dashPort.currentTimeMs()));
        }
        // Debounced raise — no-ops when the element is already buffered + audible.
        signalDashSeekIntent();
        dashPort.play();
    }

    async function seekAndResume(targetMs: number): Promise<void> {
        await ensureAudioContextRunning();
        if (dashPort.source) {
            dashPort.loadCovering(...coveringRangeFor(targetMs));
        }
        dashPort.seek(targetMs);
        // Debounced raise — no-ops when the seek lands inside the buffered window.
        signalDashSeekIntent();
        dashPort.play();
    }

    function coveringRangeFor(targetMs: number): [number, number] {
        if (!dashPort.source?.vbr) return [0, Number.POSITIVE_INFINITY];
        return vbrCoveringRangeFor(targetMs, $recitationAyahs);
    }

    function setSurahAndResume(surahNum: number): void {
        playerContext.update((s) => ({
            ...s,
            surahNum,
            positionMs: 0,
            isPlaying: true,
        }));
    }

    // Index (into the ascending ayah-start list) of the verse actually being
    // RECITED at `ms`, via the recitation locator — covers re-takes whose audio
    // plays past a later verse's canonical start. -1 when the resolver isn't
    // published (no chapter loaded) or the playhead is in a real silence gap, so
    // the caller falls back to start-ordering inference.
    function recitedAyahIndex(ms: number): number {
        const resolve = $recitationAyahAt;
        if (!resolve) return -1;
        const key = resolve(ms);
        if (!key) return -1;
        return $recitationAyahs.findIndex((a) => a.ayahKey === key);
    }

    // Whenever ayah boundaries are loaded for whatever's selected, the seek
    // buttons jump ayah-by-ayah (prev/next ayah start; back restarts the current
    // ayah if >1.5s in) — no bucket/condition gate. With no boundaries (e.g. a
    // non-timestamped reciter) they fall back to the ±15s nudge.
    function ayahSeekTarget(cur: number, dir: 1 | -1): number | null {
        if (!$recitationAyahStarts.length) return null;
        // Prefer the recitation-resolved current verse (correct inside a re-take);
        // fall back to position-from-start ordering in a silence gap.
        const ci = recitedAyahIndex(cur);
        return ci >= 0
            ? adjacentAyahStartFromIndex($recitationAyahStarts, ci, cur, dir)
            : adjacentAyahStartMs($recitationAyahStarts, cur, dir);
    }
    function seekBack(): void {
        exitLoop(); // any deliberate seek drops loop mode
        const cur = dashPort.currentTimeMs();
        const t = ayahSeekTarget(cur, -1);
        if (t !== null) { void seekAndResume(t); return; }
        void seekAndResume(Math.max(0, cur - 15_000));
    }
    function seekForward(): void {
        exitLoop();
        const cur = dashPort.currentTimeMs();
        const t = ayahSeekTarget(cur, 1);
        if (t !== null) { void seekAndResume(t); return; }
        void seekAndResume(cur + 15_000);
    }

    function prevSurah(): void {
        const ctx = $playerContext;
        if (ctx.surahNum === null) return;
        const idx = surahNums.indexOf(ctx.surahNum);
        if (idx > 0) { exitLoop(); setSurahAndResume(surahNums[idx - 1]!); }
    }
    function nextSurah(): void {
        const ctx = $playerContext;
        if (ctx.surahNum === null) return;
        const idx = surahNums.indexOf(ctx.surahNum);
        if (idx >= 0 && idx < surahNums.length - 1) { exitLoop(); setSurahAndResume(surahNums[idx + 1]!); }
    }

    function onSurahChange(n: number): void {
        setSurahAndResume(n);
        surahPopoverOpen = false;
    }

    function onSpeedChange(rate: number): void {
        setSpeed(rate);
        dashPort.setPlaybackRate(rate);
        persistSlice({
            deliverySlug: lastDeliverySlug,
            surahNum: lastSurahNum,
            speed: rate,
        });
    }

    function cycleSpeed(): void {
        const cur = $playerContext.speed;
        const idx = DASHBOARD_SPEEDS.findIndex((s) => Math.abs(s - cur) < 0.01);
        const next = DASHBOARD_SPEEDS[(idx + 1) % DASHBOARD_SPEEDS.length] ?? 1;
        onSpeedChange(next);
    }

    /**
     * Download the current chapter, delegating entirely to the browser's
     * download manager — a transient <a download> click, no fetch/blob.
     * Targets the real CDN file (entry.originUrl when routed via QF, else
     * entry.url) through the same-origin audio-proxy with `download=1`, which
     * adds `Content-Disposition: attachment` so the response saves instead of
     * streaming inline.
     */
    function downloadSurah(): void {
        const ctx = $playerContext;
        const delivery = ctx.delivery;
        const surahNum = ctx.surahNum;
        if (!delivery || surahNum === null) return;
        const entry = urls[String(surahNum)];
        if (!entry) return;

        const cdnUrl = entry.url;
        const filename = `${delivery.slug}-${String(surahNum).padStart(3, '0')}.mp3`;
        const href = cdnUrl.startsWith('/api/')
            ? cdnUrl
            : `/api/seg/audio-proxy/${delivery.slug}`
              + `?url=${encodeURIComponent(cdnUrl)}&download=1&chapter=${surahNum}`;

        const a = document.createElement('a');
        a.href = href;
        a.download = filename;
        a.rel = 'noopener';
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    function onSeekFromBar(ev: CustomEvent<number>): void {
        exitLoop(); // dragging the progress bar is a deliberate seek → drop loop
        // Snap the release to the nearest ayah start (both tabs — the bar is the
        // shared shell player). Falls back to the raw target for non-timestamped
        // playback, where no ayah boundaries exist.
        let target = ev.detail;
        if ($recitationAyahStarts.length) {
            const snapped = nearestAyahStartMs($recitationAyahStarts, target);
            if (snapped !== null) target = snapped;
        }
        void seekAndResume(target);
    }

    function onCombinationSelect(ev: CustomEvent<PublicDelivery>): void {
        const d = ev.detail;
        playerContext.update((s) => ({
            ...s,
            delivery: d,
            positionMs: 0,
            isPlaying: true,
        }));
    }

    // `by_ayah` sidecars key chapters as `"<surah>:<ayah>"`, so `Number("1:1")`
    // → NaN. Filter to finite ints so the popover / prev / next controls never
    // surface "Surah NaN" even if a stray by_ayah delivery slips through.
    $: surahNums = Object.keys(urls).map(Number).filter(Number.isFinite).sort((a, b) => a - b);

    let _surahMap: ReturnType<typeof getSurahInfo> = {};
    void surahInfoReady.then(() => { _surahMap = getSurahInfo(); });
    $: activeSurahName = ((): string | null => {
        const info = _surahMap[String($playerContext.surahNum)];
        if (!info) return null;
        return lang === 'ar' && info.name_ar ? info.name_ar.replace(/^سُورَةُ\s*/, '') : info.name_en;
    })();

    $: canPrev = $playerContext.surahNum !== null && surahNums.indexOf($playerContext.surahNum) > 0;
    $: canNext = $playerContext.surahNum !== null
        && surahNums.indexOf($playerContext.surahNum) >= 0
        && surahNums.indexOf($playerContext.surahNum) < surahNums.length - 1;
    $: canDownload = $playerContext.surahNum !== null
        && !!urls[String($playerContext.surahNum)];

    $: lang = $localeStore;
    $: pickSurahLabel = tr(lang, m.common_player_pick_surah());
    $: surahFallbackLabel = tr(
        lang,
        localizeDigits(m.common_player_surah_fallback({ num: $playerContext.surahNum ?? 0 })),
    );
    $: surahTriggerLabel = $playerContext.surahNum
        ? (activeSurahName ?? surahFallbackLabel)
        : pickSurahLabel;
    $: speedTitle = tr(lang, m.common_player_speed_title());
    // Localized digits; `×` kept trailing the number as a stable LTR token
    // (a numeric HUD chip, not flowing prose), so RTL never reorders it.
    $: speedLabel = tr(lang, localizeDigits(`${$playerContext.speed}×`));
    $: downloadLabel = tr(lang, m.common_player_download_label());
</script>

<div
    class="player"
    class:has-reciter={$playerContext.reciter !== null}
    style={accentVarText($recitationConfigStore.highlightColor, $theme$)}
>
    <PlayerProgress
        positionMs={$playerContext.positionMs}
        durationMs={$playerContext.durationMs}
        on:seek={onSeekFromBar}
    />

    <div class="row">
        <!-- Left zone: tab-specific reciter picker (meta) pinned to the far
             left; the surah picker is pushed to the inner edge so it sits just
             left of the transport. Default meta = dashboard combination chip;
             the Timestamps tab fills it with a published-only picker + shuffle. -->
        <div class="zone zone-left">
            <slot name="meta">
                <PlayerMetaChip
                    reciter={$playerContext.reciter}
                    delivery={$playerContext.delivery}
                    on:select={onCombinationSelect}
                />
            </slot>

            <!-- Inner-edge group: a tab-specific lead affordance (Timestamps
                 fills it with the Report button) sits directly left of the
                 surah picker, both pinned to the transport edge. -->
            <div class="loc-group">
                <slot name="loc-lead" />

                <div class="surah-trigger-wrap" use:clickOutside={() => (surahPopoverOpen = false)}>
                    <SurahPicker
                        surahNums={surahNums}
                        value={$playerContext.surahNum}
                        label={surahTriggerLabel}
                        hasValue={!!$playerContext.surahNum}
                        disabled={surahNums.length === 0}
                        open={surahPopoverOpen}
                        ontoggle={() => (surahPopoverOpen = !surahPopoverOpen)}
                        onchange={onSurahChange}
                        onhover={warmSurah}
                    />
                </div>
            </div>
        </div>

        <!-- Center: the transport ONLY, so the play button lands on the true
             viewport center — aligned with the now-reciting collapse chip
             (both columns flanking it are equal `1fr`). Shared by both tabs. -->
        <div class="controls">
            <PlayerControls
                isPlaying={$playerContext.isPlaying}
                isLoading={$playerContext.isLoading}
                canPlay={$playerContext.delivery !== null && $playerContext.surahNum !== null}
                canStepBack={canPrev}
                canStepForward={canNext}
                on:toggle={togglePlay}
                on:seekBack={seekBack}
                on:seekForward={seekForward}
                on:prev={prevSurah}
                on:next={nextSurah}
                on:prevHover={() => warmSurah(prevSurahNum())}
                on:nextHover={() => warmSurah(nextSurahNum())}
            />
        </div>

        <!-- Right zone: speed + tab-specific analysis at the inner edge (just
             right of the transport); download pinned to the far right. -->
        <div class="zone zone-right">
            <div class="right-inner">
                <button
                    type="button"
                    class="speed-btn"
                    dir="ltr"
                    on:click={cycleSpeed}
                    title={speedTitle}
                >{speedLabel}</button>

                <!-- Highlight accent picker (the droplet), right of speed. -->
                <HighlightColorPicker />

                <!-- Tab-specific cluster (Timestamps: analysis row). -->
                <slot name="center-trail"></slot>
            </div>

            <!-- Far-right cluster: a tab-specific lead affordance (Timestamps
                 fills it with the "open in Segments" redirect) sits directly
                 left of the always-present download button. -->
            <div class="right-edge">
                <slot name="download-lead" />

                <button
                    type="button"
                    class="download-btn"
                    on:click={downloadSurah}
                    disabled={!canDownload}
                    aria-label={downloadLabel}
                    title={downloadLabel}
                >
                    <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        aria-hidden="true"
                    >
                        <path d="M12 3v12" />
                        <path d="m7 10 5 5 5-5" />
                        <path d="M5 21h14" />
                    </svg>
                </button>
            </div>
        </div>
    </div>

    <audio bind:this={audioEl} preload="none"></audio>
</div>

<style>
    .player {
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
        background: var(--panel);
        border-top: 1px solid var(--border-default);
        padding: 0 var(--s-4) var(--s-2);
        z-index: 110;
        display: flex;
        flex-direction: column;
        box-shadow: 0 -8px 24px oklch(0 0 0 / 0.25);
    }
    .row {
        display: grid;
        grid-template-columns: minmax(220px, 1fr) auto minmax(220px, 1fr);
        align-items: center;
        gap: var(--s-4);
        height: calc(var(--player-h, 92px) - 14px);
    }
    /* The two side zones are equal `1fr` columns flanking the `auto` transport
       column, so the play button (center of the symmetric transport) sits on
       the viewport center — aligned with the now-reciting collapse chip.
       `space-between` pushes the inner items (surah / speed+analysis) up against
       the transport and pins meta / download to the outer edges. */
    .zone {
        display: flex;
        align-items: center;
        gap: var(--s-4);
        min-width: 0;
        justify-content: space-between;
    }
    .right-inner {
        display: flex;
        align-items: center;
        gap: var(--s-4);
        min-width: 0;
    }
    /* Download + its optional tab-specific lead, kept together at the outer
       edge while `right-inner` stays pinned to the transport via space-between. */
    .right-edge {
        display: flex;
        align-items: center;
        gap: var(--s-2);
        flex: 0 0 auto;
    }
    .controls {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: var(--s-3);
    }
    /* Lead affordance + surah picker, kept together at the inner (transport)
       edge while `meta` stays pinned far-left via the zone's space-between. */
    .loc-group {
        display: flex;
        align-items: stretch;
        gap: var(--s-2);
        min-width: 0;
    }
    .surah-trigger-wrap { position: relative; }
    .speed-btn {
        box-sizing: border-box;
        padding: 4px var(--s-2);
        font-family: var(--font-mono);
        font-size: 11px;
        color: var(--text-secondary);
        background: transparent;
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        cursor: pointer;
        transition: border-color var(--t-fast), color var(--t-fast);
        /* Static width sized to the widest label ("1.25×" = 5 mono chars) so
           cycling speeds never reflows the analysis cluster beside it. */
        min-width: calc(5ch + var(--s-2) * 2 + 2px);
        text-align: center;
    }
    .speed-btn:hover {
        border-color: var(--border-strong);
        color: var(--text-primary);
    }
    .download-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        color: var(--text-secondary);
        background: transparent;
        border: 0;
        border-radius: 50%;
        cursor: pointer;
        transition: color var(--t-fast), background var(--t-fast);
    }
    .download-btn:hover:not(:disabled) {
        color: var(--text-primary);
        background: var(--panel-2);
    }
    .download-btn:disabled {
        opacity: 0.35;
        cursor: not-allowed;
    }
    /* state-pill-btn removed: the delivery bucket is rendered inline
     * inside <ReciterChip>'s bottom row now, so keeping a second pill
     * on the right was duplication. The chip already opens the
     * combination picker on click — losing the bucket button doesn't
     * cost a navigation affordance. */
    audio { display: none; }
</style>
