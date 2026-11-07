/**
 * Shared edit-mode cleanup: exitEditMode + _playRange passthrough.
 *
 * Entry-point logic (enterEditWithBuffer) lives in `edit-enter.ts` so
 * that file — not this one — owns the edges into `edit-trim` / `edit-split`.
 * That keeps `edit-common` a dependency-leaf of those modules (they import
 * `_playRange` and `exitEditMode` from here).
 */

import { get } from 'svelte/store';

import type { EditOp, EditOpPatch, Segment } from '../../../../lib/types/view-models';
import { getSegByChapterIndex, segAllData } from '../../stores/chapter';
import {
    finalizeOp,
    setPendingOp,
    snapshotSeg,
} from '../../stores/dirty';
import {
    clearEdit,
    editCanvas,
    editingSegUid,
} from '../../stores/edit';
import { segPort } from '../../stores/playback';
import type { SegmentState } from '../../stores/segments';
import { applyVerseFilterAndRender } from '../data/filters-apply';
import {
    _playRange as _playRangeImpl,
    attachPreviewLoop as attachPreviewLoopImpl,
    clearPlayRangeRAF,
    clearPreviewCanplayHandler,
    editPreviewPlaying,
    getPreviewStopHandler,
    setPreviewJustSeeked,
    setPreviewLooping,
    setPreviewStopHandler,
} from '../playback/play-range';
import { ensureBoundedRange, startSegAnimation } from '../playback/playback';
import { drawWaveformFromPeaksForSeg } from '../waveform/draw-seg';
import { cancelSplitZoom } from './split-zoom';
import { readback2d } from '../../../../lib/utils/canvas-2d';

// ---------------------------------------------------------------------------
// exitEditMode — shared cleanup for trim/split
// ---------------------------------------------------------------------------

export function exitEditMode(): void {
    setPendingOp(null);

    const canvas = get(editCanvas);
    const editRow = canvas?.closest<HTMLElement>('.seg-row') ?? null;
    if (canvas) {
        canvas._editCleanup?.();
        delete canvas._trimWindow; delete canvas._splitData;
        delete canvas._editCleanup;
        // Clear cached trim/split base images too — stale caches otherwise let
        // a later `drawTrimWaveform` path repaint the old handle/dim overlay.
        canvas._trimBaseCache = null;
        canvas._splitBaseCache = null;
        canvas._wfCache = null;
        canvas.style.cursor = '';
        // Unconditionally blank the canvas so the trim handles/dim are gone
        // even when peaks aren't cached — otherwise the canvas retained its
        // last trim draw and ESC appeared to do nothing.
        const ctx = readback2d(canvas);
        if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (editRow) {
            const idx = parseInt(editRow.dataset.segIndex ?? '-1');
            const chapter = parseInt(editRow.dataset.segChapter ?? '-1');
            const seg = chapter >= 0 ? getSegByChapterIndex(chapter, idx) : null;
            // If peaks are cached, redraw the plain waveform immediately;
            // otherwise re-attach to the IntersectionObserver so it repaints
            // as soon as peaks arrive.
            const drew = seg ? drawWaveformFromPeaksForSeg(canvas, seg, seg.chapter ?? 0) : false;
            if (!drew) canvas.setAttribute('data-needs-waveform', '');
        }
    }

    clearEdit();
    setPreviewLooping(false);
    setPreviewJustSeeked(false);
    editPreviewPlaying.set(false);
    cancelSplitZoom();
    clearPlayRangeRAF();
    clearPreviewCanplayHandler();
    const stopHandler = getPreviewStopHandler();
    if (stopHandler) {
        const el = segPort.element;
        if (el) el.removeEventListener('timeupdate', stopHandler);
        setPreviewStopHandler(null);
    }
    // If preview left audio playing (e.g. user hit Apply mid-loop), hand
    // playback back to the main rAF loop. `startSegAnimation` is a no-op
    // while editMode is set — we reach it here only AFTER `clearEdit()`
    // above, so the gate is open and the main-list playhead resumes.
    if (!segPort.paused) startSegAnimation();
    // Reconcile the bounded-vs-continuous state — `enter.ts` disposed
    // `_segRange` on edit entry, and without this rebuild a played
    // accordion / autoplay-off segment would emerge from the edit cycle
    // unbounded and sail past its boundary. Idempotent for the chapter-
    // continuous case (no range needed → no-op).
    ensureBoundedRange();
}

