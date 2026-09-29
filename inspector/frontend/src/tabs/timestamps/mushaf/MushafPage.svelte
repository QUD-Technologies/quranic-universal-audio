<script lang="ts">
    /**
     * One mushaf page — static markup only. Highlight / visibility state is
     * applied imperatively by MushafView (classes on `[data-w]` spans), so a
     * frame of playback never re-renders a page.
     */
    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';
    import {
        isVerseMarker,
        LINE_AYAH,
        LINE_BASMALLAH,
        LINE_SURAH,
        type MushafLayout,
        type WordIndex,
    } from './layout';
    import { MIN_WORD_GAP_EM } from './fit';
    import { SURAH_FRAME_FAMILY, SURAH_NAME_FAMILY } from './fonts';

    /** Surah-header frame glyph (PUA) and its advance in em, for the stretch. */
    const FRAME_GLYPH = '';
    const FRAME_ADVANCE_EM = 8.05;
    /** Basmallah = the first four words of al-Fatiha. */
    const BASMALLAH_IDS = [1, 2, 3, 4];

    interface Props {
        page: number;
        layout: MushafLayout;
        words: WordIndex;
        fontPx: number;
        pitchPx: number;
        columnPx: number;
        widthPx: number;
        heightPx: number;
        /** Surahs the reciter has — their words are clickable. */
        playable: Set<number>;
        fontStack: string;
    }

    let { page, layout, words, fontPx, pitchPx, columnPx, widthPx, heightPx, playable, fontStack }: Props = $props();

    const lines = $derived(layout.pages[page - 1] ?? []);
    const firstId = $derived(layout.pageFirstWord[page - 1] ?? 0);
    const headerSurah = $derived(words.surah[firstId] ?? 0);
    const juz = $derived(layout.juz[page - 1] ?? 0);
    const hizb = $derived(layout.hizb[page - 1] ?? 0);
    const numberFmt = $derived(new Intl.NumberFormat(i18n.locale === 'ar' ? 'ar-EG' : 'en'));
    const basmallah = $derived(BASMALLAH_IDS.map((id) => words.text[id]).join(' '));
    /** Pages 1–2 carry fewer lines; the mushaf centres them on the page. */
    const short = $derived(lines.length < 15);
    const frameScale = $derived(columnPx / (pitchPx * FRAME_ADVANCE_EM));

    function ligature(surah: number): string {
        return `surah${String(surah).padStart(3, '0')}`;
    }

    function range(a: number, b: number): number[] {
        const out: number[] = [];
        for (let id = a; id <= b; id++) out.push(id);
        return out;
    }

    /** Re-evaluate a message on locale switch (reads the locale rune). */
    const L = (s: string): string => (i18n.locale, s);
</script>

<article
    class="mp-page"
    data-page={page}
    aria-label={L(m.ts_mushaf_page_aria({ n: numberFmt.format(page) }))}
    style:width="{widthPx}px"
    style:height="{heightPx}px"
    style:--mp-font="{fontPx}px"
    style:--mp-pitch="{pitchPx}px"
    style:--mp-column="{columnPx}px"
    style:--mp-gap="{MIN_WORD_GAP_EM}em"
    style:--mp-text-font={fontStack}
