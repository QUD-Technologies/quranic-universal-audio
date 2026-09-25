<script lang="ts">
    /**
     * One-line recitation teleprompter.
     *
     * Renders a single line of words for the current ayah from `pageStart`,
     * highlighting word-by-word (or char-by-char) in sync with playback. The
     * line clears + re-pages when:
     *   - the active word would overflow the line ("out of space"), or
     *   - the chain finishes (restart from the new chain's first word).
     *
     * A cross-verse waṣl chains the line PAST a verse end: when verse N recites
     * into N+1 without a stop, the page extends to the whole chain (`wasl-chains`)
     * so N+1's words flow onto the same line and the `۝` marker sits inline
     * between them; the line clears only when the active verse leaves the chain.
     * The `۝` marker silence-colours on a real waqf stop at a (non-bridging)
     * verse end.
     *
     * The reveal engine (`engine/*`) is shared with the timestamps tab; the
     * paging + measurement is the line-specific layer here. All styling is
     * driven by CSS custom properties projected from `RecitationAnimConfig`,
     * so the throwaway playground can tune it live.
     */
    import { isCombiningMark, toArabicNumeral, ZWSP } from '../utils/arabic-text';
    import { AYAH_END_ORNAMENT } from '../riwayat';
    import { ayahUnitRanges } from './chapter-words';
    import { themeStore } from '../stores/theme.svelte';
    import { cssVarText, type RecitationAnimConfig } from './config';
    import { buildAnimStructure, stampCharTimes, type AnimSourceWord } from './engine/build-structure';
    import {
        clearHighlights,
        indexCache,
        type HighlightCache,
    } from './engine/index-cache';
    import { fittedPrefixLength } from './line-window';
    import { type ActiveHit, buildSortedIntervals, findActiveAt } from './recitation-active';
    import type { AnimUnit, TimeSpan } from './types';
    import { buildWaslChains } from './wasl-chains';
    import {
        EMPTY_SHAPED_GLYPHS,
        type ShapedGlyphFixture,
    } from './shaped-glyphs';


    /** Standalone Quranic marks are already isolated as shaped SVG paths. A
     * drop-shadow around such a tiny path can overlap its seat/host and make
     * that neighbouring letter look co-highlighted (notably هُۥ in 21:88).
     * Tatweel/word-joiner are positioning anchors, not painted bases. */
    function isMarkOnly(text: string): boolean {
        let sawMark = false;
        for (const char of text.normalize('NFD')) {
            const cp = char.codePointAt(0);
            if (cp === undefined) continue;
            if (isCombiningMark(cp)) {
                sawMark = true;
                continue;
            }
            if (cp === 0x0640 || cp === 0x2060) continue;
            return false;
        }
        return sawMark;
    }

    interface Props {
        units: AnimUnit[];
        config: RecitationAnimConfig;
        /** Chapter-absolute current time, ms. */
        getTimeMs: () => number;
        /** Whether playback is running (drives the rAF loop). */
        playing: boolean;
        /** Pre-shaped DigitalKhatt geometry for the active Hafs chapter. */
        shapedGlyphs?: ShapedGlyphFixture;
        /** Reveal timing-only silent tokens without applying the active colour. */
        omitSilentHighlights?: boolean;
        /** Click-to-seek: receives the clicked word's chapter-absolute ms. */
        onSeekToWord?: (_ms: number) => void;
        /**
         * Glyph placed before an ayah's Arabic-Indic number. Defaults to the
         * Hafs ornament, which Digital Khatt expects to be sent alongside the
         * digits; the three packaged QPC fonts decorate the digits themselves,
         * so a non-Hafs edition passes `''` (D7).
         */
        ayahMarker?: string;
    }

    let {
        units,
        config,
        getTimeMs,
        playing,
        shapedGlyphs = EMPTY_SHAPED_GLYPHS,
        omitSilentHighlights = false,
        onSeekToWord,
        ayahMarker = AYAH_END_ORNAMENT,
    }: Props = $props();

    // ---- paging state (reactive — drives the rendered page) ----
    let rootEl = $state<HTMLDivElement | undefined>(undefined);
    let pageStart = $state(0);
    let pageAyahKey = $state('');
    /** Words rendered on the current page; null = "measure the remainder". */
    let pageCount = $state<number | null>(null);
    /** Set for the frame a layout/granularity switch re-pages the line, so the
     *  per-word opacity/color transitions are suppressed (words snap straight to
     *  their new-mode state). Without this, switching letter→word fades every
     *  word from char-mode opacity:1 down to the dim unreached level at once,
     *  reading as a flash of all-words-active. Cleared once the sweep settles. */
    let suppressTransition = $state(false);

    // ---- imperative per-frame state (not reactive) ----
    let wordCache: HighlightCache | null = null;
    let charCache: HighlightCache | null = null;
    let globalActive = -1;
    /** Reading index of the last active word (the cursor). On a jump-back it
     *  decreases; used to keep the trail revealed during a silence gap. */
    let lastActive = -1;

    const ayahRanges = $derived(ayahUnitRanges(units));

    // Cross-verse waṣl chains: which verses recite into the next without a stop.
    // A page spans a whole chain so the line flows across the boundary.
    const waslChains = $derived(buildWaslChains(units));
    const pageChainKey = $derived(waslChains.chainStartOf.get(pageAyahKey) ?? pageAyahKey);

    // Flat sorted-by-start list of every occurrence interval across all units,
    // for binary-search active lookup on fast-path miss. Built once per chapter.
    const sortedIntervals = $derived(buildSortedIntervals(units));
    // The page runs to the end of the active CHAIN (a single verse when not in a
    // waṣl chain), so a bridged N»N+1 flows onto one line instead of clearing.
    const ayahEndIdx = $derived(
        config.clearOnAyahEnd
            ? (waslChains.chainEndIdxOf.get(pageChainKey)
                ?? ayahRanges.get(pageAyahKey)?.[1] ?? units.length)
            : units.length,
    );
    // While measuring (pageCount null) render the whole remainder so the fit
    // probe sees every candidate; once measured, render exactly the fitted set
    // (which `text-align` then centers).
    const pageEnd = $derived(
        pageCount === null ? ayahEndIdx : Math.min(pageStart + pageCount, ayahEndIdx),
    );
    const pageUnits = $derived(units.slice(pageStart, pageEnd));
    const structure = $derived.by(() => {
        const s = buildAnimStructure(
            pageUnits.map(
                (u): AnimSourceWord => ({
                    text: u.text,
                    display_text: u.text,
                    // Letter timings are anchored to the FIRST occurrence; a
                    // repeated word's `u.start/u.end` are expanded to span every
                    // occurrence (min..max), which would stretch the char
                    // fallback range. Use the first occurrence's span instead.
                    start: u.intervals[0]?.start ?? u.start,
                    end: u.intervals[0]?.end ?? u.end,
                    letters: u.letters,
                }),
            ),
        );
        // Attach each cluster's per-occurrence [start,end] from the take's OWN
        // letters, so `sweepChar` reveals a repeat at its real pace rather than
        // linearly stretching take 1 (the cause of "future letters lit on take 2").
        for (let wi = 0; wi < s.length; wi++) {
            const u = pageUnits[wi];
            const occLetters = u?.occurrenceLetters;
            const chars = s[wi]!.chars;
            if (!u || !occLetters) continue;
            const perChar: (TimeSpan | undefined)[][] = chars.map(() => []);
            const perSilent: (boolean | undefined)[][] = chars.map(() => []);
            u.intervals.forEach((span, oi) => {
                const lts = occLetters[oi];
                let times: (TimeSpan | undefined)[] | null = null;
                let sameFoundationTokens = false;
                if (lts && lts.length) {
                    sameFoundationTokens = lts.length === chars.length
                        && lts.every((lt, index) => lt.char === chars[index]?.text);
                    if (sameFoundationTokens) {
                        // Animation-token IDs are positional inside one native
                        // reading, not stable across repeated readings. The
                        // producer's within-word token order + exact text is the
                        // stable identity shared by stop/wasl takes.
                        times = lts.map((lt) => lt.start != null && lt.end != null
                            ? { start: lt.start, end: lt.end }
                            : undefined);
                    } else {
                        times = stampCharTimes(chars, lts, span.start, span.end);
                    }
                }
                chars.forEach((_, di) => {
                    perChar[di]!.push(times ? times[di] : undefined);
                    perSilent[di]!.push(sameFoundationTokens ? lts?.[di]?.silent : undefined);
                });
            });
            chars.forEach((ch, di) => {
                ch.occIntervals = perChar[di];
                ch.occSilent = perSilent[di];
            });
        }
        return s;
    });

    // Reset paging whenever the chapter (units identity) changes.
    $effect(() => {
        void units; // track
        pageStart = 0;
        pageAyahKey = units[0]?.ayahKey ?? '';
        pageCount = null;
        globalActive = -1;
        lastActive = -1;
    });

    // Layout-affecting config changes force a re-measure (re-page) of the line.
    // Kept separate from color/timing tweaks so tuning those doesn't re-page.
    $effect(() => {
        void config.fontSizePx;
        void config.lineHeight;
        void config.wordSpacingPx;
        void config.letterSpacingPx;
        void config.fontFamily;
        void config.granularity;
        void config.showAyahMarker;
        void shapedGlyphs; // may land after the units (outlines vs native text)
        pageCount = null;
        // Snap (no fade) across this re-page so a granularity switch doesn't
        // flash every word lit; re-enabled once the sweep below settles.
        suppressTransition = true;
    });

    // Re-measure once webfonts finish loading. The display font (DigitalKhatt)
    // loads async; a first measure against the fallback font has different
    // metrics and would otherwise lock in a too-small page on cold load.
    $effect(() => {
        if (typeof document === 'undefined' || !document.fonts) return;
        let cancelled = false;
        void document.fonts.ready.then(() => {
            if (!cancelled) pageCount = null;
        });
        return () => {
            cancelled = true;
        };
    });

    // Rebuild caches + measure fit after each render. Settles the fitted page
    // count in two passes (measure-all → render-fitted), then sweeps. Runs
    // post-DOM-commit, so the spans exist.
    $effect(() => {
        void structure; // re-run when the page content changes
        void omitSilentHighlights; // policy toggles re-sweep without re-paging
        void shapedGlyphs; // late glyphs replace the word spans → re-index them
        if (!rootEl) return;
        wordCache = indexCache(rootEl, '.ra-word');
        charCache = config.granularity === 'char' ? indexCache(rootEl, '.ra-char') : null;
        // Reuse the freshly-built caches' element refs instead of a second
        // `querySelectorAll` over the same line.
        clearHighlights(rootEl, wordCache, charCache);
        const fitted = measureFits();
        if (pageCount === null || fitted < pageCount) {
            pageCount = fitted; // re-renders the fitted set; this effect re-runs
            return; // sweep on the stabilized pass
        }
        doSweep();
        // The corrected per-word classes are now committed with transitions
        // off; re-enable smooth transitions once this paints (double rAF so
        // the snapped state lands first, then subsequent frames animate).
        if (suppressTransition) {
            requestAnimationFrame(() =>
                requestAnimationFrame(() => { suppressTransition = false; }),
            );
        }
    });

    // rAF loop — only alive while playing.
    $effect(() => {
        if (!playing) return;
        let raf = 0;
        const loop = (): void => {
            tick();
            raf = requestAnimationFrame(loop);
        };
        raf = requestAnimationFrame(loop);
        return () => cancelAnimationFrame(raf);
    });

    /** Count of leading words that fully fit the line (always ≥1). */
    function measureFits(): number {
        if (!rootEl) return 1;
        const rowRect = rootEl.getBoundingClientRect();
        const eps = 0.75;
        const fits: boolean[] = [];
        rootEl.querySelectorAll<HTMLElement>('.ra-word').forEach((el) => {
            const r = el.getBoundingClientRect();
            fits.push(r.left >= rowRect.left - eps && r.right <= rowRect.right + eps);
        });
        return fittedPrefixLength(fits);
    }

    function doSweep(t?: number, hit?: ActiveHit | null): void {
        const tt = t ?? (getTimeMs() + config.leadMs) / 1000;
        // `tick()` already computed the active hit to drive paging; reuse its
        // result rather than re-bsearching. Structure-effect callers omit it.
        const h = hit !== undefined ? hit : findActiveAt(units, sortedIntervals, tt, globalActive);
        if (config.granularity === 'char') {
            sweepChar(tt, h);
        } else {
            sweepWord(h);
        }
        sweepDecorators(h);
        sweepMarker(h);
    }

    /** Silence-colour the `۝` marker when a pause holds at its verse end — a real
     *  waqf stop. A waṣl boundary has no stop (recitation flows on), so its marker
     *  never lights; the `bridgesNext` guard also suppresses any one-frame gap. */
    function sweepMarker(hit: ActiveHit | null): void {
        if (!rootEl) return;
        const inPause = hit === null && lastActive >= 0;
        const markers = rootEl.querySelectorAll<HTMLElement>('.ra-ayah-marker');
        for (const mk of markers) {
            const after = parseInt(mk.dataset.afterWord ?? '-1', 10);
            const ayahKey = pageUnits[after]?.ayahKey;
            const pausing = inPause && after === lastActive
                && !!ayahKey && !waslChains.bridgesNext.has(ayahKey);
            mk.classList.toggle('marker-pause', pausing);
        }
    }

    /** Drive each word's dynamic decorator marks. Only the waqf (stop) sign is
     *  dynamic (`.ra-decorator--waqf`); rub-el-hizb and sajdah are static markers
     *  handled purely in CSS.
     *
     *  Opacity follows the SAME reveal model as the text it rides. Word mode
     *  reveals the whole word at once, so the mark just composites under the
     *  word's opacity (handled in CSS, no class). Char mode reveals letter by
     *  letter, and the mark rides the LAST letter — so `.revealed` lights the
     *  sign once that last letter is REACHED (recitation has passed it), not
     *  while it is still being recited, and not at the word's first letter.
     *
     *  `waqf-active` lights the sign while a pause holds on its word: a silence
     *  gap (`hit == null`) after at least one word was lit. The sign never takes
     *  the per-letter reveal highlight — only this pause light recolours it. */
    function sweepDecorators(hit: ActiveHit | null): void {
        if (!wordCache) return;
        const inPause = hit === null && lastActive >= 0;
        const charMode = config.granularity === 'char';
        const items = wordCache.items;
        for (let i = 0; i < items.length; i++) {
            const wordEl = items[i]?.el;
            const mark = wordEl?.querySelector('.ra-decorator--waqf');
            if (!mark || !wordEl) continue;
            if (charMode) {
                const chars = wordEl.querySelectorAll('.ra-char');
                const anchor = chars.length ? chars[chars.length - 1]! : wordEl;
                // `reached` only — the sign reveals once recitation has PASSED its
                // last letter, not while that letter is still being recited.
                mark.classList.toggle('revealed', anchor.classList.contains('reached'));
            }
            mark.classList.toggle('waqf-active', inPause && i === lastActive);
        }
    }

    /** Translate a global unit index to its page-local position, or -1 if the
     *  active unit is off the current page. */
    function pageLocal(globalIdx: number): number {
        if (globalIdx < 0) return -1;
        const local = globalIdx - pageStart;
        return local >= 0 && local < pageUnits.length ? local : -1;
    }

    /** Word-granularity highlight from per-word occurrence intervals.
     *  active  = the word whose any occurrence span contains t (a repeat
     *            re-lights the existing word — no duplicate text), or none
     *            during a silence gap.
     *  reached = words strictly BEFORE the active cursor (reading order). When
     *            the reciter jumps back, the cursor moves to the repeated word
     *            and the words now AHEAD of it (already recited) revert to
     *            unreached. During a silence gap the trail up to the last active
     *            word stays revealed (so a pause doesn't blank the line). */
    function sweepWord(hit: ActiveHit | null): void {
        if (!wordCache) return;
        const items = wordCache.items;
        const active = pageLocal(hit?.unitIdx ?? -1);
        if (active >= 0) lastActive = active;
        for (let i = 0; i < items.length; i++) {
            const el = items[i]?.el;
            if (!el) continue;
            const isActive = i === active;
            const isReached = (active >= 0 ? i < active : i <= lastActive) && !isActive;
            el.classList.toggle('active', isActive);
            el.classList.toggle('reached', isReached);
        }
    }

    /** Char-granularity highlight — occurrence-aware, mirroring {@link sweepWord}.
     *  The naive time-based char sweep keyed each letter off its single canonical
     *  [start,end]; on a repeat / look-back the audio time is past every letter
     *  of the repeated word, so the whole word read `reached` and the active
     *  letter never travelled back. Instead we locate the active word's CURRENT
     *  occurrence and reveal letter-by-letter against THAT take's own letter
     *  timings (`ch.occIntervals`, raw playback time) — so a re-recitation
     *  tracks its own (often slower / melodic) pace. When a take carries no
     *  per-letter data we fall back to remapping `t` onto the canonical timeline.
     *
     *  Walk order matches the DOM: `.ra-char` spans exist only for words with
     *  `hasChars`, in `structure` order, so the flat `charCache` index advances
     *  in lockstep with the rendered spans. */
    function sweepChar(t: number, hit: ActiveHit | null): void {
        if (!charCache) return;
        const items = charCache.items;

        // Active word (local page index) + the occurrence interval containing t.
        const active = pageLocal(hit?.unitIdx ?? -1);
        const occStart = hit?.ivStart ?? 0;
        const occEnd = hit?.ivEnd ?? 0;
        const activeOccIdx = active >= 0 && hit
            ? pageUnits[active]!.intervals.findIndex(
                (iv) => iv.start === occStart && iv.end === occEnd,
            )
            : -1;
        if (active >= 0) lastActive = active;

        // Remap playback time into the active word's canonical letter timeline —
        // the FALLBACK for a take with no per-letter data. The letters are anchored
        // to the FIRST occurrence (`intervals[0]`), so we map the CURRENT
        // occurrence's progress onto that span. (Using the unit's start/end is
        // wrong for repeats: they expand to cover every occurrence, overshooting
        // all letters → nothing animates on a repeat.)
        let localT = -1;
        if (active >= 0) {
            const canon = pageUnits[active]!.intervals[0] ?? { start: occStart, end: occEnd };
            const span = occEnd - occStart;
            const frac = span > 0 ? (t - occStart) / span : 0;
            localT = canon.start + frac * (canon.end - canon.start);
        }

        let ci = 0;
        for (let wi = 0; wi < structure.length; wi++) {
            const sw = structure[wi];
            const chars = sw && sw.hasChars ? sw.chars : [];
            // Words before the active cursor are reached; after it, unreached.
            // During a silence gap (active < 0) keep the trail up to lastActive.
            const wordReached = active >= 0 ? wi < active : wi <= lastActive;
            // Mark word-level state too (not just chars). In char mode, CSS
            // hides per-letter spans for non-active words so Arabic keeps its
            // joined whole-word silhouette. Cross-word co-timed letters are the
            // exception: if a non-current word has an active char, that word
            // must also become active or the highlighted char is hidden.
            const wordEl = wordCache?.items[wi]?.el;
            let wordHasLiveChar = false;
            for (let k = 0; k < chars.length; k++) {
                const el = items[ci]?.el;
                ci++;
                if (!el) continue;
                let isActive = false;
                let isReached = wordReached;
                const ch = chars[k]!;
                let silentForOccurrence = ch.silent;
                const rawOccIdx = ch.occIntervals?.findIndex(
                    (iv) => iv !== undefined && t >= iv.start && t < iv.end,
                ) ?? -1;
                const activeOccIv = wi === active && activeOccIdx >= 0
                    ? ch.occIntervals?.[activeOccIdx]
                    : undefined;
                const hasRecordedOccurrences = ch.occIntervals?.some(
                    (iv) => iv !== undefined,
                ) ?? false;
                // Prefer the active take's OWN letter timing (raw playback time);
                // fall back to the canonical remap when that take lacks per-letter
                // data (or the unit was built without `occurrenceLetters`).
                if (rawOccIdx >= 0) {
                    isActive = true;
                    isReached = false;
                    silentForOccurrence = ch.occSilent?.[rawOccIdx] ?? ch.silent;
                } else if (activeOccIv) {
                    isReached = t >= activeOccIv.end;
                    silentForOccurrence = ch.occSilent?.[activeOccIdx] ?? ch.silent;
                } else if (wi === active && localT >= 0) {
                    isActive = localT >= ch.start && localT < ch.end;
                    isReached = !isActive && localT >= ch.end;
                } else if (!hasRecordedOccurrences && active >= 0 && localT >= 0
                    && sw && (ch.start !== sw.start || ch.end !== sw.end)) {
                    // Cross-word idgham/ghunnah: a real-timed letter in a
                    // NON-active word, co-timed with the active letter, lights
                    // with it. Compare against the SAME remapped `localT` the
                    // active word uses — not raw `t`, which on a repeat/loopback
                    // sits in a later occurrence and overshoots the canonical
                    // interval, dropping the co-timed letter to `reached`.
                    // Word-timed fallback chars are skipped so an overlapping
                    // word isn't flooded.
                    if (localT >= ch.start && localT < ch.end) { isActive = true; isReached = false; }
                    else if (localT >= ch.end) isReached = true; // keep co-timed trail revealed
                }
                // Inert symbols (rub-el-hizb, sajdah) reveal in place but never
                // take the highlight — show as reached where they'd otherwise light.
                if (ch.inert && isActive) { isActive = false; isReached = true; }
                if (isActive) {
                    wordHasLiveChar = true;
                    if (omitSilentHighlights && silentForOccurrence) {
                        isActive = false;
                        // Dim/hidden modes need an opaque base-colour overlay so
                        // the borrower appears at its target's time. Full mode's
                        // base silhouette is already opaque, so paint no extra
                        // layer at all.
                        isReached = config.unreachedOpacity < 1;
                    }
                }
                el.classList.toggle('active', isActive);
                el.classList.toggle('reached', isReached);
            }
            if (wordEl) {
                const wActive = wi === active || wordHasLiveChar;
                wordEl.classList.toggle('active', wActive);
                wordEl.classList.toggle('reached', wordReached && !wActive);
            }
        }
    }

    function repaginate(start: number, ayahKey: string): void {
        pageStart = start;
        pageAyahKey = ayahKey;
        pageCount = null;
        lastActive = -1;
        // The structure effect re-measures + sweeps once the new page renders.
    }

    function tick(): void {
        if (!units.length) return;
        const t = (getTimeMs() + config.leadMs) / 1000;
        const hit = findActiveAt(units, sortedIntervals, t, globalActive);
        const ga = hit?.unitIdx ?? -1;
        if (ga >= 0) {
            const activeAyah = units[ga]!.ayahKey;
            const activeChain = waslChains.chainStartOf.get(activeAyah) ?? activeAyah;

            // Chain finished → restart from the new chain's entered ayah. A waṣl
            // boundary stays in the SAME chain, so a forward crossing N»N+1 never
            // clears (the line flows on; overflow re-pages within the chain).
            if (config.clearOnAyahEnd && activeChain !== pageChainKey) {
                const range = ayahRanges.get(activeAyah);
                repaginate(range ? range[0] : ga, activeAyah);
                globalActive = ga;
                return;
            }

            // Active word off the current page — overflow forward, or look-back
            // to an earlier page → re-page to the word so the highlight shows.
            const localActive = ga - pageStart;
            if (
                config.clearOnOverflow
                && pageCount !== null
                && (localActive >= pageCount || localActive < 0)
            ) {
                repaginate(ga, activeAyah);
                globalActive = ga;
                return;
            }

            globalActive = ga;
        }

        // Always sweep. During a silence gap (ga < 0) this clears the active
        // highlight — the just-finished word goes `reached`, nothing is active.
        // On look-back the time-based sweep travels the highlight backward.
        doSweep(t, hit);
    }

    /** Force a reveal update — call after a seek while paused. */
    export function refresh(): void {
        tick();
    }
