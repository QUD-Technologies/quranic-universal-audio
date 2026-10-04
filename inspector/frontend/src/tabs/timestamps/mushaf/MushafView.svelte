<script lang="ts">
    /**
     * Timestamps Mushaf view — the recitation read along in a two-page
     * Madani mushaf (one page when the window is too narrow for two).
     *
     * Follows playback: the recited word lights up, the current verse gets a
     * faint band, and the book turns when the recitation reaches the next
     * spread. Paging by hand stops following until "Back to reciting" or any
     * seek. Clicking a word seeks to it; a verse marker seeks to its verse.
     * Pages of surahs the reciter never recited are skipped and inert.
     *
     * A chapter switch turns straight to its page from the layout alone and
     * holds the audio until the chapter's timings are in, so playback never
     * runs ahead of the page it's read along on.
     */
    import { onMount, tick, untrack } from 'svelte';
    import { get } from 'svelte/store';

    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';
    import { ensureDashCovering } from '../../../lib/playback/dash-covering';
    import { signalDashSeekIntent } from '../../../lib/playback/dash-buffering';
    import { dashPort } from '../../../lib/playback/dash-port';
    import { exitLoop } from '../../../lib/playback/loop';
    import { loadChapterRecitation } from '../../../lib/recitation-data/load-chapter';
    import { loadManifest } from '../../../lib/recitation-data/ts-source';
    import { recitationConfigStore } from '../../../lib/recitation-animation/recitation-settings';
    import { playerContext } from '../../../lib/stores/player-context';
    import { themeStore, THEME_CHANGE_EVENT } from '../../../lib/stores/theme.svelte';
    import { accentVarText } from '../../../lib/utils/accent-override';
    import { activeTab } from '../../../lib/utils/active-tab';
    import { TAB_NAMES } from '../../../lib/utils/constants';
    import { shouldHandleKey } from '../../../lib/utils/keyboard-guard';
    import { mushafScope, mushafShowUpcoming, mushafYear } from '../stores/mushaf';
    import { fitPages, spreadFontPx, type PageMetrics } from './fit';
    import {
        ensureMushafFont,
        SURAH_FRAME_FAMILY,
        SURAH_NAME_FAMILY,
        TEXT_FALLBACK_FAMILY,
        textFamily,
        textFontStack,
    } from './fonts';
    import { PageHighlighter } from './highlight';
    import {
        firstWordOfVerse,
        isVerseMarker,
        loadLayout,
        loadWordIndex,
        pageOfWord,
        PAGE_COUNT,
        surahsOnPage,
        type MushafLayout,
        type WordIndex,
    } from './layout';
    import { LineMeasurer } from './measure';
    import MushafBook, { type Leaf } from './MushafBook.svelte';
    import { indexChapter, positionAt, type ChapterIndex } from './position';
    import { mushafFullscreen } from './fullscreen.svelte';
    import { mushafRepeat } from './repeat.svelte';
    import { compareRefs, type VerseRef } from './repeat-plan';

    const DEFAULT_PLAYER_H = 72;
    /** Room kept under the book for the docked-footer handle in full screen. */
    const DOCK_HANDLE_PX = 22;
    const PLAYER_SELECTOR = '.player';
    /** Page-turn duration (keep in step with `.mv-leaf` in MushafBook). */
    const LEAF_MS = 620;
    const LEAF_SLACK_MS = 80;
    const reducedMotion = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

    let root: HTMLDivElement | undefined = $state();
    let stage: HTMLDivElement | undefined = $state();

    // ---- data ----
    let layout = $state.raw<MushafLayout | null>(null);
    let words = $state.raw<WordIndex | null>(null);
    let failed = $state(false);
    let tsChapters = $state.raw(new Set<number>());
    let chapterIx = $state.raw<ChapterIndex | null>(null);
    /** "slug:chapter" `chapterIx` was built for. */
    let ixKey = $state('');
    /** "slug:chapter" whose timings are being fetched, '' when none. */
    let loadingKey = '';
    /** Text face the pages are set in: the year's own, or the fallback when it won't load. */
    let textFace = $state(TEXT_FALLBACK_FAMILY);

    const slug = $derived($playerContext.delivery?.slug ?? '');
    const chapter = $derived($playerContext.surahNum ?? 0);
    const live = $derived(chapterIx !== null && ixKey === `${slug}:${chapter}`);
    const fontStack = $derived(textFontStack(textFace));

    // Layout, its text face and the line measurements land together, so a
    // year switch never renders the new layout against the old sizing.
    $effect(() => {
        const year = $mushafYear;
        const w = words;
        if (!w) return;
        let cancelled = false;
        void Promise.all([loadLayout(year), ensureMushafFont(textFamily(year))])
            .then(([l, ok]) => {
                if (cancelled) return;
                const face = ok ? textFamily(year) : TEXT_FALLBACK_FAMILY;
                measurer = new LineMeasurer(`"${face}"`);
                const em = measurer.referenceEm(l, w);
                fontCache.clear();
                textFace = face;
                refEm = em;
                layout = l;
            })
            .catch((e: unknown) => {
                console.error('Mushaf: layout load failed', e);
                failed = true;
            });
        return () => { cancelled = true; };
    });

    $effect(() => {
        const s = slug;
        mushafRepeat.stop(); // a new reciter invalidates the resolved takes
        if (!s) return;
        void loadManifest()
            .then((mf) => { tsChapters = new Set(mf.reciters?.[s]?.ts_chapters ?? []); })
            .catch((e: unknown) => console.error('Mushaf: manifest load failed', e));
    });

    $effect(() => {
        const s = slug;
        const ch = chapter;
        const w = words;
        if (!s || !ch || !w) return;
        const key = `${s}:${ch}`;
        const ctrl = new AbortController();
        loadingKey = key;
        holdAudio();
        void loadChapterRecitation(s, ch, ctrl.signal)
            .then((data) => {
                if (ctrl.signal.aborted) return;
                chapterIx = data ? indexChapter(ch, data.units, w) : null;
                ixKey = key;
                lastUnit = -1;
                following = true;
            })
            .catch((e: unknown) => console.error('Mushaf: chapter load failed', e))
            .finally(() => {
                if (ctrl.signal.aborted) return;
                loadingKey = '';
                releaseAudio();
            });
        return () => ctrl.abort();
    });

    // ---- audio hold across a chapter switch ----
    /** Playback we paused while the chapter's timings load; resumed after. */
    let held = false;

    function holdAudio(): void {
        // Only a switch holds: opening the view on a playing chapter just follows it.
        if (!loadingKey || !ixKey || mushafRepeat.running || dashPort.paused) return;
        if (get(activeTab) !== TAB_NAMES.TIMESTAMPS) return;
        dashPort.pause();
        held = true;
    }

    function releaseAudio(): void {
        if (pendingSeek) {
            held = false;
            applyPendingSeek();
            return;
        }
        if (!held) return;
        held = false;
        dashPort.play();
    }

    // Turn to a newly chosen chapter at once, from the layout alone.
    let shownChapter = 0;
    $effect(() => {
        const ch = chapter;
        const l = layout;
        const w = words;
        if (!ch || !l || !w || !metrics) return;
        untrack(() => {
            if (ch === shownChapter) return;
            const first = shownChapter === 0;
            shownChapter = ch;
            if (live) return; // already reading along in it
            following = true;
            openView(viewOfPage(pageOfWord(l, landingWord(ch, w))), !first);
        });
    });

    /** The word a chapter switch lands on: the pending seek's, else the first. */
    function landingWord(ch: number, w: WordIndex): number {
        const t = pendingSeek?.chapter === ch ? pendingSeek.target : null;
        if (t && 'loc' in t) return w.idOfLoc.get(t.loc) ?? firstWordOfVerse(w, ch, 1);
        if (t) {
            const [s, a] = t.verse.split(':').map(Number);
            return firstWordOfVerse(w, s ?? ch, a ?? 1);
        }
        return firstWordOfVerse(w, ch, 1);
    }

    // ---- sizing ----
    let vp = $state({ width: 0, height: 0 });
    let refEm = $state(0);
    let measurer: LineMeasurer | null = null;

    const metrics = $derived<PageMetrics | null>(refEm > 0 && vp.width > 0 ? fitPages(vp, refEm) : null);
    const spread = $derived(metrics?.spread ?? true);
    const fontCache = new Map<string, number>();

    function pagesOfView(v: number): number[] {
        return spread ? [2 * v + 1, 2 * v + 2] : [v + 1];
    }
    function viewOfPage(p: number): number {
        return spread ? Math.floor((p - 1) / 2) : p - 1;
    }
    const viewCount = $derived(spread ? PAGE_COUNT / 2 : PAGE_COUNT);

    function fontOf(page: number): number {
        if (!metrics || !layout || !words || !measurer) return 16;
        const pages = pagesOfView(viewOfPage(page));
        const key = `${metrics.fontPx}|${pages.join(',')}`;
        let f = fontCache.get(key);
        if (f === undefined) {
            f = spreadFontPx(metrics, measurer.maxEm(layout, words, pages));
            fontCache.set(key, f);
        }
        return f;
    }

    function measureViewport(): void {
        if (!root) return;
        // The shell player's real top edge — `--player-h` omits its progress rail.
        const player = document.querySelector<HTMLElement>(PLAYER_SELECTOR);
        const playerH = parseFloat(getComputedStyle(root).getPropertyValue('--player-h')) || DEFAULT_PLAYER_H;
        // Docked in full screen, the footer is off-screen or overlaid: the book
        // keeps the whole height bar the handle's strip.
        const bottom = mushafFullscreen.docked
            ? window.innerHeight - DOCK_HANDLE_PX
            : player ? player.getBoundingClientRect().top : window.innerHeight - playerH;
        const top = root.getBoundingClientRect().top;
        vp = { width: root.clientWidth, height: Math.max(0, bottom - top) };
    }

    // ---- which pages are open ----
    /** First (right) page of the open view. */
    let anchorPage = $state(1);
    let following = $state(true);
    let leaf = $state<Leaf | null>(null);
    let leafTimer: ReturnType<typeof setTimeout> | null = null;
    let slotPages = $state<number[]>([1, 2]);

    const view = $derived(viewOfPage(anchorPage));

    function playableView(v: number): boolean {
        if (!layout || !words) return false;
        return pagesOfView(v).some((p) => surahsOnPage(layout!, words!, p).some((s) => tsChapters.has(s)));
    }

    /** Open view `v`, turning the page unless `animate` is off. */
    function openView(v: number, animate = true): void {
        const target = Math.max(0, Math.min(viewCount - 1, v));
        const from = view;
        anchorPage = pagesOfView(target)[0]!;
        if (target === from) {
            slotPages = pagesOfView(target);
            return;
        }
        const dir: 1 | -1 = target > from ? 1 : -1;
        const a = pagesOfView(from);
        const b = pagesOfView(target);
        if (reducedMotion || !metrics || !animate) {
            leaf = null;
            slotPages = b;
        } else if (spread) {
            // Right page first: forward keeps the old right page under the
            // turning left one; back keeps the old left page.
            slotPages = dir === 1 ? [a[0]!, b[1]!] : [b[0]!, a[1]!];
            leaf = dir === 1 ? { front: a[1]!, back: b[0]!, dir } : { front: a[0]!, back: b[1]!, dir };
        } else {
            slotPages = b;
            leaf = { front: a[0]!, back: 0, dir };
        }
        // `animationend` never fires in a hidden tab (animations pause), and a
        // stuck leaf would freeze following — settle on a timer as well.
        if (leafTimer) clearTimeout(leafTimer);
        if (leaf) leafTimer = setTimeout(onLeafEnd, LEAF_MS + LEAF_SLACK_MS);
    }

    function onLeafEnd(): void {
        if (leafTimer) clearTimeout(leafTimer);
        leafTimer = null;
        leaf = null;
        slotPages = pagesOfView(view);
    }

    // Spread ↔ single switch (resize) keeps the anchor page.
    $effect(() => {
        void spread;
        untrack(() => {
            leaf = null;
            slotPages = pagesOfView(viewOfPage(anchorPage));
        });
    });

    function navigate(delta: 1 | -1): void {
        let v = view + delta;
        while (v >= 0 && v < viewCount && !playableView(v)) v += delta;
        if (v < 0 || v >= viewCount) return;
        // Paging back onto the recited page is the same as "Back to reciting".
        following = !!layout && lastAnchorId > 0 && v === viewOfPage(pageOfWord(layout, lastAnchorId));
        openView(v);
    }

    // ---- highlight + follow (per frame) ----
    const highlighter = new PageHighlighter();
    let lastUnit = -1;
    let lastMs = 0;
    /** Word the recitation is at (or last was), for "am I on the recited page?". */
    let lastAnchorId = 0;
    let raf: number | null = null;
    /** A playhead jump larger than this between frames is a seek → follow again. */
    const SEEK_JUMP_MS = 1500;

    $effect(() => {
        void slotPages; void metrics; void layout; void $mushafScope; void $mushafShowUpcoming;
        const r = root;
        if (!r) return;
        void tick().then(() => highlighter.rebuild(r));
    });

    function frame(): void {
        mushafRepeat.tick();
        if (loadingKey && !dashPort.paused) holdAudio(); // the player autoplayed the new chapter
        const ix = chapterIx;
        const l = layout;
        const w = words;
        if (!ix || !l || !w || !live) return;
        const ms = dashPort.currentTimeMs();
        if (Math.abs(ms - lastMs) > SEEK_JUMP_MS) following = true;
        lastMs = ms;
        const { pos, unit } = positionAt(ix, ms / 1000, lastUnit);
        lastUnit = unit;
        lastAnchorId = pos.anchorId;
        // Picking a verse needs every verse visible and clickable.
        const picking = mushafRepeat.picking !== null;
        highlighter.apply(
            {
                ...pos,
                scope: picking ? 'spread' : get(mushafScope),
                showUpcoming: picking || get(mushafShowUpcoming),
            },
            w,
        );
        if (following && pos.anchorId && !leaf) {
            const v = viewOfPage(pageOfWord(l, pos.anchorId));
            // Reading on turns one leaf; a jump (seek, first load) cuts straight there.
            if (v !== view) openView(v, Math.abs(v - view) === 1);
        }
    }

    function loop(): void {
        frame();
        raf = requestAnimationFrame(loop);
    }

    $effect(() => {
        if ($activeTab !== TAB_NAMES.TIMESTAMPS) return;
        raf = requestAnimationFrame(loop);
        return () => {
            if (raf !== null) cancelAnimationFrame(raf);
            raf = null;
        };
    });

    // ---- seeking ----
    type SeekTarget = { loc: string } | { verse: string };
    let pendingSeek: { chapter: number; target: SeekTarget } | null = null;

    function targetMs(ix: ChapterIndex, t: SeekTarget): number | null {
        if ('loc' in t) {
            const u = ix.unitOfLoc.get(t.loc);
            if (u !== undefined) return Math.round((ix.units[u]!.intervals[0]?.start ?? 0) * 1000);
            t = { verse: t.loc.split(':').slice(0, 2).join(':') };
        }
        return ix.verseStartMs.get(t.verse) ?? null;
    }

    function seekTo(ms: number): void {
        ensureDashCovering(ms);
        dashPort.uncut();
        dashPort.seekAndPlay(ms);
        signalDashSeekIntent();
        following = true;
    }

    function applyPendingSeek(): void {
        const ix = chapterIx;
        if (!pendingSeek || !ix || pendingSeek.chapter !== ix.chapter) return;
        const ms = targetMs(ix, pendingSeek.target);
        pendingSeek = null;
        if (ms !== null) seekTo(ms);
    }

    function switchChapter(surah: number, play: boolean): void {
        playerContext.update((s) => ({ ...s, surahNum: surah, positionMs: 0, isPlaying: play }));
    }

    function onPageClick(e: MouseEvent): void {
        const el = (e.target as HTMLElement).closest<HTMLElement>('.mv-slot [data-w]');
        const w = words;
        if (!el || !w) return;
        const id = Number(el.dataset.w);
        const surah = w.surah[id] ?? 0;
        if (!tsChapters.has(surah)) return;
        const ayah = w.ayah[id] ?? 0;
        if (mushafRepeat.picking) {
            pickVerse({ surah, ayah });
            return;
        }
        mushafRepeat.stop();
        exitLoop();
        const target: SeekTarget = isVerseMarker(w.text[id] ?? '') ? { verse: `${surah}:${ayah}` } : { loc: w.loc[id]! };
        following = true;
        if (surah === chapter && chapterIx?.chapter === chapter) {
            const ms = targetMs(chapterIx, target);
            if (ms !== null) seekTo(ms);
        } else {
            pendingSeek = { chapter: surah, target };
            switchChapter(surah, true);
        }
    }

    /** "To" can't come before "From": such a verse isn't pickable as the end. */
    function pickable(v: VerseRef): boolean {
        const from = mushafRepeat.from;
        return mushafRepeat.picking !== 'to' || !from || compareRefs(v, from) >= 0;
    }

    function pickVerse(v: VerseRef): void {
        if (!pickable(v)) return;
        const to = mushafRepeat.to;
        // A new start past the old end leaves just that verse.
        if (mushafRepeat.picking === 'from' && to && compareRefs(v, to) > 0) mushafRepeat.to = v;
        mushafRepeat[mushafRepeat.picking!] = v;
        mushafRepeat.picking = null;
    }

    /** Repeat pick: light up the verse under the pointer, or the range it would make. */
    function onPageHover(e: MouseEvent): void {
        const w = words;
        const end = mushafRepeat.picking;
        if (!w || !end) return;
        const el = (e.target as HTMLElement).closest<HTMLElement>('.mv-slot [data-w]');
        const id = el ? Number(el.dataset.w) : 0;
        const surah = w.surah[id] ?? 0;
        if (!id || !tsChapters.has(surah)) {
            highlighter.spotlight(null, w);
            return;
        }
        const here: VerseRef = { surah, ayah: w.ayah[id] ?? 0 };
        if (!pickable(here)) {
            highlighter.spotlight(null, w);
            return;
        }
        const to = mushafRepeat.to;
        const other = end === 'to'
            ? mushafRepeat.from
            : to && compareRefs(here, to) <= 0 ? to : null;
        highlighter.spotlight([other ?? here, here], w);
    }

    function clearSpotlight(): void {
        if (words) highlighter.spotlight(null, words);
    }

    $effect(() => {
        if (mushafRepeat.picking === null) untrack(clearSpotlight);
    });

    // Word clicks are delegated from the stage. The words themselves aren't tab
    // stops (a page holds ~150); keyboard users page with ←/→ and type verses
    // into Repeat instead.
    $effect(() => {
        const el = stage;
        if (!el) return;
        el.addEventListener('click', onPageClick);
        el.addEventListener('mouseover', onPageHover);
        el.addEventListener('mouseleave', clearSpotlight);
        return () => {
            el.removeEventListener('click', onPageClick);
            el.removeEventListener('mouseover', onPageHover);
            el.removeEventListener('mouseleave', clearSpotlight);
        };
    });

    function onKeydown(e: KeyboardEvent): void {
        if (!shouldHandleKey(e, TAB_NAMES.TIMESTAMPS)) return;
        // F11 goes through the same full screen as the in-app button; out of an
        // OS full screen it stays the browser's (the resize then leaves the mode).
        if (e.code === 'F11' && mushafFullscreen.on && mushafFullscreen.windowFull()) return;
        if (e.code === 'KeyF' || e.code === 'F11') {
            e.preventDefault();
            mushafFullscreen.toggle();
            return;
        }
        // Esc leaves real full screen by itself; this covers the chrome-less fallback.
        if (e.code === 'Escape' && mushafFullscreen.on) {
            mushafFullscreen.exit();
            return;
        }
        if (e.code !== 'ArrowLeft' && e.code !== 'ArrowRight') return;
        e.preventDefault();
        navigate(e.code === 'ArrowLeft' ? 1 : -1); // the book reads right to left
    }

    // Full screen moves the book's top edge without resizing it — re-measure.
    $effect(() => {
        void mushafFullscreen.on;
        void mushafFullscreen.docked;
        void tick().then(measureViewport);
    });

    // Leaving the tab (or the view) leaves full screen.
    $effect(() => {
        if ($activeTab !== TAB_NAMES.TIMESTAMPS) untrack(() => mushafFullscreen.exit());
    });

    /** Surah-name + header-frame fonts loaded? Pages fall back to plain text / a ruled box. */
    let headerFonts = $state({ name: true, frame: true });

    // ---- theme-aware accent (the footer droplet colours the highlight) ----
    let theme = $state(themeStore.current);
    const accentStyle = $derived(accentVarText($recitationConfigStore.highlightColor, theme));

    onMount(() => {
        void loadWordIndex()
            .then((w) => { words = w; })
            .catch((e: unknown) => {
                console.error('Mushaf: script load failed', e);
                failed = true;
            });
        void ensureMushafFont(SURAH_NAME_FAMILY).then((ok) => { headerFonts = { ...headerFonts, name: ok }; });
        void ensureMushafFont(SURAH_FRAME_FAMILY).then((ok) => { headerFonts = { ...headerFonts, frame: ok }; });

        const detachWindowFull = mushafFullscreen.attachWindow();
        const detachRepeat = mushafRepeat.attach({
            readyChapter: () => (live ? chapter : 0),
            switchChapter: (s) => switchChapter(s, false),
        });
        // Reading on: at the end of a chapter carry into the reciter's next one.
        const offEnded = dashPort.onEnded(() => {
            if (get(activeTab) !== TAB_NAMES.TIMESTAMPS || mushafRepeat.running) return;
            const next = [...tsChapters].filter((c) => c > chapter).sort((a, b) => a - b)[0];
            if (next) switchChapter(next, true);
        });
        const offTime = dashPort.onTimeUpdate(() => mushafRepeat.tick());
        const offPlay = dashPort.onPlay(holdAudio);

        const ro = new ResizeObserver(() => {
            measureViewport();
            highlighter.invalidate();
        });
        if (root) ro.observe(root);
        // The player grows when the footer wraps on a narrow window.
        const player = document.querySelector<HTMLElement>(PLAYER_SELECTOR);
        if (player) ro.observe(player);
        window.addEventListener('resize', measureViewport);
        const onTheme = (): void => { theme = themeStore.current; };
        window.addEventListener(THEME_CHANGE_EVENT, onTheme);
        measureViewport();
        return () => {
            detachWindowFull();
            mushafFullscreen.exit();
            if (leafTimer) clearTimeout(leafTimer);
            detachRepeat();
            offEnded();
            offTime();
            offPlay();
            ro.disconnect();
            window.removeEventListener('resize', measureViewport);
            window.removeEventListener(THEME_CHANGE_EVENT, onTheme);
        };
    });

    /** Re-evaluate a message on locale switch (reads the locale rune). */
    const L = (s: string): string => (i18n.locale, s);