>
    <header class="mp-head">
        <span class="mp-head-surah" style:font-family="'{SURAH_NAME_FAMILY}', serif">{headerSurah ? ligature(headerSurah) : ''}</span>
        <span class="mp-head-meta">
            {L(m.ts_mushaf_juz({ n: numberFmt.format(juz) }))}
            <span aria-hidden="true">·</span>
            {L(m.ts_mushaf_hizb({ n: numberFmt.format(hizb) }))}
        </span>
    </header>

    <div class="mp-lines" class:short dir="rtl">
        {#each lines as [kind, centered, a, b], li (li)}
            {#if kind === LINE_AYAH}
                <div class="mp-line" class:centered={centered === 1}>
                    {#each range(a, b) as id (id)}
                        {@const text = words.text[id] ?? ''}
                        <span
                            class="mp-w"
                            class:mp-mark={isVerseMarker(text)}
                            class:mp-inert={!playable.has(words.surah[id] ?? 0)}
                            data-w={id}
                        >{text}</span>
                    {/each}
                </div>
            {:else if kind === LINE_SURAH}
                <div class="mp-line mp-surah">
                    <span
                        class="mp-frame"
                        aria-hidden="true"
                        style:font-family="'{SURAH_FRAME_FAMILY}', serif"
                        style:transform="translate(-50%, -50%) scaleX({frameScale})"
                    >{FRAME_GLYPH}</span>
                    <span class="mp-surah-name" style:font-family="'{SURAH_NAME_FAMILY}', serif">{ligature(a)}</span>
                </div>
            {:else if kind === LINE_BASMALLAH}
                <div class="mp-line centered mp-basm">{basmallah}</div>
            {/if}
        {/each}
    </div>

    <footer class="mp-num">{numberFmt.format(page)}</footer>
</article>

<style>
    .mp-page {
        position: relative;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: calc(var(--mp-font) * 0.9) 0;
        background: var(--mushaf-paper);
        color: var(--text-primary);
        font-size: var(--mp-font);
    }
    .mp-head,
    .mp-num {
        flex: 0 0 calc(var(--mp-pitch) * 0.8);
        width: var(--mp-column);
        margin: 0; /* the app's global header/footer margins would push the rows off the page */
        display: flex;
        align-items: center;
        color: var(--text-muted);
        font-family: var(--font-ui, inherit);
        font-size: max(11px, calc(var(--mp-font) * 0.42));
        line-height: 1;
    }
    .mp-head {
        justify-content: space-between;
        direction: rtl;
        border-block-end: 1px solid var(--border-quiet);
    }
    .mp-head-surah {
        font-size: calc(var(--mp-font) * 0.9);
        color: var(--text-secondary);
    }
    .mp-head-meta { display: inline-flex; gap: 0.4em; }
    .mp-num { justify-content: center; }

    .mp-lines {
        flex: 1 1 auto;
        width: var(--mp-column);
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        font-family: var(--mp-text-font);
    }
    .mp-lines.short { justify-content: center; }

    .mp-line {
        position: relative;
        height: var(--mp-pitch);
        display: flex;
        align-items: center;
        justify-content: space-between;
        column-gap: var(--mp-gap);
        white-space: nowrap;
        line-height: 1;
    }
    .mp-line.centered { justify-content: center; }
    .mp-basm { font-feature-settings: 'basm'; }

    .mp-w {
        position: relative;
        z-index: 1;
        cursor: pointer;
        transition: color var(--t-fast), opacity var(--t-fast);
    }
    .mp-w.mp-inert { cursor: default; }
    .mp-w:not(.mp-inert):hover { color: var(--accent); }
    .mp-mark { color: var(--text-secondary); }

    /* Imperative state (MushafView): the recited word, the hidden ones. */
    .mp-w:global(.is-active) { color: var(--accent); }
    .mp-w:global(.is-hidden) { visibility: hidden; }

    /* Current-verse band, positioned per line by MushafView. */
    .mp-line :global(.mp-band) {
        position: absolute;
        inset-block: 12%;
        border-radius: var(--r-2);
        background: var(--accent-tint-soft);
        pointer-events: none;
        z-index: 0;
    }

    .mp-surah { justify-content: center; }
    .mp-frame,
    .mp-surah-name {
        position: absolute;
        top: 50%;
        left: 50%;
        line-height: 1;
        white-space: nowrap;
    }
    .mp-frame {
        font-size: var(--mp-pitch);
        color: var(--text-muted);
    }
    .mp-surah-name {
        transform: translate(-50%, -50%);
        font-size: calc(var(--mp-pitch) * 0.72);
        color: var(--text-primary);
    }
</style>
