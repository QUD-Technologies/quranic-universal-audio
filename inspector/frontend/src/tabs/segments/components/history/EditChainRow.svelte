<script lang="ts">
    /**
     * SplitChainRow — collapsed split-chain card (root → N leaves).
     *
     * Lays out a single before card (the original segment) on the left and
     * N after cards (the final leaves) on the right, with the shared
     * HistoryArrows column between them.
     *
     * The waveform sub-range is the union of root ± leaf boundaries. When
     * any leaf exceeds the root range the chain is "expanded" and the
     * canvas renders against the wider range via `_splitHL.wfStart/wfEnd`
     * (passed through SegmentRow's reactive splitHL prop).
     */

    import { editGate } from '../../../../lib/actions/editGate';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import type { Segment } from '../../../../lib/types/view-models';
    import { surahOptionText } from '../../../../lib/utils/surah-info';
    import {
        computeChainLeafSnaps,
        type EditChain,
        formatHistDate,
        getChainBatchIds,
        type HistorySnapshot,
        SHORT_LABELS,
        snapToSeg,
    } from '../../stores/history';
    import { undoPending } from '../../stores/undo-pending';
    import type { SplitHighlight, TrimHighlight } from '../../types/segments-waveform';
    import type { PreviewPlaybackContext } from '../../utils/playback/preview';
    import { onChainUndoClick, onPendingOpsDiscard } from '../../utils/save/undo';
    import { classifiedIssuesOf } from '../../utils/validation/classified-issues';
    import SegmentRow from '../list/SegmentRow.svelte';
    import GuideFlagButton from './GuideFlagButton.svelte';
    import HistoryArrows from './HistoryArrows.svelte';
    import JoinVerdicts from './JoinVerdicts.svelte';

    // Props ------------------------------------------------------------------

    export let chain: EditChain;
    export let previewCtx: PreviewPlaybackContext | undefined = undefined;
    /** `'history'` (default) shows Undo for any saved batches in the chain.
     *  `'preview'` (set by SavePreview) suppresses Undo — that view is for
     *  reviewing pending edits, not reverting prior saves. */
    export let mode: 'preview' | 'history' = 'history';
    /** Guide examples reuse the chain renderer without saved-date or edit controls. */
    export let variant: 'default' | 'guide' = 'default';

    // Derived ----------------------------------------------------------------

    $: rootSnap = chain.rootSnap ?? null;
    $: leafSnaps = computeChainLeafSnaps(chain);
    $: chapter = chain.rootBatch?.chapter ?? null;
    $: chainBatchIds = getChainBatchIds(chain);
    // Dev-only guide flagging — flatten the chain's ops for GuideFlagButton.
    $: chainOps = chain.ops.map((co) => co.op);
    /** Op ids whose enclosing batch is unsaved (`batch_id == null`). The
     *  save-preview Discard button hands these to `onPendingOpsDiscard`
     *  so the entire pending chain reverts atomically. */
    $: pendingOpIds = chain.ops
        .filter(({ batch }) => !batch.batch_id)
        .map(({ op }) => op.op_id);

    // Compute waveform range (may exceed root when a leaf went outside).
    $: wfRange = (() => {
        if (!rootSnap) return { wfStart: 0, wfEnd: 0, expanded: false };
        let wfStart = rootSnap.time_start;
        let wfEnd = rootSnap.time_end;
        for (const ls of leafSnaps) {
            if (ls.time_start < wfStart) wfStart = ls.time_start;
            if (ls.time_end > wfEnd) wfEnd = ls.time_end;
        }
        const expanded = wfStart < rootSnap.time_start || wfEnd > rootSnap.time_end;
        return { wfStart, wfEnd, expanded };
    })();

    $: chainOpType = chain.ops[0]?.op.op_type;
    $: isSplit = chainOpType === 'split_segment';
    $: isMerge = chainOpType === 'merge_segments';

    // Root card gets splitHL only when the range is expanded (and only for splits)
    $: rootSplitHL = (() => {
        if (!isSplit || !rootSnap || !wfRange.expanded) return null;
        const hl: SplitHighlight = {
            wfStart: wfRange.wfStart,
            wfEnd: wfRange.wfEnd,
            hlStart: rootSnap.time_start,
            hlEnd: rootSnap.time_end,
        };
        return hl;
    })();

    // Each leaf card gets a splitHL pointing to its own range within wfRange
    // (only for splits). Applied to every leaf — adjusted or not — so the
    // child's portion always renders against the wider parent peak with its
    // own range highlighted. Trim delta (when present) stacks on top, clipped
    // to the leaf's bounds so it doesn't bleed onto siblings.
    function leafSplitHL(leaf: HistorySnapshot): SplitHighlight | null {
        if (!isSplit || !rootSnap) return null;
        return {
            wfStart: wfRange.wfStart,
            wfEnd: wfRange.wfEnd,
            hlStart: leaf.time_start,
            hlEnd: leaf.time_end,
        };
    }

    // Merge highlight for the result card
    $: leafMergeHL = (() => {
        if (!isMerge) return null;
        const mergeOp = chain.ops.find(co => co.op.op_type === 'merge_segments')?.op;
        if (!mergeOp || !mergeOp.targets_before || mergeOp.targets_before.length < 2) return null;
        const b = mergeOp.targets_before as HistorySnapshot[];
        const hlSnap = mergeOp.merge_direction === 'prev' ? b[1]! : b[0]!;
        const mergePoint = mergeOp.merge_direction === 'prev' ? hlSnap.time_start : hlSnap.time_end;
        return { mergePoint };
    })();

    // Earliest snapshot in the chain carrying this leaf's uid (excluding the leaf itself).
    // For a split→adjust chain that's the split's targets_after entry (the leaf's
    // initial post-split state); for a plain adjust chain it's the first
    // targets_before. chain.ops is already chronological.
    function leafBaselineSnap(leaf: HistorySnapshot): HistorySnapshot | null {
        if (!leaf.segment_uid) return null;
        for (const { op } of chain.ops) {
            const before = ((op.targets_before || []) as HistorySnapshot[]).find(s => s.segment_uid === leaf.segment_uid);
            if (before) return before;
            const after = ((op.targets_after || []) as HistorySnapshot[]).find(s => s.segment_uid === leaf.segment_uid);
            if (after && after !== leaf) return after;
        }
        return null;
    }

    // Per-leaf trim highlight: only the leaf actually targeted by an adjust gets
    // the green delta painted, against its baseline (post-split or pre-adjust)
    // state — not against an arbitrary other-leaf's range. `clipStart`/`clipEnd`
    // pin the delta paint to the leaf's own bounds so it doesn't bleed across
    // the wider split-chain canvas onto sibling leaves' portions.
    function leafTrimHL(leaf: HistorySnapshot): TrimHighlight | null {
        const wasAdjusted = chain.ops.some(({ op }) => {
            if (op.op_type !== 'boundary_adjustment' && op.op_type !== 'trim_segment') return false;
            return ((op.targets_after || []) as HistorySnapshot[]).some(s => s.segment_uid === leaf.segment_uid);
        });
        if (!wasAdjusted) return null;
        const baseline = leafBaselineSnap(leaf);
        if (!baseline) return null;
        if (baseline.time_start === leaf.time_start && baseline.time_end === leaf.time_end) return null;
        return {
            color: 'green',
            otherStart: baseline.time_start,
            otherEnd: baseline.time_end,
            clipStart: leaf.time_start,
            clipEnd: leaf.time_end,
        };
    }

    // Leaf cards need a wider seg range when expanded so the canvas
    // renders against the chain's union. The IntersectionObserver callback
    // uses splitHL.wfStart/wfEnd to substitute the sub-range
    // (see waveform/index.ts).
    function rootSegForCard(s: HistorySnapshot): Segment {
        return snapToSeg(s, chapter);
    }

    // Validation-delta badges (resolved / regressed issues over the chain).
    // Reads `classified_issues` directly off saved snapshots; absent on
    // unsaved chains (next save+validate cycle resurfaces any pending state).
    $: valDelta = (() => {
        const beforeIssues = new Set<string>();
        if (rootSnap) {
            for (const i of classifiedIssuesOf(rootSnap)) beforeIssues.add(i);
        }
        const afterIssues = new Set<string>();
        for (const ls of leafSnaps) {
            for (const i of classifiedIssuesOf(ls)) afterIssues.add(i);
        }
        const improved = [...beforeIssues].filter((i) => !afterIssues.has(i));
        const regressed = [...afterIssues].filter((i) => !beforeIssues.has(i));
        return { improved, regressed };
    })();

    // Arrow column refs ------------------------------------------------------
    let beforeCardEls: (HTMLElement | undefined)[] = [];
    let afterCardEls: (HTMLElement | undefined)[] = [];
    let arrowsBefore: HTMLElement[] = [];
    let arrowsAfter: HTMLElement[] = [];

    $: chainBadgeText = (() => {
        if (chainOpType === 'split_segment') return m.segments_history_chain_split_badge({ n: leafSnaps.length });
        if (chainOpType === 'merge_segments') return m.segments_history_chain_merge_badge({ n: leafSnaps.length });
        return m.segments_history_chain_multi_badge({ n: leafSnaps.length });
    })();

    $: {
        arrowsBefore = beforeCardEls.filter((e): e is HTMLElement => !!e);
        arrowsAfter = afterCardEls.filter((e): e is HTMLElement => !!e);
    }

    function handleChainUndoClick(): void {
        void onChainUndoClick(chainBatchIds, chapter);
    }
    $: chainUndoKey = `chain:${chainBatchIds.join(',')}`;
    $: isChainUndoing = $undoPending.has(chainUndoKey);

    function handleChainDiscardClick(e: MouseEvent): void {
        if (chapter == null || pendingOpIds.length === 0) return;
        const btn = e.currentTarget as HTMLButtonElement;
        onPendingOpsDiscard(chapter, pendingOpIds, btn);
    }

    $: chainUndoButtonLabel = tr($localeStore, isChainUndoing ? m.segments_history_op_undoing_button() : m.segments_history_op_undo_button());
    $: discardButtonLabel = tr($localeStore, m.segments_history_discard_button());
    $: allDeletedPlaceholder = tr($localeStore, m.segments_history_all_deleted_placeholder());
    $: chainDateLabel = tr($localeStore, formatHistDate(chain.latestDate));
