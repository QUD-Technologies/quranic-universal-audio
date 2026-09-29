<script lang="ts">
    /**
     * The open book: one or two page slots (right page first — the book is
     * RTL whatever the UI locale) plus, during a turn, a leaf that rotates
     * about the spine. The leaf's pages are static copies (`inert`); only the
     * slot pages carry live highlight state.
     */
    import MushafPage from './MushafPage.svelte';
    import type { MushafLayout, WordIndex } from './layout';
    import type { PageMetrics } from './fit';

    export interface Leaf {
        /** Page on the leaf's face before / after the turn (0 = blank). */
        front: number;
        back: number;
        /** +1 forward (the left page turns over to the right), −1 back. */
        dir: 1 | -1;
    }

    interface Props {
        layout: MushafLayout;
        words: WordIndex;
        metrics: PageMetrics;
        fontOf: (_page: number) => number;
        playable: Set<number>;
        fontStack: string;
        /** Slot pages in visual order right → left. */
        pages: number[];
        leaf: Leaf | null;
        onleafend: () => void;
    }

    let { layout, words, metrics, fontOf, playable, fontStack, pages, leaf, onleafend }: Props = $props();
</script>

{#snippet page(p: number)}
    <MushafPage
        page={p}
        {layout}
        {words}
        fontPx={fontOf(p)}
        pitchPx={metrics.pitchPx}
        columnPx={metrics.columnPx}
        widthPx={metrics.pageWidthPx}
        heightPx={metrics.pageHeightPx}
        {playable}
        {fontStack}
    />
{/snippet}

<div class="mv-book" class:single={pages.length === 1} dir="rtl" style:--mv-page-w="{metrics.pageWidthPx}px">
    {#each pages as p, i (i)}
        <div class="mv-slot" class:right={i === 0 && pages.length === 2} class:left={i === 1}>
            {@render page(p)}
        </div>
    {/each}
    {#if leaf}
        <div
            class="mv-leaf"
            class:fwd={leaf.dir === 1}
            class:bwd={leaf.dir === -1}
            class:single={pages.length === 1}
            inert
            aria-hidden="true"
            onanimationend={onleafend}
        >
            <div class="mv-face">{#if leaf.front}{@render page(leaf.front)}{/if}</div>
            <div class="mv-face mv-back">{#if leaf.back}{@render page(leaf.back)}{:else}<div class="mv-blank"></div>{/if}</div>
        </div>
    {/if}
</div>

<style>
    .mv-book {
        position: relative;
        display: flex;
        gap: 2px;
        perspective: 2400px;
    }
    /* Spine shade on the inner edge of each page. */
    .mv-slot { position: relative; }
    .mv-slot.right::after,
    .mv-slot.left::after {
        content: '';
        position: absolute;
        inset-block: 0;
        width: 7%;
        pointer-events: none;
    }
    .mv-slot.right::after { left: 0; background: linear-gradient(to right, var(--mushaf-spine), transparent); }
    .mv-slot.left::after { right: 0; background: linear-gradient(to left, var(--mushaf-spine), transparent); }

    .mv-leaf {
        position: absolute;
        top: 0;
        width: var(--mv-page-w);
        height: 100%;
        transform-style: preserve-3d;
        animation-duration: 620ms;
        animation-timing-function: cubic-bezier(0.35, 0.1, 0.2, 1);
        animation-fill-mode: forwards;
        z-index: 2;
    }
    /* Forward: the left page lifts about the spine (its right edge) and lands on the right. */
    .mv-leaf.fwd { left: 0; transform-origin: right center; animation-name: turn-fwd; }
    /* Back: the right page lifts about the spine (its left edge) and lands on the left. */
    .mv-leaf.bwd { right: 0; transform-origin: left center; animation-name: turn-bwd; }
    /* One page: the old page lifts away and fades — there is no facing page. */
    .mv-leaf.single { left: 0; right: auto; transform-origin: right center; animation-name: turn-away; }
    .mv-leaf.single.bwd { transform-origin: left center; animation-name: turn-away-bwd; }

    .mv-face {
        position: absolute;
        inset: 0;
        backface-visibility: hidden;
        box-shadow: var(--shadow-pop);
    }
    .mv-back { transform: rotateY(180deg); }
    .mv-blank { width: 100%; height: 100%; background: var(--mushaf-paper); }

    @keyframes turn-fwd { from { transform: rotateY(0deg); } to { transform: rotateY(180deg); } }
    @keyframes turn-bwd { from { transform: rotateY(0deg); } to { transform: rotateY(-180deg); } }
    @keyframes turn-away { from { transform: rotateY(0deg); opacity: 1; } to { transform: rotateY(110deg); opacity: 0; } }
    @keyframes turn-away-bwd { from { transform: rotateY(0deg); opacity: 1; } to { transform: rotateY(-110deg); opacity: 0; } }
</style>