// Re-export play-range implementation so existing callers still work.
export const _playRange = _playRangeImpl;
export const attachPreviewLoop = attachPreviewLoopImpl;

// ---------------------------------------------------------------------------
// finalizeEdit — post-mutation scaffolding shared by edit-merge/split/delete/trim
// ---------------------------------------------------------------------------

/**
 * Finalize an edit op and refresh the UI after a segment mutation.
 *
 * Stamps the op (applied_at + targets_after), appends it to the chapter op
 * log via `finalizeOp` (which also clears the pending-op ref), re-renders
 * the filtered list, and publishes the completion status message.  Silence
 * gaps are derived from ``derivedTimings`` (a Svelte derived store over
 * ``segAllData``) so they refresh automatically when the store update
 * fires; no explicit recompute call is required here.
 *
 * Callers still own `markDirty` (timing varies) and whichever exit path
 * they need (`clearEdit` for merge/delete, `exitEditMode` for trim/split
 * which also has canvas-level cleanup). Each step is skippable via `opts`
 * so the helper also fits the few sites that don't need the full refresh.
 */
export function finalizeEdit(
    op: EditOp,
    chapter: number,
    targetsAfter: Segment[],
    opts?: {
        skipSilence?: boolean;
        skipFilterRender?: boolean;
        skipAccordion?: boolean;
        /** Forward-change patch from the reducer; attached to the op so the
         *  pending-discard path (and the save payload) can consume it without
         *  re-running the command. */
        patch?: EditOpPatch;
    },
): void {
    op.targets_after = targetsAfter.map(snapshotSeg);
    if (opts?.patch) op.patch = opts.patch;
    // skipSilence is retained on the type for call-site compatibility; the
    // derivedTimings store reactively refreshes from segAllData on its own.
    void opts?.skipSilence;
    if (!opts?.skipFilterRender) applyVerseFilterAndRender();
    finalizeOp(chapter, op);
}

// ---------------------------------------------------------------------------
// getEditingSeg — resolve the currently-editing seg by UID
// ---------------------------------------------------------------------------

/**
 * Resolve the currently-editing segment by walking `segAllData.segments` for a
 * UID match against `$editingSegUid`. Used by keyboard handlers that must
 * operate on accordion-mounted edits too — the main-list `displayedSegments`
 * lookup misses when the editing row belongs to an accordion in a different
 * chapter. Returns null if no edit is active or the UID is stale.
 */
export function getEditingSeg(): Segment | null {
    const uid = get(editingSegUid);
    if (!uid) return null;
    const all = get(segAllData);
    if (!all?.segments) return null;
    return all.segments.find((s) => s.segment_uid === uid) ?? null;
}

// ---------------------------------------------------------------------------
// segSlice — build a minimal SegmentState for a single-segment applyCommand call
// ---------------------------------------------------------------------------

/**
 * Build the minimal ``SegmentState`` slice that ``applyCommand`` expects when
 * dispatching a single-segment command. All per-dispatcher call sites repeat
 * the same three-field literal; this helper centralizes that boilerplate.
 *
 * Usage:
 * ```ts
 * const result = applyCommand(segSlice(seg, chapter), { type: 'trim', ... });
 * ```
 */
export function segSlice(seg: Segment, chapter: number): SegmentState {
    const uid = seg.segment_uid;
    if (!uid) {
        return { byId: {}, idsByChapter: { [chapter]: [] }, selectedChapter: chapter };
    }
    return {
        byId: { [uid]: seg },
        idsByChapter: { [chapter]: [uid] },
        selectedChapter: chapter,
    };
}
