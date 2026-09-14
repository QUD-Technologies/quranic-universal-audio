<script lang="ts">
    import { createEventDispatcher } from 'svelte';

    import { editGate } from '../../../../lib/actions/editGate';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import type { SegValAutoFix, SegValMissingWordsItem } from '../../../../lib/types/generated/schemas';
    import type { Segment } from '../../../../lib/types/view-models';
    import {
        getAdjacentSegments,
        getChapterSegments,
        getSegByChapterIndex,
        segAllData,
    } from '../../stores/chapter';
    import { segConfig } from '../../stores/config';
    import {
        dirtyTick,
        getChapterOpsSnapshot,
    } from '../../stores/dirty';
    import { splitGroupIndex } from '../../stores/validation';
    import { autoFixMissingWord } from '../../utils/edit/auto-fix';
    import { getSplitGroupMembers } from '../../utils/validation/split-group';
    import SegmentRow from '../list/SegmentRow.svelte';

    const dispatch = createEventDispatcher<{ contextchange: boolean }>();

    // ---- Props ----
    export let item: SegValMissingWordsItem;

    // ---- State ----
    let showContext = false;

    $: ctxMode = $segConfig.accordionContext?.['missing_words'] ?? 'hidden';
    $: ctxDefaultOpen = ctxMode !== 'hidden';
    $: ctxNextOnly = ctxMode === 'next_only';

    // Segments in the gap range. Subscribes to segAllData so the list
    // re-derives after split/merge mutates indices in place. For each base
    // seg, include its full split-group (transitive descendants) so splits
    // inside the gap stay visible rather than snapping to whichever half
    // currently holds the original index. Preserves source order and dedupes
    // by UID — groups themselves are time-sorted, so relative position is
    // meaningful.
    //
    // No-match filler: the backend's `seg_indices` is built from the verse's
    // coverage map, which only ever contains segments that matched a
    // reference. A segment with an EMPTY `matched_ref` sitting between the
    // two bracketing segments of a gap is therefore invisible to it — and
    // that unmatched row is usually the whole reason the words went missing.
    // We walk the index span between consecutive bases and splice in every
    // unmatched segment we find, so the card renders the full contiguous
    // run (pair + every no-match row between them) instead of just the pair.
    // Matched in-between segments (a different verse interleaved into the
    // span) stay excluded — they belong to their own cards.
    //
    // Memoized by split-op-only fingerprint over (chapterSegs, splitOps, base
    // UIDs) — see GenericIssueCard for the invariant rationale. Counting only
    // split ops (not all batches/ops) keeps trim/ref-edit ops as cache hits.
    // Prevents a N-card accordion × M-gap-segment re-walk of every split op
    // on every reactive tick.
    $: segStoreTick = $segAllData;
    let _segRangeMemoKey = '';
    let _segRangeMemoResult: Segment[] = [];
    $: {
        void segStoreTick; void $dirtyTick;
        const chapterSegs = getChapterSegments(item.chapter);
        const splitIdx = $splitGroupIndex;
        const ops = getChapterOpsSnapshot(item.chapter);
        // UIDs are looked up per index and folded into the key so a fixup
        // that swaps the seg at a given index also invalidates the memo.
        // Render plan: every base index, plus the unmatched ("no match")
        // segments between consecutive bases. Built here rather than in the
        // body below so the filler rows also fingerprint into the memo key.
        const baseUids: string[] = [];
        const plan: Array<{ idx: number; filler: boolean }> = [];
        const idxs = item.seg_indices ?? [];
        for (let k = 0; k < idxs.length; k++) {
            const idx = idxs[k]!;
            const base = getSegByChapterIndex(item.chapter, idx);
            baseUids.push(base?.segment_uid ?? `_${idx}`);
            plan.push({ idx, filler: false });
            const nextIdx = idxs[k + 1];
            if (nextIdx == null || nextIdx <= idx + 1) continue;
            for (let j = idx + 1; j < nextIdx; j++) {
                const mid = getSegByChapterIndex(item.chapter, j);
                if (!mid || mid.matched_ref) continue;
                plan.push({ idx: j, filler: true });
                baseUids.push(mid.segment_uid ?? `_${j}`);
            }
        }
        // Bump on any reference-mutating op so memo busts when auto-fix /
        // edit-reference / merge / boundary-adjust change a base seg under
        // us. Committed-history closures arrive via $splitGroupIndex which
        // refreshes on every validate post-save, so the FE op log is the
        // only mutable input we need to fingerprint here.
        const MUTATING_OPS = new Set([
            'split_segment', 'merge_segments', 'edit_reference',
            'auto_fix_missing_word', 'boundary_adjustment', 'trim_segment',
        ]);
        let mutatingOpsCount = 0;
        for (const op of ops) {
            if (MUTATING_OPS.has(op.op_type)) mutatingOpsCount++;
        }
        // Count of distinct uids the committed index touches for this
        // chapter's bases — a fingerprint stand-in for the index identity.
        let committedTouches = 0;
        for (const uid of baseUids) {
            if (splitIdx[uid]) committedTouches++;
        }
        const key = `${item.chapter}|${idxs.join(',')}|${baseUids.join(',')}|${chapterSegs.length}|${committedTouches}|${mutatingOpsCount}`;
        if (key !== _segRangeMemoKey) {
            _segRangeMemoKey = key;
            const out: Segment[] = [];
            const seenUids = new Set<string>();
            const push = (s: Segment): void => {
                const segKey = s.segment_uid ?? `${s.chapter}:${s.index}`;
                if (seenUids.has(segKey)) return;
                seenUids.add(segKey);
                out.push(s);
            };
            for (const step of plan) {
                const base = getSegByChapterIndex(item.chapter, step.idx);
                if (!base) continue;
                // A no-match filler is never a split parent — render it as-is.
                if (step.filler) {
                    push(base);
                    continue;
                }
                const baseUid = base.segment_uid ?? null;
                const committed = baseUid != null ? splitIdx[baseUid] : undefined;
                const group = getSplitGroupMembers(baseUid, chapterSegs, committed, ops);
                const list = group.length > 0 ? group : [base];
                for (const s of list) push(s);
            }
            _segRangeMemoResult = out;
        }
    }
    $: segmentsInRange = _segRangeMemoResult;

    let _didAutoOpen = false;
    $: if (segmentsInRange.length > 0 && ctxDefaultOpen && !_didAutoOpen) {
        showContext = true;
        _didAutoOpen = true;
    }

    // Context neighbours: prev of first / next of last. Guard against the
    // neighbour being itself a split-group member (already rendered inline).
    $: prevSeg = ((): Segment | null => {
        if (!showContext || ctxNextOnly) return null;
        const first = segmentsInRange[0];
        if (!first || first.chapter == null) return null;
        const p = getAdjacentSegments(first.chapter, first.index).prev;
        if (p && p.segment_uid && segmentsInRange.some((s) => s.segment_uid === p.segment_uid)) return null;
        return p;
    })();

    $: nextSeg = ((): Segment | null => {
        if (!showContext) return null;
        const last = segmentsInRange[segmentsInRange.length - 1];
        if (!last || last.chapter == null) return null;
        const n = getAdjacentSegments(last.chapter, last.index).next;
        if (n && n.segment_uid && segmentsInRange.some((s) => s.segment_uid === n.segment_uid)) return null;
        return n;
    })();

    // Ordered sibling list — render order — passed to every SegmentRow so
    // playback prefetch can warm the next sibling's clip URL by list
    // position. Mirrors the template below exactly.
    $: siblings = ((): Segment[] => {
        const out: Segment[] = [];
        if (prevSeg) out.push(prevSeg);
        for (const s of segmentsInRange) out.push(s);
        if (nextSeg) out.push(nextSeg);
        return out;
    })();

    // ---- Public interface (forwarded from ErrorCard dispatcher) ----
    export function getIsContextShown(): boolean { return showContext; }
    export function showContextForced(): void { showContext = true; dispatch('contextchange', true); }
    export function hideContextForced(): void { showContext = false; dispatch('contextchange', false); }

    function toggleContext(): void {
        showContext = !showContext;
        dispatch('contextchange', showContext);
    }

    // ---- Auto-fix handler ----
    function handleAutoFix(autoFix: SegValAutoFix | null | undefined): void {
        if (!autoFix) return;
        const targetSeg = getSegByChapterIndex(item.chapter, autoFix.target_seg_index);
        if (!targetSeg) return;
        const newRef = `${autoFix.new_ref_start}-${autoFix.new_ref_end}`;
        autoFixMissingWord(targetSeg, newRef);
    }

    // Keyboard "F" target — the single auto-fill (or the "up" variant when the
    // gap offers a directional pair). Null when this card has no auto-fix.
    $: cardAutofill = item.auto_fix
        ? () => handleAutoFix(item.auto_fix)
        : item.auto_fix_up
          ? () => handleAutoFix(item.auto_fix_up)
          : null;

    $: gapLabel = tr($localeStore, item.msg || m.segments_validation_missing_words_default_label());
    $: contextPreviousLabel = tr($localeStore, m.segments_validation_context_label_previous());
    $: contextNextLabel = tr($localeStore, m.segments_validation_context_label_next());
    $: autofillTitle = tr($localeStore, m.segments_validation_autofill_title());
    $: autofillButtonLabel = tr($localeStore, m.segments_validation_autofill_button());
    $: autofillUpTitle = tr($localeStore, m.segments_validation_autofill_up_title());
    $: autofillUpButtonLabel = tr($localeStore, m.segments_validation_autofill_up_button());
    $: autofillDownTitle = tr($localeStore, m.segments_validation_autofill_down_title());
    $: autofillDownButtonLabel = tr($localeStore, m.segments_validation_autofill_down_button());
    $: contextToggleLabel = tr($localeStore, showContext ? m.segments_validation_hide_context_button() : m.segments_validation_show_context_button());
