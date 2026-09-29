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
    import { ensureMushafFont, SURAH_FRAME_FAMILY, SURAH_NAME_FAMILY, textFamily, textFontStack } from './fonts';
    import { PageHighlighter } from './highlight';
    import {
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
    import { mushafRepeat } from './repeat.svelte';

    const DEFAULT_PLAYER_H = 72;
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
    let fontsReady = $state(false);
    let failed = $state(false);
    let tsChapters = $state.raw(new Set<number>());
    let chapterIx = $state.raw<ChapterIndex | null>(null);

    const slug = $derived($playerContext.delivery?.slug ?? '');
    const chapter = $derived($playerContext.surahNum ?? 0);
    const live = $derived(chapterIx !== null && chapterIx.chapter === chapter);
    const fontStack = $derived(textFontStack($mushafYear));

    $effect(() => {
        const year = $mushafYear;
        let cancelled = false;
        fontsReady = false;
        void Promise.all([loadLayout(year), ensureMushafFont(textFamily(year))])
            .then(([l]) => {
                if (cancelled) return;
                layout = l;
                fontsReady = true;
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
        const ctrl = new AbortController();
        void loadChapterRecitation(s, ch, ctrl.signal)
            .then((data) => {
                if (ctrl.signal.aborted) return;
                chapterIx = data ? indexChapter(ch, data.units, w) : null;
                lastUnit = -1;
                following = true;
                applyPendingSeek();
            })
            .catch((e: unknown) => console.error('Mushaf: chapter load failed', e));
        return () => ctrl.abort();
    });

    // ---- sizing ----
    let vp = $state({ width: 0, height: 0 });
    let refEm = $state(0);
    let measurer: LineMeasurer | null = null;

    $effect(() => {
        const l = layout;
        const w = words;
        if (!l || !w || !fontsReady) return;
        measurer = new LineMeasurer(`"${textFamily(l.year)}"`);
        refEm = measurer.referenceEm(l, w);
        fontCache.clear();
    });

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
        const bottom = player ? player.getBoundingClientRect().top : window.innerHeight - playerH;
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

    /** Open view `v`, turning the page when it's a neighbour move. */
    function openView(v: number): void {
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
        if (reducedMotion || !metrics) {
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
        following = false;
        openView(v);
    }

    // ---- highlight + follow (per frame) ----
    const highlighter = new PageHighlighter();
    let lastUnit = -1;
    let lastMs = 0;
    let raf: number | null = null;
    /** A playhead jump larger than this between frames is a seek → follow again. */
    const SEEK_JUMP_MS = 1500;

    $effect(() => {
        void slotPages; void metrics; void $mushafScope; void $mushafShowUpcoming;
        const r = root;
        if (!r) return;
        void tick().then(() => highlighter.rebuild(r));
    });

    function frame(): void {
        mushafRepeat.tick();
        const ix = chapterIx;
        const l = layout;
        const w = words;
        if (!ix || !l || !w || !live) return;
        const ms = dashPort.currentTimeMs();
        if (Math.abs(ms - lastMs) > SEEK_JUMP_MS) following = true;
        lastMs = ms;
        const { pos, unit } = positionAt(ix, ms / 1000, lastUnit);
        lastUnit = unit;
        highlighter.apply(
            { ...pos, scope: get(mushafScope), showUpcoming: get(mushafShowUpcoming) },
            w,
        );
        if (following && pos.anchorId && !leaf) {
            const v = viewOfPage(pageOfWord(l, pos.anchorId));
            if (v !== view) openView(v);
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
            mushafRepeat[mushafRepeat.picking] = { surah, ayah };
            mushafRepeat.picking = null;
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

    // Word clicks are delegated from the stage. The words themselves aren't tab
    // stops (a page holds ~150); keyboard users page with ←/→ and type verses
    // into Repeat instead.
    $effect(() => {
        const el = stage;
        if (!el) return;
        el.addEventListener('click', onPageClick);
        return () => el.removeEventListener('click', onPageClick);
    });

    function onKeydown(e: KeyboardEvent): void {
        if (!shouldHandleKey(e, TAB_NAMES.TIMESTAMPS)) return;
        if (e.code !== 'ArrowLeft' && e.code !== 'ArrowRight') return;
        e.preventDefault();
        navigate(e.code === 'ArrowLeft' ? 1 : -1); // the book reads right to left
    }

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
        void ensureMushafFont(SURAH_NAME_FAMILY);
        void ensureMushafFont(SURAH_FRAME_FAMILY);

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
            if (leafTimer) clearTimeout(leafTimer);
            detachRepeat();
            offEnded();
            offTime();
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
        <button
            type="button" class="mv-nav mv-nav-left"
            title={L(m.ts_mushaf_next())} aria-label={L(m.ts_mushaf_next())}
            onclick={() => navigate(1)}
        ><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5" /></svg></button>

        <div class="mv-stage" bind:this={stage}>
            <MushafBook
                {layout} {words} {metrics} {fontOf} {fontStack}
                playable={tsChapters}
                pages={slotPages}
                {leaf}
                onleafend={onLeafEnd}
            />
        </div>

        <button
            type="button" class="mv-nav mv-nav-right"
            title={L(m.ts_mushaf_prev())} aria-label={L(m.ts_mushaf_prev())}
            onclick={() => navigate(-1)}
        ><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3l5 5-5 5" /></svg></button>

        {#if !following}
            <button type="button" class="mv-back" onclick={() => (following = true)}>
                {L(m.ts_mushaf_back_to_reciting())}
            </button>
        {/if}
    {/if}
</div>

<style>
    .mv {
        position: relative;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
    }
    .mv-stage { display: flex; align-items: center; justify-content: center; }
    .mv.picking .mv-stage :global(.mp-w:not(.mp-inert)) { cursor: crosshair; }

    .mv-status {
        margin: 0;
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }

    .mv-nav {
        position: absolute;
        top: 50%;
        transform: translateY(-50%);
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
    .mv-nav-left { left: var(--s-2); }
    .mv-nav-right { right: var(--s-2); }

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
