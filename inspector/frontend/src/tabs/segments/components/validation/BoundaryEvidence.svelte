<script lang="ts">
    /**
     * BoundaryEvidence — the evidence strip on a Hidden Pause / Missed Waqf /
     * False Split / Unmarked Wasl card. Reads the item's `boundary` payload
     * (offline sidecar pass-through) and renders: the agreeing axes as chips,
     * the proposed cursor(s) as m:ss.mmm (one line per cut on Hidden Pause and
     * Missed Waqf, which share the cuts shape), the gap, the word and its
     * final-letter class, and the score. A False Split row also states that the merge target is the
     * next segment; an Unmarked Wasl row states the join was read through and
     * should be marked waṣl.
     */
    import { i18n } from '../../../../lib/i18n/locale.svelte';
    import * as m from '../../../../lib/paraglide/messages';
    import type {
        SegValAnyItem,
        SegValFalseSplitItem,
        SegValHiddenPauseCut,
        SegValHiddenPauseItem,
        SegValUnmarkedWaslItem,
    } from '../../../../lib/types/generated/schemas';

    /** false_split and unmarked_wasl carry the same next-join boundary payload. */
    type NextJoinItem = SegValFalseSplitItem | SegValUnmarkedWaslItem;

    let { category, item }: { category: string; item: SegValAnyItem } = $props();

    const MS_PER_SEC = 1000;
    const SEC_PER_MIN = 60;

    function fmtCursor(ms: number): string {
        const total = Math.max(0, Math.floor(ms));
        const mins = Math.floor(total / MS_PER_SEC / SEC_PER_MIN);
        const secs = Math.floor(total / MS_PER_SEC) % SEC_PER_MIN;
        const millis = total % MS_PER_SEC;
        return `${mins}:${String(secs).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
    }

    interface Line {
        key: string;
        axes: string[];
        cursorMs: number | null;
        gapMs: number | null;
        word: string | null;
        finalClass: string | null;
        verseEnd: boolean;
        score: number | null;
    }

    function hiddenPauseLines(it: SegValHiddenPauseItem): Line[] {
        const b = it.boundary;
        const cuts: SegValHiddenPauseCut[] = b.cuts ?? [];
        if (cuts.length > 0) {
            return cuts.map((c, i) => ({
                key: `cut-${i}`,
                axes: c.axes ?? [],
                cursorMs: c.cursor_ms ?? null,
                gapMs: c.gap_ms ?? null,
                word: c.word ?? null,
                finalClass: c.final_class ?? null,
                verseEnd: c.verse_end ?? false,
                score: c.score ?? null,
            }));
        }
        return (b.cursors ?? []).map((ms, i) => ({
            key: `cursor-${i}`,
            axes: [],
            cursorMs: ms,
            gapMs: null,
            word: null,
            finalClass: null,
            verseEnd: false,
            score: null,
        }));
    }

    function nextJoinLine(it: NextJoinItem): Line {
        const b = it.boundary;
        return {
            key: 'end',
            axes: b.axes ?? [],
            cursorMs: null,
            gapMs: b.gap_ms ?? null,
            word: b.word ?? null,
            finalClass: b.final_class ?? null,
            verseEnd: b.verse_end ?? false,
            score: null,
        };
    }

    const isFalseSplit = $derived(category === 'false_split');
    const isUnmarkedWasl = $derived(category === 'unmarked_wasl');
    const isNextJoin = $derived(isFalseSplit || isUnmarkedWasl);
    const lines = $derived.by((): Line[] => {
        if (isNextJoin) return [nextJoinLine(item as NextJoinItem)];
        return hiddenPauseLines(item as SegValHiddenPauseItem);
    });
    const totalScore = $derived((item as SegValHiddenPauseItem).boundary?.score ?? 0);
    const isWasl = $derived(isNextJoin && ((item as NextJoinItem).boundary?.is_wasl ?? false));
    const scoreLabel = $derived((i18n.locale, m.segments_boundary_score({ score: String(totalScore) })));
    const mergeNextLabel = $derived((i18n.locale, m.segments_boundary_merge_next()));
    const readThroughLabel = $derived((i18n.locale, m.segments_boundary_read_through()));
    const verseEndLabel = $derived((i18n.locale, m.segments_boundary_verse_end()));
    const waslLabel = $derived((i18n.locale, m.segments_boundary_wasl()));
</script>

<div class="bx" data-boundary-category={category}>
    {#each lines as line (line.key)}
        <div class="bx-line">
            {#each line.axes as axis (axis)}
                <span class="bx-chip bx-axis">{axis}</span>
            {/each}
            {#if line.cursorMs != null}
                <span class="bx-fact bx-cursor">{m.segments_boundary_cut_at({ time: fmtCursor(line.cursorMs) })}</span>
            {/if}
            {#if line.gapMs != null}
                <span class="bx-fact">{m.segments_boundary_gap({ ms: String(line.gapMs) })}</span>
            {/if}
            {#if line.word}
                <span class="bx-fact bx-word" dir="rtl">{line.word}</span>
            {/if}
            {#if line.finalClass}
                <span class="bx-fact">{m.segments_boundary_final_class({ cls: line.finalClass })}</span>
            {/if}
            {#if line.verseEnd}
                <span class="bx-chip">{verseEndLabel}</span>
            {/if}
            {#if line.score != null && lines.length > 1}
                <span class="bx-fact bx-score">{m.segments_boundary_score({ score: String(line.score) })}</span>
            {/if}
        </div>
    {/each}
    <div class="bx-line bx-summary">
        {#if isFalseSplit}
            <span class="bx-fact">{mergeNextLabel}</span>
        {:else if isUnmarkedWasl}
            <span class="bx-fact">{readThroughLabel}</span>
        {/if}
        {#if isWasl}
            <span class="bx-chip">{waslLabel}</span>
        {/if}
        <span class="bx-fact bx-score">{scoreLabel}</span>
    </div>
</div>

<style>
    .bx {
        display: flex;
        flex-direction: column;
        gap: 3px;
        margin: 2px 0 6px;
        font-size: 11px;
        font-family: var(--font-mono);
        color: var(--text-secondary);
    }
    .bx-line {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 6px;
    }
    .bx-chip {
        display: inline-flex;
        align-items: center;
        height: 18px;
        padding: 0 7px;
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: 999px;
        white-space: nowrap;
    }
    .bx-axis {
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .bx-fact {
        white-space: nowrap;
    }
    .bx-word {
        font-family: inherit;
        font-size: 13px;
    }
    .bx-cursor,
    .bx-score {
        color: var(--text-primary);
    }
</style>