</script>

<div>
    <div class="val-card-gap-label">{gapLabel}</div>
    {#if prevSeg}
        <SegmentRow
            seg={prevSeg}
            isContext={true}
            contextLabel={contextPreviousLabel}
            showPlayBtn={true}
            showChapter={true}
            accordionSiblings={siblings}
        />
    {/if}
    {#each segmentsInRange as s (s.segment_uid ?? `${s.chapter}:${s.index}`)}
        <SegmentRow
            seg={s}
            showGotoBtn={true}
            showPlayBtn={true}
            showChapter={true}
            validationCategory="missing_words"
            accordionSiblings={siblings}
            onCardAutofill={cardAutofill}
            onCardToggleContext={toggleContext}
        />
    {/each}
    {#if nextSeg}
        <SegmentRow
            seg={nextSeg}
            isContext={true}
            contextLabel={contextNextLabel}
            showPlayBtn={true}
            showChapter={true}
            accordionSiblings={siblings}
        />
    {/if}
    <div class="val-card-actions">
        {#if item.auto_fix}
            <button
                class="val-action-btn"
                title={autofillTitle}
                use:editGate
                on:click={() => handleAutoFix(item.auto_fix)}
            >{autofillButtonLabel}</button>
        {:else if item.auto_fix_up && item.auto_fix_down}
            <button
                class="val-action-btn"
                title={autofillUpTitle}
                use:editGate
                on:click={() => handleAutoFix(item.auto_fix_up)}
            >{autofillUpButtonLabel}</button>
            <button
                class="val-action-btn"
                title={autofillDownTitle}
                use:editGate
                on:click={() => handleAutoFix(item.auto_fix_down)}
            >{autofillDownButtonLabel}</button>
        {/if}
        <button
            class="val-action-btn val-action-btn-muted val-ctx-toggle-btn"
            on:click={toggleContext}
        >{contextToggleLabel}</button>
    </div>
</div>