</script>

<div class="seg-history-batch seg-history-split-chain">
    <div class="seg-history-batch-header">
        {#if variant !== 'guide'}
            <span class="seg-history-batch-time">{chainDateLabel}</span>
        {/if}
        {#if chapter != null}
            <span class="seg-history-batch-chapter">{surahOptionText(chapter)}</span>
        {/if}
        <span class="seg-history-batch-ops-count">{chainBadgeText}</span>
        {#each valDelta.improved as cat}
            <span class="seg-history-val-delta improved">&minus;{SHORT_LABELS[cat] || cat}</span>
        {/each}
        {#each valDelta.regressed as cat}
            <span class="seg-history-val-delta regression">+{SHORT_LABELS[cat] || cat}</span>
        {/each}
        {#if variant !== 'guide' && mode === 'history' && chainBatchIds.length > 0}
            <button
                class="btn btn-sm seg-history-undo-btn"
                use:editGate
                on:click|stopPropagation={handleChainUndoClick}
                disabled={isChainUndoing}
            >{chainUndoButtonLabel}</button>
        {/if}
        {#if variant !== 'guide' && pendingOpIds.length > 0 && chapter != null}
            <button
                class="btn btn-sm seg-history-undo-btn"
                use:editGate
                on:click|stopPropagation={handleChainDiscardClick}
            >{discardButtonLabel}</button>
        {/if}
        {#if variant !== 'guide' && mode === 'history' && chainBatchIds.length > 0}
            <GuideFlagButton {chapter} batchId={chainBatchIds[0] ?? null} group={chainOps} />
        {/if}
    </div>

    <div class="seg-history-batch-body">
        <div class="seg-history-diff">
            <div class="seg-history-before">
                {#if rootSnap}
                    <div bind:this={beforeCardEls[0]}>
                        <SegmentRow
                            seg={rootSegForCard(rootSnap)}
                            readOnly={true}
                            showChapter={true}
                            showPlayBtn={true}
                            mode="history"
                            instanceRole="history"
                            splitHL={rootSplitHL}
                            opId={chain.ops[0]?.op.op_id ?? null}
                            {previewCtx}
                        />
                    </div>
                {/if}
            </div>

            <div class="seg-history-arrows">
                <HistoryArrows
                    beforeCards={arrowsBefore}
                    afterCards={arrowsAfter}
                    emptyEl={null}
                />
            </div>

            <div class="seg-history-after">
                {#if leafSnaps.length === 0}
                    <div class="seg-history-empty">{allDeletedPlaceholder}</div>
                {:else}
                    {#each leafSnaps as leaf, i (leaf.segment_uid ?? i)}
                        <div bind:this={afterCardEls[i]}>
                            <SegmentRow
                                seg={snapToSeg(leaf, chapter)}
                                readOnly={true}
                                showChapter={true}
                                showPlayBtn={true}
                                mode="history"
                                instanceRole="history"
                                splitHL={leafSplitHL(leaf)}
                                mergeHL={leafMergeHL}
                                trimHL={leafTrimHL(leaf)}
                                opId={chain.ops[0]?.op.op_id ?? null}
                                {previewCtx}
                            />
                            <JoinVerdicts answers={leaf.join_verdicts} />
                        </div>
                    {/each}
                {/if}
            </div>
        </div>
    </div>
</div>
