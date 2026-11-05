<script lang="ts">
    /**
     * Timestamps Mushaf view — the recitation read along in a Madani mushaf
     * (`@qud-technologies/mushaf-svelte`: two pages, or one when the window is
     * too narrow for two).
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
    import type { HitPayload, PageChangePayload } from '@qud-technologies/mushaf-core';
    import { QudMushaf } from '@qud-technologies/mushaf-svelte';
    import type { QudMushafElement } from '@qud-technologies/mushaf-web';
    import type { QuranPack } from '@qud-technologies/quran-data';
    import { onMount, tick, untrack } from 'svelte';
    import { get } from 'svelte/store';

    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';
    import { ensureDashCovering } from '../../../lib/playback/dash-covering';
    import { signalDashSeekIntent } from '../../../lib/playback/dash-buffering';
    import { dashPort } from '../../../lib/playback/dash-port';
    import { exitLoop } from '../../../lib/playback/loop';
    import { loadQuranPack, quranFontUrl } from '../../../lib/quran-data/pack';
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
    import { mushafFullscreen } from './fullscreen.svelte';
    import { readingHighlights } from './highlights';
    import { indexChapter, positionAt, type ChapterIndex, type MushafPosition } from './position';
    import { mushafRepeat } from './repeat.svelte';
    import { compareRefs, type VerseRef } from './repeat-plan';

    const DEFAULT_PLAYER_H = 72;
    /** Room kept under the book for the docked-footer handle in full screen. */
    const DOCK_HANDLE_PX = 22;
    const PLAYER_SELECTOR = '.player';
    /** A page change within this long of a layout switch is the switch's, not the reader's. */
    const OWN_CHANGE_MS = 1500;
    /** Give up waiting for a turn's `pageChange` (a hidden tab never ends the slide). */
    const TURN_TIMEOUT_MS = 2000;
    /** A playhead jump larger than this between frames is a seek → follow again. */
    const SEEK_JUMP_MS = 1500;

    let root: HTMLDivElement | undefined = $state();
    let element: QudMushafElement | undefined = $state();

    // ---- data ----
    let pack = $state.raw<QuranPack | null>(null);
    let failed = $state(false);
    let tsChapters = $state.raw(new Set<number>());
    let chapterIx = $state.raw<ChapterIndex | null>(null);
    /** "slug:chapter" `chapterIx` was built for. */
    let ixKey = $state('');
    /** "slug:chapter" whose timings are being fetched, '' when none. */
    let loadingKey = '';
    /** The element drew its first view (its controller and fonts are in). */
    let ready = $state(false);

    const slug = $derived($playerContext.delivery?.slug ?? '');
    const chapter = $derived($playerContext.surahNum ?? 0);
    const live = $derived(chapterIx !== null && ixKey === `${slug}:${chapter}`);
    const layout = $derived(`madani-${$mushafYear}`);

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
        if (!s || !ch) return;
        const key = `${s}:${ch}`;
        const ctrl = new AbortController();
        loadingKey = key;
        holdAudio();
        void loadChapterRecitation(s, ch, ctrl.signal)
            .then((data) => {
                if (ctrl.signal.aborted) return;
                chapterIx = data ? indexChapter(ch, data.units) : null;
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
        if (!ch || !ready) return;
        untrack(() => {
            if (ch === shownChapter) return;
            shownChapter = ch;
            if (live) return; // already reading along in it
            following = true;
            jumpTo(landingAnchor(ch));
        });
    });

    /** The word a chapter switch lands on: the pending seek's, else the first. */
    function landingAnchor(ch: number): string {
        const t = pendingSeek?.chapter === ch ? pendingSeek.target : null;
        if (t && 'loc' in t) return t.loc;
        if (t) return `${t.verse}:1`;
        return `${ch}:1:1`;
    }

    // ---- sizing: the element fills the box between the tab's top and the player ----
    let boxHeight = $state(0);

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
        boxHeight = Math.max(0, bottom - root.getBoundingClientRect().top);
    }

    // ---- which pages are open ----
    let following = $state(true);
    /** A turn or jump we asked for is under way; its `pageChange` is ours, not the reader's. */
    let ownTurn = false;
    let ownUntil = 0;
    let turnTimer: ReturnType<typeof setTimeout> | null = null;

    function settleTurn(): void {
        ownTurn = false;
        if (turnTimer) clearTimeout(turnTimer);
        turnTimer = null;
    }

    function startOwnTurn(run: () => boolean): void {
        ownTurn = true;
        if (turnTimer) clearTimeout(turnTimer);
        turnTimer = setTimeout(settleTurn, TURN_TIMEOUT_MS);
        if (!run()) settleTurn();
    }

    /** Open the view holding `anchor` at once (no slide). */
    function jumpTo(anchor: string): void {
        const el = element;
        if (!el?.state || !pack) return;
        if (el.state.current.includes(pack.pageOf(layout, anchor))) return;
        startOwnTurn(() => el.goTo(anchor));
    }

    /** Pages of surahs the reciter has are the only ones navigation lands on. */
    function canNavigate(page: number): boolean {
        const ctl = element?.controller;
        if (!ctl) return true;
        return ctl.pageView(page).chrome.surahs.some((s) => tsChapters.has(s));
    }

    function onPageChange(detail: PageChangePayload): void {
        if (ownTurn) {
            settleTurn();
            return;
        }
        if (performance.now() < ownUntil) return;
        // Paging by hand stops following; paging back onto the recited page resumes it.
        following = !!pack && !!lastAnchor && detail.pages.includes(pack.pageOf(layout, lastAnchor));
    }

    // A layout switch re-opens the book on the same words: not the reader paging.
    $effect(() => {
        void layout;
        ownUntil = performance.now() + OWN_CHANGE_MS;
    });

    /** Keep the recited word in view: one slide reading on, a jump otherwise. */
    function follow(anchor: string): void {
        const el = element;
        const st = el?.state;
        if (!el || !st || !pack || ownTurn) return;
        const page = pack.pageOf(layout, anchor);
        const cur = st.current;
        if (cur.includes(page)) return;
        const first = cur[0]!;
        const last = cur.at(-1)!;
        const per = st.pagesPerView;
        if (page > last && page <= last + per) startOwnTurn(() => el.next());
        else if (page < first && page >= first - per) startOwnTurn(() => el.prev());
        else startOwnTurn(() => el.goTo(anchor));
    }

    // ---- highlight + follow (per frame) ----
    let lastUnit = -1;
    let lastMs = 0;
    /** Word the recitation is at (or last was), for "am I on the recited page?". */
    let lastAnchor = '';
    let position = $state.raw<MushafPosition | null>(null);
    let spot = $state.raw<[VerseRef, VerseRef] | null>(null);
    let raf: number | null = null;
    let highlightKey = '';

    function samePosition(a: MushafPosition | null, b: MushafPosition): boolean {
        return !!a && a.active === b.active && a.verseKey === b.verseKey && a.progress === b.progress && a.anchor === b.anchor;
    }

    // Highlights follow the position and the prefs; the element only redraws on a change.
    $effect(() => {
        const el = element;
        const p = pack;
        if (!el || !p || !ready) return;
        const list = readingHighlights({
            pos: live ? position : null,
            firstAnchor: chapterIx?.firstAnchor ?? '',
            scope: $mushafScope,
            showUpcoming: $mushafShowUpcoming,
            spot: mushafRepeat.picking !== null ? spot : null,
            playable: tsChapters,
            ayahs: (s) => p.counts(s).ayahs,
        });
        const key = JSON.stringify(list);
        if (key === highlightKey) return;
        highlightKey = key;
        el.setHighlights(list);
    });

    function frame(): void {
        mushafRepeat.tick();
        if (loadingKey && !dashPort.paused) holdAudio(); // the player autoplayed the new chapter
        const ix = chapterIx;
        if (!ix || !live) return;
        const ms = dashPort.currentTimeMs();
        if (Math.abs(ms - lastMs) > SEEK_JUMP_MS) following = true;
        lastMs = ms;
        const { pos, unit } = positionAt(ix, ms / 1000, lastUnit);
        lastUnit = unit;
        if (pos.anchor) lastAnchor = pos.anchor;
        if (!samePosition(position, pos)) position = pos;
        if (following && pos.anchor && ready) follow(pos.anchor);
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

    /** `{surah, ayah}` of a word (`s:a:w`) or ayah-end (`s:a`) anchor. */
    function verseOf(anchor: string): VerseRef {
        const [surah, ayah] = anchor.split(':').map(Number);
        return { surah: surah ?? 0, ayah: ayah ?? 0 };
    }

    /** A tap on a word (`loc`) or a verse marker (`verse`). */
    function onTap(detail: HitPayload, kind: 'loc' | 'verse'): void {
        const v = verseOf(detail.anchor);
        if (!tsChapters.has(v.surah)) return;
        if (mushafRepeat.picking) {
            pickVerse(v);
            return;
        }
        mushafRepeat.stop();
        exitLoop();
        const target: SeekTarget = kind === 'verse' ? { verse: detail.ayah } : { loc: detail.anchor };
        following = true;
        if (v.surah === chapter && chapterIx?.chapter === chapter) {
            const ms = targetMs(chapterIx, target);
            if (ms !== null) seekTo(ms);
        } else {
            pendingSeek = { chapter: v.surah, target };
            switchChapter(v.surah, true);
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

    /** The anchor of the word under a pointer event, read off the element's page DOM. */
    function anchorAt(e: Event): string {
        const ctl = element?.controller;
        if (!ctl) return '';
        const path = e.composedPath() as HTMLElement[];
        const word = path.find((n) => n.classList?.contains('qm-w'));
        const line = word?.closest<HTMLElement>('.qm-line');
        const page = word?.closest<HTMLElement>('.qm-page');
        if (!word || !line || !page) return '';
        const lines = ctl.pageView(Number(page.dataset.page)).lines;
        return lines.find((l) => l.index === Number(line.dataset.line))?.words[Number(word.dataset.i)]?.anchor ?? '';
    }

    /** Repeat pick: light up the verse under the pointer, or the range it would make. */
    function onPageHover(e: Event): void {
        const end = mushafRepeat.picking;
        if (!end) return;
        const anchor = anchorAt(e);
        const here = anchor ? verseOf(anchor) : null;
        if (!here || !tsChapters.has(here.surah) || !pickable(here)) {
            spot = null;
            return;
        }
        const to = mushafRepeat.to;
        const other = end === 'to'
            ? mushafRepeat.from
            : to && compareRefs(here, to) <= 0 ? to : null;
        spot = [other ?? here, here];
    }

    $effect(() => {
        if (mushafRepeat.picking === null) spot = null;
    });

    $effect(() => {
        const el = element;
        if (!el) return;
        const clear = (): void => { spot = null; };
        el.addEventListener('pointerover', onPageHover);
        el.addEventListener('pointerleave', clear);
        return () => {
            el.removeEventListener('pointerover', onPageHover);
            el.removeEventListener('pointerleave', clear);
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
        // The element turns pages itself while it has focus.
        if ((e.code !== 'ArrowLeft' && e.code !== 'ArrowRight') || e.defaultPrevented) return;
        e.preventDefault();
        if (e.code === 'ArrowLeft') element?.next(); // the book reads right to left
        else element?.prev();
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

    // ---- theme-aware accent (the footer droplet colours the highlight) ----
    let theme = $state(themeStore.current);
    const accentStyle = $derived(accentVarText($recitationConfigStore.highlightColor, theme));

    /** Re-evaluate a message on locale switch (reads the locale rune). */
    const L = (s: string): string => (i18n.locale, s);
    /** Numbers as before: Arabic-Indic digits in Arabic, Western digits otherwise. */
    const bookLocale = $derived(i18n.locale === 'ar' ? 'ar' : 'en');
    const labels = $derived({
        juz: L(m.ts_mushaf_juz({ n: '{n}' })),
        hizb: L(m.ts_mushaf_hizb({ n: '{n}' })),
        page: L(m.ts_mushaf_page_aria({ n: '{n}' })),
        next: L(m.ts_mushaf_next()),
        prev: L(m.ts_mushaf_prev()),
        surah: 'سورة {name}',
    });

    onMount(() => {
        void loadQuranPack()
            .then((p) => { pack = p; })
            .catch((e: unknown) => {
                console.error('Mushaf: Quran data load failed', e);
                failed = true;
            });

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

        const ro = new ResizeObserver(measureViewport);
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
            settleTurn();
            detachRepeat();
            offEnded();
            offTime();
            offPlay();
            ro.disconnect();
            window.removeEventListener('resize', measureViewport);
            window.removeEventListener(THEME_CHANGE_EVENT, onTheme);
        };
    });
</script>

<svelte:window onkeydown={onKeydown} />

<div
    class="mv"
    class:picking={mushafRepeat.picking !== null}
    bind:this={root}
    style="{accentStyle}; height: {boxHeight}px"
>
    {#if failed}
        <p class="mv-status">{L(m.ts_mushaf_load_failed())}</p>
    {:else if !pack}
        <p class="mv-status">{L(m.ts_mushaf_loading())}</p>
    {:else}
        <QudMushaf
            bind:element
            class="mv-book"
            {pack}
            fontUrl={quranFontUrl}
            {layout}
            mode="auto"
            theme={{ scheme: theme === 'light' ? 'light' : 'dark' }}
            locale={bookLocale}
            {labels}
            navButtons
            {canNavigate}
            onReady={() => { ready = true; }}
            onPageChange={onPageChange}
            onWordTap={(d) => onTap(d, 'loc')}
            onAyahTap={(d) => onTap(d, 'verse')}
            onError={(d) => console.error('Mushaf:', d.code, d.message)}
        />

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
        overflow: hidden;
    }

    /* The book: the app's tokens drive the element's theme (the element's own
     * scheme colours are the fallback). Its custom properties are set here, on
     * the element, which wins over the element's :host rule. */
    .mv :global(qud-mushaf.mv-book) {
        flex: 1 1 auto;
        align-self: stretch;
        outline: none;
        --qm-background: transparent;
        --qm-page: var(--mushaf-paper);
        --qm-text: var(--text-primary);
        --qm-chrome: var(--text-muted);
        --qm-marker: var(--text-secondary);
        --qm-frame: var(--text-muted);
        --qm-spine: var(--mushaf-spine);
        --qm-highlight-text: var(--accent);
        --qm-highlight-band: var(--accent-tint-soft);
        --qm-highlight-underline: var(--accent);
        --qm-band-radius: var(--r-2);
        --qm-ui-font: var(--font-ui, inherit);
    }
    .mv :global(qud-mushaf.mv-book::part(word)) { transition: color var(--t-fast), opacity var(--t-fast); }
    .mv :global(qud-mushaf.mv-book::part(word):hover) { color: var(--accent); }
    /* Surahs the reciter lacks: no pointer, no hover. */
    .mv :global(qud-mushaf.mv-book::part(hl-inert)) { cursor: default; }
    .mv :global(qud-mushaf.mv-book::part(hl-inert):hover) { color: unset; }
    .mv :global(qud-mushaf.mv-book::part(nav)) { color: var(--text-muted); transition: color var(--t-fast), background var(--t-fast); }
    .mv :global(qud-mushaf.mv-book::part(nav):hover) { color: var(--text-primary); background: var(--panel-2); }
    /* Picking a verse: the page dims, the verse (or range) under the pointer lights up. */
    .mv.picking :global(qud-mushaf.mv-book::part(word)) { opacity: 0.45; cursor: crosshair; }
    .mv.picking :global(qud-mushaf.mv-book::part(hl-inert)) { cursor: default; }
    .mv.picking :global(qud-mushaf.mv-book::part(hl-pick)) { opacity: 1; color: var(--accent); }
    .mv.picking :global(qud-mushaf.mv-book::part(band)) { visibility: hidden; }

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
        margin: 0 auto;
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }

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
