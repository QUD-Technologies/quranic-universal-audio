/**
 * commitSplit — dispatch a `split` command and land its pieces in the store.
 *
 * The one place a split becomes an op: resolves per-piece refs/texts,
 * mints child uids, runs the reducer, splices the pieces into the chapter
 * (or global) segment list, marks the chapter structurally dirty and
 * finalizes the op onto the dirty log. Two callers:
 *
 *   • `split.ts::confirmSplit` — the canvas edit mode (cursor drag). It owns
 *     the pending op created by `enterEditWithBuffer`, then chains ref-edits
 *     and WASL/WAQF prompts after this returns.
 *   • the staged cross-verse card — sidecar cursors + refs, every boundary
 *     already labelled, so it passes `wasls` and skips the chain entirely.
 *
 * The reducer builds the op envelope itself; the pending op from edit mode
 * (when any) only contributes its `op_context_category`. The staged path has
 * no pending op and passes `contextCategory` instead.
 */

import { get } from 'svelte/store';

import { quranRefs } from '../../../../lib/refs/quran-refs';
import type { Segment } from '../../../../lib/types/view-models';
import { applyCommand } from '../../domain/apply-command';
import type { CommandResult } from '../../domain/command';
import {
    getChapterSegments,
    invalidateChapterIndexFor,
    segAllData,
    segData,
    selectedChapter,
    syncChapterSegsToAll,
} from '../../stores/chapter';
import { getPendingOp, markDirty } from '../../stores/dirty';
import { clearFlashForChapter } from '../../stores/navigation';
import {
    _suggestSplitRefs as _suggestSplitRefsLib,
    dkTextForRef,
    getVerseWordCounts,
} from '../data/references';
import { reconcilePlayingAfterMutation } from '../playback/playback';
import { finalizeEdit } from './common';

export interface CommitSplitOptions {
    /** Per-piece refs (`cursors.length + 1`). Omit to fall back to the
     *  binary cross-verse suggestion (single-cursor case only). */
    refs?: readonly (string | undefined)[] | null;
    /** `is_wasl` for every non-last piece (`cursors.length`). */
    wasls?: readonly boolean[] | null;
    /** Uids for pieces 1..N; fresh uuids when omitted. */
    newUids?: readonly string[] | null;
    /** Validation category the split was launched from (history pill). Used
     *  only when no pending op already carries one. */
    contextCategory?: string | null;
}

export interface CommitSplitResult {
    pieces: Segment[];
    chapter: number;
    contextCategory: string | null;
    result: CommandResult;
}

/**
 * Apply the split. Returns `null` (and dispatches nothing) when the cursors
 * fall outside the seg or aren't strictly ascending, or the seg has no uid.
 */
export function commitSplit(
    seg: Segment,
    cursors: readonly number[],
    opts: CommitSplitOptions = {},
): CommitSplitResult | null {
    if (!cursors.length) return null;
    for (let i = 0; i < cursors.length; i++) {
        const ci = cursors[i]!;
        if (ci <= seg.time_start || ci >= seg.time_end) return null;
        if (i > 0 && ci <= cursors[i - 1]!) return null;
    }
    const uid = seg.segment_uid;
    if (!uid) return null;

    const chStr = get(selectedChapter);
    const chapter = seg.chapter || parseInt(chStr);
    const currentChapter = parseInt(chStr);
    const curData = get(segData);
    const useSegData = chapter === currentChapter && curData?.segments;
    const prePlayingUid = seg.segment_uid ?? null;

    const ctxCat = getPendingOp()?.op_context_category ?? opts.contextCategory ?? null;

    const n = cursors.length + 1;
    const dk = get(quranRefs)?.dk_words;
    const vwc = getVerseWordCounts();
    let refs: (string | undefined)[] = new Array<string | undefined>(n).fill(undefined);
    let texts: (string | undefined)[] = new Array<string | undefined>(n).fill(undefined);
    if (opts.refs && opts.refs.length === n) {
        refs = opts.refs.slice();
        texts = refs.map((r) => (r ? dkTextForRef(r, dk, vwc) : undefined));
    } else if (cursors.length === 1) {
        const suggested = _suggestSplitRefsLib(seg.matched_ref, vwc);
        if (suggested) {
            refs[0] = suggested.first;
            refs[1] = suggested.second;
            texts[0] = dkTextForRef(suggested.first, dk, vwc);
            texts[1] = dkTextForRef(suggested.second, dk, vwc);
        }
    }

    const newUids = opts.newUids && opts.newUids.length === cursors.length
        ? opts.newUids.slice()
        : cursors.map(() => crypto.randomUUID());
    const wasls = opts.wasls && opts.wasls.length === cursors.length ? opts.wasls.slice() : undefined;

    const result = applyCommand(
        {
            byId: { [uid]: seg },
            idsByChapter: { [chapter]: [uid] },
            selectedChapter: chapter,
        },
        {
            type: 'split',
            segmentUid: uid,
            splitMs: cursors.slice(),
            newUids,
            refs,
            texts,
            ...(wasls ? { wasls } : {}),
            sourceCategory: ctxCat ?? undefined,
            contextCategory: ctxCat ?? undefined,
        },
    );

    // Reducer produces N pieces: piece 0 reuses `uid`, the rest `newUids`.
    const pieces: Segment[] = [result.nextState.byId[uid] as Segment];
    for (const u of newUids) pieces.push(result.nextState.byId[u] as Segment);
    if (pieces.some((p) => !p)) return null;

    if (useSegData && curData) {
        const segIdx = curData.segments.findIndex((s) => s.index === seg.index);
        curData.segments.splice(segIdx, 1, ...pieces);
        curData.segments.forEach((s, i) => { s.index = i; });
        syncChapterSegsToAll();
        curData.segments = getChapterSegments(chapter);
    } else {
        const allData = get(segAllData);
        if (allData) {
            const globalIdx = allData.segments.findIndex((s) => s.segment_uid === seg.segment_uid);
            if (globalIdx !== -1) allData.segments.splice(globalIdx, 1, ...pieces);
            let reIdx = 0;
            allData.segments.forEach((s) => { if (s.chapter === chapter) s.index = reIdx++; });
            invalidateChapterIndexFor(chapter);
        }
    }

    reconcilePlayingAfterMutation(chapter, prePlayingUid);
    clearFlashForChapter(chapter);
    markDirty(chapter, undefined, true);

    return { pieces, chapter, contextCategory: ctxCat, result };
}

/** Finalize the op produced by `commitSplit` onto the dirty log. Split out
 *  so the canvas caller can run `exitEditMode()` (which clears the pending
 *  op ref) between the two steps, as it always has. */
export function finalizeSplit(commit: CommitSplitResult): void {
    finalizeEdit(commit.result.operation, commit.chapter, commit.pieces, { patch: commit.result.patch });
}
