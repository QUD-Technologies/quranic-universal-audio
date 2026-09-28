/**
 * Merge adjacent segments operation.
 */

import { get } from 'svelte/store';

import { quranRefs } from '../../../../lib/refs/quran-refs';
import type { Segment } from '../../../../lib/types/view-models';
import { applyCommand } from '../../domain/apply-command';
import {
    getChapterSegments,
    invalidateChapterIndexFor,
    segAllData,
    segData,
    selectedChapter,
    syncChapterSegsToAll,
} from '../../stores/chapter';
import {
    markDirty,
} from '../../stores/dirty';
import {
    clearEdit,
    setEdit,
} from '../../stores/edit';
import { recordMergeRedirect } from '../../stores/merge-redirect';
import { clearFlashForChapter } from '../../stores/navigation';
import { dkTextForRef, getVerseWordCounts } from '../data/references';
import { reconcilePlayingAfterMutation } from '../playback/playback';
import { finalizeEdit } from './common';

// ---------------------------------------------------------------------------
// mergeAdjacent — combine two adjacent segments
// ---------------------------------------------------------------------------

export function mergeAdjacent(
    seg: Segment,
    direction: 'prev' | 'next',
    contextCategory: string | null = null,
    mountId: symbol | null = null,
    joinVerdicts?: NonNullable<Segment['join_verdicts']>,
): void {
    const chStr = get(selectedChapter);
    const chapter = seg.chapter || parseInt(chStr);
    const currentChapter = parseInt(chStr);
    const curData = get(segData);
    const allData = get(segAllData);

    let chapterSegs: Segment[] | undefined;
    if (chapter === currentChapter && curData?.segments) {
        chapterSegs = curData.segments;
    } else if (allData?.segments) {
        chapterSegs = getChapterSegments(chapter);
    }
    if (!chapterSegs) return;

    const idx = chapterSegs.findIndex(s => s.index === seg.index);
    if (idx === -1) return;
    const otherIdx = direction === 'prev' ? idx - 1 : idx + 1;
    if (otherIdx < 0 || otherIdx >= chapterSegs.length) return;
    const other = chapterSegs[otherIdx];
    if (!other) return;

    const first = direction === 'prev' ? other : seg;
    const second = direction === 'prev' ? seg : other;

    // Audio-URL guard: cross-audio merges aren't representable. Bail before
    // signaling merge mode so the store doesn't briefly reflect an aborted op.
    const firstAudio = first.audio_url || '';
    const secondAudio = second.audio_url || '';
    if (firstAudio !== secondAudio) return;

    // Reducer needs both halves identifiable by uid.
    const segUid = seg.segment_uid;
    const otherUid = other.segment_uid;
    if (!segUid || !otherUid) return;

    // Signal merge mode to EditOverlay (and future MergePanel) only after all
    // pure guard checks pass, so the store only reflects merge while we're
    // actually committed to executing. `mountId` pins the initiating row
    // so accordion twins stay passive; omit (null) for programmatic calls.
    setEdit('merge', seg.segment_uid ?? null, mountId);

    // Build the merged ref from the two halves' endpoints, then derive the
    // canonical Arabic text for it locally via `dkTextForRef`. Falls back to
    // concatenated halves when the merged ref isn't resolvable.
    let mergedRef = '';
    const refs = [first.matched_ref, second.matched_ref].filter(Boolean);
    if (refs.length > 0) {
        const firstRef = refs[0]!;
        const lastRef = refs[refs.length - 1]!;
        const s = firstRef.includes('-') ? firstRef.split('-')[0] : firstRef;
        const e = lastRef.includes('-') ? lastRef.split('-')[1] : lastRef;
        mergedRef = `${s}-${e}`;
    }

    let mergedText = [first.matched_text, second.matched_text].filter(Boolean).join(' ');
    if (mergedRef) {
        const dk = get(quranRefs)?.dk_words;
        const resolved = dkTextForRef(mergedRef, dk, getVerseWordCounts());
        if (resolved) mergedText = resolved;
    }

    const result = applyCommand(
        {
            byId: { [segUid]: seg, [otherUid]: other },
            idsByChapter: { [chapter]: chapterSegs.map(s => s.segment_uid).filter((u): u is string => !!u) },
            selectedChapter: chapter,
        },
        {
            type: 'merge',
            joinVerdicts,
            fromUid: segUid,
            toUid: otherUid,
            direction,
            mergedRef,
            mergedText,
            sourceCategory: contextCategory ?? undefined,
            contextCategory: contextCategory ?? undefined,
        },
    );
    const keptUid = first.segment_uid!;
    const consumedUid = second.segment_uid!;
    const merged = result.nextState.byId[keptUid] as Segment;
    if (!merged) { clearEdit(); return; }

    // Record the merge redirect so accordion cards pinned to the consumed
    // UID can follow the redirect to the surviving segment.
    recordMergeRedirect(consumedUid, keptUid);

    const _keptOldIdx = first.index;
    const _consumedOldIdx = second.index;

    // Capture pre-mutation UIDs for playing-pair reconciliation. The playing
    // seg might be either side of the merge; try whichever survives post-merge.
    const firstUid = first.segment_uid ?? null;
    const secondUid = second.segment_uid ?? null;

    if (chapter === currentChapter && curData?.segments) {
        const spliceIdx = Math.min(idx, otherIdx);
        curData.segments.splice(spliceIdx, 2, merged);
        curData.segments.forEach((s, i) => { s.index = i; });
        syncChapterSegsToAll();
    } else if (allData?.segments) {
        // Identity via UID (not object reference) — safer across store refreshes.
        const globalFirst = allData.segments.findIndex(s => s.segment_uid === first.segment_uid);
        const globalSecond = allData.segments.findIndex(s => s.segment_uid === second.segment_uid);
        const spliceStart = Math.min(globalFirst, globalSecond);
        allData.segments.splice(spliceStart, 2, merged);
        let reIdx = 0;
        allData.segments.forEach(s => { if (s.chapter === chapter) s.index = reIdx++; });
        invalidateChapterIndexFor(chapter);
    }

    // Try both sides for the playing seg — whichever preserves a hit resolves
    // to the merged kept-UID (first), whichever doesn't is the consumed side
    // which now legitimately clears.
    reconcilePlayingAfterMutation(chapter, firstUid);
    reconcilePlayingAfterMutation(chapter, secondUid);
    clearFlashForChapter(chapter);

    markDirty(chapter, undefined, true);
    if (chapter === currentChapter && curData) {
        curData.segments = getChapterSegments(chapter);
    }
    finalizeEdit(result.operation, chapter, [merged], { patch: result.patch });
    clearEdit();
}