</script>

<svelte:window onkeydown={onKeydown} />

<div
    class="mv"
    class:picking={mushafRepeat.picking !== null}
    bind:this={root}
    style="{accentStyle}; height: {vp.height}px"
>
    {#if failed}
        <p class="mv-status">{L(m.ts_mushaf_load_failed())}</p>
    {:else if !layout || !words || !metrics}
        <p class="mv-status">{L(m.ts_mushaf_loading())}</p>
    {:else}
        <div class="mv-row">
            <button
                type="button" class="mv-nav"
                title={L(m.ts_mushaf_next())} aria-label={L(m.ts_mushaf_next())}
                onclick={() => navigate(1)}
            ><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5" /></svg></button>

            <div class="mv-stage" bind:this={stage}>
                <!-- Keyed by print year: a new layout gets fresh pages, never re-used spans. -->
                {#key layout.year}
                    <MushafBook
                        {layout} {words} {metrics} {fontOf} {fontStack} {headerFonts}
                        playable={tsChapters}
                        pages={slotPages}
                        {leaf}
                        onleafend={onLeafEnd}
                    />
                {/key}
            </div>

            <button
                type="button" class="mv-nav"
                title={L(m.ts_mushaf_prev())} aria-label={L(m.ts_mushaf_prev())}
                onclick={() => navigate(-1)}
            ><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3l5 5-5 5" /></svg></button>
        </div>

        {#if !following}
            <button type="button" class="mv-back" onclick={() => (following = true)}>
                {L(m.ts_mushaf_back_to_reciting())}
            </button>
        {/if}
    {/if}
</div>

{#if mushafFullscreen.docked && !mushafFullscreen.revealed}
    <button
        type="button" class="mv-dock-handle"
        title={L(m.ts_mushaf_show_controls())} aria-label={L(m.ts_mushaf_show_controls())}
        onmouseenter={() => mushafFullscreen.reveal()}
        onfocus={() => mushafFullscreen.reveal()}
        onclick={() => mushafFullscreen.reveal()}
    >
        <span class="mv-dock-pill">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="m6 15 6-6 6 6" />
            </svg>
        </span>
    </button>
{/if}

<style>
    .mv {
        position: relative;
        display: flex;
        align-items: flex-start;
        justify-content: center;
        overflow: hidden;
    }
    /* Page-turn buttons flank the book (fit.ts NAV_SLOT_PX = button + gap). */
    .mv-row { display: flex; align-items: center; gap: 12px; }
    .mv-stage { display: flex; align-items: center; justify-content: center; }
    .mv.picking .mv-stage :global(.mp-w:not(.mp-inert)) { cursor: crosshair; }
    /* Picking a verse: the page dims, the verse (or range) under the pointer lights up. */
    .mv.picking .mv-stage :global(.mp-w) { opacity: 0.45; }
    .mv.picking .mv-stage :global(.mp-w.is-pick) { opacity: 1; color: var(--accent); }
    .mv.picking .mv-stage :global(.mp-band) { visibility: hidden; }

    /* Full screen: only the book and the shell footer remain. */
    :global(html.mushaf-full .container > header) { display: none; }
    :global(html.mushaf-full .container) { padding-top: var(--s-2); }
    /* Docked: the footer slides off the bottom; the handle brings it back over the book. */
    :global(html.mushaf-full .player) { transition: transform 240ms var(--ease-out-quart, ease-out); }
    :global(html.mushaf-dock .player) { transform: translateY(100%); }
    :global(html.mushaf-dock.mushaf-dock-open .player) { transform: none; box-shadow: var(--shadow-pop); }
    :global(html.mushaf-dock #timestamps-panel) { padding-bottom: 0; }

    /* The whole strip the footer docked into is the hover target; the pill marks it. */
    .mv-dock-handle {
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        height: 22px; /* DOCK_HANDLE_PX */
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        color: var(--text-faint);
        background: transparent;
        border: 0;
        cursor: pointer;
        z-index: 111;
    }
    .mv-dock-pill {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 40px;
        height: 16px;
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        transition: color var(--t-fast), background var(--t-fast);
    }
    .mv-dock-handle:hover { color: var(--text-primary); }
    .mv-dock-handle:hover .mv-dock-pill { background: var(--panel-3, var(--panel-2)); }
    .mv-dock-handle:focus-visible { outline: none; }
    .mv-dock-handle:focus-visible .mv-dock-pill { outline: 2px solid var(--accent); outline-offset: 2px; }

    .mv-status {
        margin: 0;
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }

    .mv-nav {
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 56px;
        padding: 0;
        color: var(--text-muted);
        background: transparent;
        border: 1px solid transparent;
        border-radius: var(--r-2);
        cursor: pointer;
        z-index: 3;
        transition: color var(--t-fast), background var(--t-fast);
    }
    .mv-nav:hover { color: var(--text-primary); background: var(--panel-2); }
    .mv-nav:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .mv-nav svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }

    .mv-back {
        position: absolute;
        bottom: var(--s-3);
        left: 50%;
        transform: translateX(-50%);
        padding: var(--s-1) var(--s-3);
        font-size: var(--fs-meta);
        color: var(--accent-fg);
        background: var(--accent);
        border: none;
        border-radius: 999px;
        box-shadow: var(--shadow-pop);
        cursor: pointer;
        z-index: 3;
    }
    .mv-back:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