</script>

<div
    bind:this={rootEl}
    class="ra-line"
    class:ra-chars={config.granularity === 'char'}
    class:ra-omit-silent={omitSilentHighlights}
    class:ra-full-opacity={config.unreachedOpacity >= 0.999}
    class:ra-no-transition={suppressTransition}
    style={cssVarText(config, themeStore.current)}
    style:text-align={pageCount === null ? 'right' : null}
>
    {#each structure as w, i (pageStart + '-' + i)}
        {@const u = pageUnits[i]}
        {@const shaped = u ? shapedGlyphs.words[u.text] : undefined}
        {#if i > 0}{' '}{/if}<span
            class="ra-word"
            data-start={w.start}
            data-end={w.end}
            role="button"
            tabindex="-1"
            onclick={() => onSeekToWord?.((pageUnits[i]?.start ?? 0) * 1000)}
            onkeydown={() => {}}
                    >{#each w.leading.filter((d) => !(shaped && w.hasChars && d.role === 'waqf')) as d, di (di)}<span
                    class="ra-decorator ra-decorator--{d.role}"
                    aria-hidden="true">{ZWSP}{d.glyph}</span
                    >{/each}{#if shaped && w.hasChars}<span
                        class="ra-shaped-base-text"
                        aria-hidden="true"
                        style="visibility: hidden">{shaped.baseText}</span
                    ><svg
                        class="ra-shaped-svg"
                        viewBox="0 -200 {shaped.advance} 1500"
                        preserveAspectRatio="none"
                        aria-label={u?.text}
                        ><g class="ra-shaped-base-layer" aria-hidden="true"
                            >{#each shaped.placements.filter((placement) => placement[4] !== 'waqf') as placement}<path
                                    d={shapedGlyphs.paths[placement[0]]}
                                    transform="translate({placement[1]} {placement[2]}) scale(1 -1)"
                                />{/each}</g
                        >{#each w.chars as ch, ci (ch.tokenId ?? ci)}<g
                                class="ra-char ra-shaped-token"
                                data-start={ch.start}
                                data-end={ch.end}
                                data-group-id={ch.groupId}
                                data-token-id={ch.tokenId}
                                data-token-text={ch.text}
                                data-mark-only={isMarkOnly(ch.text)}
                                data-has-silent-companion={shaped.placements.some(
                                    (placement) => placement[3] === ci && placement[4] === 'silent_companion',
                                )}
                                data-silent={ch.silent}
                                >{#each shaped.placements.filter((placement) => placement[3] === ci && placement[4] !== 'waqf') as placement}<path
                                        class:ra-silent-companion={placement[4] === 'silent_companion'}
                                        d={shapedGlyphs.paths[placement[0]]}
                                        transform="translate({placement[1]} {placement[2]}) scale(1 -1)"
                                    />{/each}</g
                            >{/each}{#each shaped.placements.filter((placement) => placement[4] === 'waqf') as placement}<g
                                class="ra-decorator ra-decorator--waqf"
                                ><path
                                    d={shapedGlyphs.paths[placement[0]]}
                                    transform="translate({placement[1]} {placement[2]}) scale(1 -1)"
                                /></g
                            >{/each}</svg
                    >{:else if config.granularity === 'char' && w.hasChars}<span class="ra-word-ink" aria-hidden="true">{w.clean}</span
                    >{#each w.chars as ch, ci (ci)}<span
                            class="ra-char"
                            data-start={ch.start}
                            data-end={ch.end}
                            data-group-id={ch.groupId}
                            data-silent={ch.silent}>{ch.text}</span
                        >{/each}{:else}{w.clean}{/if}{#each w.trailing.filter((d) => !(shaped && w.hasChars && d.role === 'waqf')) as d, di (di)}<span
                    class="ra-decorator ra-decorator--{d.role}"
                    aria-hidden="true">{ZWSP}{d.glyph}</span
                >{/each}</span
        >{#if config.showAyahMarker && u && ayahRanges.get(u.ayahKey)?.[1] === pageStart + i + 1}{' '}<span
                class="ra-ayah-marker"
                data-after-word={i}>{ayahMarker}{toArabicNumeral(u.ayah)}</span
            >{/if}
    {/each}
</div>

<style>
    .ra-line {
        /* RTL: first (recitation-order) word sits at the right, the line fills
         *  leftward, overflow clips on the left. `unicode-bidi: plaintext`
         *  must NOT be used here — it reorders the inline-block word boxes
         *  left-to-right. Plain `direction: rtl` flows the words right-to-left. */
        direction: rtl;
        /* Default alignment from config (`--ra-align`); the component overrides
         *  to `right` for the one-frame measure pass so the fitted-prefix probe
         *  isn't offset by centering. */
        text-align: var(--ra-align);
        white-space: nowrap;
        overflow: hidden;
        width: 100%;
        height: calc(var(--ra-font-size) * var(--ra-line-height));
        /* Reserve vertical headroom so an active word scaled by `activeScale`
         *  (>1) isn't clipped top/bottom by `overflow:hidden`. content-box keeps
         *  `height` the text row; the padding adds the scale headroom around it. */
        padding-block: var(--ra-scale-pad, 0px);
        box-sizing: content-box;
        line-height: var(--ra-line-height);
        font-family: var(--ra-font);
        font-size: var(--ra-font-size);
        letter-spacing: var(--ra-letter-spacing);
        word-spacing: var(--ra-word-spacing);
        color: var(--ra-base-color);
    }

    /* End-of-ayah marker (۝ + Arabic-Indic numeral). Quiet divider; always
     *  visible (not part of the reveal). Inherits the line font + base color;
     *  gets the base outline. */
    .ra-ayah-marker {
        display: inline-block;
        color: var(--ra-base-color);
        text-shadow: var(--ra-word-shadow);
        transition: color var(--ra-active-emphasis) var(--ra-easing);
    }
    /* Lit while a pause holds at this (non-bridging) verse end — a waqf stop. */
    .ra-ayah-marker:global(.marker-pause) {
        color: var(--ra-highlight);
    }

    /* Word granularity: the whole shaped word is the animated unit, so scale
     *  and fade never disturb the joined glyph geometry or leak into char mode. */
    .ra-word {
        display: inline-block;
        /* Shared containing block for the shaped SVG in both granularities. */
        position: relative;
        cursor: pointer;
        opacity: var(--ra-unreached-opacity);
        text-shadow: var(--ra-word-shadow);
        transition:
            opacity var(--ra-word-reveal) var(--ra-easing),
            color var(--ra-active-emphasis) var(--ra-easing),
            transform var(--ra-active-emphasis) var(--ra-easing),
            text-shadow var(--ra-active-emphasis) var(--ra-easing);
    }
    /* `reached` / `active` are toggled imperatively by the engine, so scope
     *  only `.ra-word` and keep the state class global (else Svelte prunes /
     *  warns it as unused). Word-mode only — char mode reuses the same word-level
     *  classes for a different purpose (whole-word fallback, see below). */
    .ra-line:not(.ra-chars) .ra-word:global(.reached) {
        opacity: 1; /* visited words stay fully visible (no reached-dim) */
    }
    .ra-line:not(.ra-chars) .ra-word:global(.active) {
        opacity: 1;
        color: var(--ra-highlight);
        transform: scale(var(--ra-active-scale));
        text-shadow: var(--ra-word-shadow-active);
    }

    /* Char granularity: the word stays lit; characters are the animated unit.
     *  The legibility stroke is drawn ONCE at the word-silhouette level via the
     *  `.ra-word-ink` underlay (a transparent-fill copy of the whole word that
     *  carries the outline), so cursive-joined letters are never individually
     *  stroked — a per-char outline shows dark seams across the joins. */
    .ra-line.ra-chars .ra-word {
        opacity: 1;
        /* Don't inherit the word outline down to each char span. */
        text-shadow: none;
    }
    /* Shaped letter mode has exactly one paint source for the word's lifetime.
     * Native DigitalKhatt text remains in flow only as the width/line-height
     * ruler; `visibility:hidden` keeps that geometry without rasterizing it. */
    .ra-line .ra-word .ra-shaped-base-text {
        visibility: hidden;
        white-space: nowrap;
        pointer-events: none;
    }
    .ra-line .ra-word .ra-shaped-svg {
        position: absolute;
        /* The shaped paths use the font's 1000-unit em at 1.5em of vertical
         * canvas. Centre that canvas inside the native line box with a POSITIVE
         * inset. The previous negative inset put the SVG baseline ~12px above
         * the native DigitalKhatt baseline at the default 26px size. */
        inset: calc((var(--ra-line-height) - 1.5) * 0.5em) 0 auto;
        width: 100%;
        height: 1.5em;
        overflow: visible;
        pointer-events: none;
        color: inherit;
        opacity: 1;
        transition: opacity var(--ra-char-reveal) var(--ra-easing);
        /* Apply the legibility silhouette to the complete joined word. A
         * per-token filter would draw dark seams through cursive joins. */
        filter: var(--ra-svg-word-shadow);
    }
    .ra-line .ra-word .ra-shaped-svg path {
        fill: currentColor;
    }
    /* Word mode paints one complete base silhouette only. Letter mode adds
     * token overlays for independent colouring while retaining that same base
     * silhouette underneath. Keeping the base in both modes also preserves
     * source-owned inert glyphs which deliberately have no animation token.
     *
     * Letter mode adds one
     * complete base silhouette underneath it so the unreached remainder of an
     * active word is dimmed as a single SVG group, not path by path. SVG group
     * opacity composites after its joined children have painted, preventing
     * alpha accumulation where the font intentionally overlaps cursive joins. */
    .ra-line .ra-word .ra-shaped-base-layer {
        display: inline;
    }
    .ra-line .ra-word .ra-shaped-token {
        display: none;
    }
    .ra-line.ra-chars .ra-shaped-base-layer {
        display: inline;
        color: var(--ra-base-color);
        opacity: var(--ra-unreached-opacity);
        transition: opacity var(--ra-char-reveal) var(--ra-easing);
    }
    .ra-line.ra-chars .ra-shaped-token {
        display: inline;
        color: var(--ra-base-color);
        /* The base layer owns all unreached ink. This layer contains only the
         * opaque reached/current overlays needed for independent highlighting. */
        opacity: 0;
        transition:
            opacity var(--ra-char-reveal) var(--ra-easing),
            color var(--ra-active-emphasis) var(--ra-easing);
    }
    .ra-line.ra-chars .ra-shaped-token:global(.reached) {
        opacity: 1;
    }
    .ra-line.ra-full-opacity.ra-chars .ra-shaped-token:global(.reached):not(:global(.active)) {
        /* Full mode already paints the complete word through its opaque base
         * silhouette. Retaining opaque reached overlays would paint completed
         * glyphs twice and make previous words look heavier than they do in
         * Dim/Hidden. Active tokens remain visible through the rule below. */
        opacity: 0;
    }
    .ra-line.ra-chars .ra-shaped-token:global(.active) {
        opacity: 1;
        color: var(--ra-highlight);
        /* SVG groups do not support text-shadow. Keep the established active
         * character glow on the one active paint group only. */
        filter: var(--ra-svg-glow);
    }
    .ra-line.ra-chars .ra-shaped-token[data-mark-only="true"]:global(.active) {
        /* Keep the fill highlight exact. A mark-sized glow has nowhere to land
         * except over the adjacent base letter, falsely reading as co-paint. */
        filter: none;
    }
    /* U+06E2 iqlab is display-only in the Hafs DigitalKhatt text. Its producer
     * token owns the meem sound, while the shaped token also contains the
     * written (silent) noon body and dot. In silent-omit mode those host paths
     * stay visible through the shared base silhouette but are not repainted by
     * the active/reached token overlay. */
    .ra-line.ra-omit-silent.ra-chars .ra-shaped-token .ra-silent-companion {
        /* Follow the token's reveal opacity, but never inherit its active
         * highlight colour. This makes the silent host appear at the sound's
         * time in Dim/Invisible modes without claiming that sound. */
        fill: var(--ra-base-color);
    }
    .ra-line.ra-omit-silent.ra-chars .ra-shaped-token[data-has-silent-companion="true"]:global(.active) {
        /* A group filter would cast the mini-meem's green glow across the
         * base-coloured noon paths. Keep the exact fills clean instead. */
        filter: none;
    }
    .ra-line.ra-full-opacity.ra-omit-silent.ra-chars .ra-shaped-token .ra-silent-companion {
        /* Full mode's base silhouette is already opaque; avoid double-painting
         * its antialiased edge while the mini-meem overlay remains active. */
        display: none;
    }
    .ra-line.ra-chars .ra-word-ink {
        position: absolute;
        inset: 0;
        z-index: 0;
        color: transparent;
        text-shadow: var(--ra-word-shadow);
        white-space: nowrap;
        pointer-events: none;
        /* Tie the stroke's strength to the upcoming-text level so it never
         *  dominates a faint fill (which read as inverted/dark text when the
         *  letters were Dim or Hidden). At Hidden (0) the outline vanishes too. */
        opacity: var(--ra-unreached-opacity);
    }
    .ra-line.ra-chars span.ra-char {
        position: relative;
        z-index: 1;
        opacity: var(--ra-unreached-opacity);
        transition:
            opacity var(--ra-char-reveal) var(--ra-easing),
            color var(--ra-active-emphasis) var(--ra-easing),
            text-shadow var(--ra-active-emphasis) var(--ra-easing);
    }
    .ra-line.ra-chars span.ra-char:global(.reached) {
        opacity: 1; /* visited words stay fully visible (no reached-dim) */
    }
    .ra-line.ra-chars span.ra-char:global(.active) {
        opacity: 1;
        color: var(--ra-highlight);
        /* Glow only — never re-stroke the active letter. */
        text-shadow: var(--ra-glow);
    }

    /* Char mode: only the ACTIVE word is shown letter-by-letter. Previous and
     *  upcoming words fall back to the WHOLE-WORD silhouette so they read like
     *  word mode — splitting a word into per-letter spans breaks Arabic cursive
     *  joining (isolated forms with visible seams, the "letter borders"). The
     *  `.ra-word-ink` underlay is already the full word as one joined text node,
     *  so for non-active words we fill it (make it the visible text) and hide the
     *  per-letter spans. Independent of the upcoming-visibility (eye) setting —
     *  the ink still carries the unreached opacity for dimming. */
    .ra-line.ra-chars .ra-word:not(:global(.active)) .ra-word-ink {
        color: var(--ra-base-color);
    }
    .ra-line.ra-chars .ra-word:not(:global(.active)) span.ra-char {
        opacity: 0;
    }
    /* Already-recited words stay fully visible, like word-mode reached. */
    .ra-line.ra-chars .ra-word:global(.reached) .ra-word-ink {
        opacity: 1;
    }

    /* Non-recited decorator marks (waqf stop signs, rub-el-hizb ۞, sajdah ۩) —
     *  each a standalone glyph anchored to an invisible WORD JOINER (the same
     *  trick `build-structure` uses for combining letters). They carry no letters,
     *  so they never take the per-letter reveal highlight; rendered on a
     *  zero-advance joiner each shapes into its own run with an independent
     *  colour and adds NO width. */
    .ra-line .ra-word .ra-decorator {
        position: relative;
        z-index: 2; /* above the char/ink layers so the mark always paints on top */
        pointer-events: none;
        color: var(--ra-base-color);
        text-shadow: var(--ra-word-shadow);
        transition:
            opacity var(--ra-word-reveal) var(--ra-easing),
            color var(--ra-active-emphasis) var(--ra-easing);
    }
    /* Stop signs are discrete recitation events, not sounding text. Their
     * reveal at the anchor boundary and pause highlight both snap immediately. */
    .ra-line .ra-word .ra-decorator--waqf {
        transition: none;
    }
    /* Waqf (stop) sign — opacity follows the same reveal model as the text it
     *  rides. Word mode dims the whole `.ra-word`, so the child mark just
     *  composites under it (no own opacity — an own one would double-dim) and
     *  reveals with the word. Char mode keeps the word box opaque and dims the
     *  letters, so the mark dims itself and reveals (via the sweep's `.revealed`)
     *  only once recitation has passed its last letter. */
    .ra-line.ra-chars .ra-word .ra-decorator--waqf {
        opacity: var(--ra-unreached-opacity);
    }
    .ra-line.ra-chars .ra-word .ra-decorator--waqf:global(.revealed) {
        opacity: 1;
    }
    /* Lit while a pause holds on this word — colour the run; only the mark shows. */
    .ra-line .ra-word .ra-decorator--waqf:global(.waqf-active) {
        color: var(--ra-highlight);
    }

    /* One-frame transition kill across a granularity / layout re-page so words
     *  snap to their new-mode opacity instead of all fading together (the
     *  letter→word flash). */
    .ra-line.ra-no-transition .ra-word,
    .ra-line.ra-no-transition.ra-chars .ra-word,
    .ra-line.ra-no-transition.ra-chars span.ra-char,
    .ra-line.ra-no-transition.ra-chars .ra-shaped-base-layer,
    .ra-line.ra-no-transition.ra-chars .ra-shaped-token,
    .ra-line.ra-no-transition.ra-chars .ra-shaped-svg,
    .ra-line.ra-no-transition.ra-chars .ra-shaped-base-text,
    .ra-line.ra-no-transition.ra-chars .ra-word-ink {
        transition: none;
    }

    @media (prefers-reduced-motion: reduce) {
        .ra-word,
        .ra-line.ra-chars span.ra-char,
        .ra-line.ra-chars .ra-shaped-base-layer,
        .ra-line.ra-chars .ra-shaped-token,
        .ra-line.ra-chars .ra-shaped-svg,
        .ra-line .ra-word .ra-decorator,
        .ra-ayah-marker {
            transition: none;
        }
    }
</style>
