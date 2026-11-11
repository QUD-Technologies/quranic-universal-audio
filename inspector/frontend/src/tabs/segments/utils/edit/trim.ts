/**
 * Trim (boundary adjustment) edit mode: enter, drag handles, preview, confirm.
 */

import { get } from 'svelte/store';

import type { Segment } from '../../../../lib/types/view-models';
import { getWaveformPeaks } from '../../../../lib/utils/waveform-cache';
import { applyCommand } from '../../domain/apply-command';
import {
    getChapterSegments,
    getCurrentChapterSegs,
    invalidateChapterIndexFor,
    refreshSegInStore,
    segAllData,
    segData,
    selectedChapter,
    syncChapterSegsToAll,
} from '../../stores/chapter';
import { segConfig } from '../../stores/config';
import {
    getPendingOp,
    markDirty,
} from '../../stores/dirty';
import {
    activeTrimBoundary,
    editCanvas,
    editMode,
    setActiveTrimBoundary,
    setEdit,
    setEditCanvas,
    setEditingSegIndex,
    setEditStatusText,
    setTrimWindow,
    updateTrimWindow,
} from '../../stores/edit';
import { segPort } from '../../stores/playback';
import type { SegCanvas } from '../../types/segments-waveform';
import { EDIT_MIN_DURATION_MS, EDIT_SNAP_MS, TRIM_HANDLE_HIT_RADIUS_PX } from '../constants';
import {
    editPreviewPlaying,
    setPreviewJustSeeked,
    setPreviewLooping,
} from '../playback/play-range';
import { _ensureTrimBaseCache, drawTrimWaveform } from '../waveform/trim-draw';
import { _fetchPeaksForClick } from '../waveform/utils';
import { _playRange, attachPreviewLoop, exitEditMode, finalizeEdit } from './common';
import { applyWheelZoom } from './trim-zoom';

// Re-export draw functions so registration sites and other callers still work.
export { _ensureTrimBaseCache, drawTrimWaveform };

// ---------------------------------------------------------------------------
// enterTrimMode
// ---------------------------------------------------------------------------

/**
 * Compute the trim clamp bounds for a segment — the hard min/max the user
 * can move time_start/time_end to. Bounds are `max(prevSegEnd, seg.time_start - trimPadLeft)`
 * and `min(nextSegStart, seg.time_end + trimPadRight)`, i.e. pad outward from
 * the current boundary but never cross a neighbor. `peaksDurationMs` caps the
 * right bound for the last seg in a chapter (no next seg to clamp against).
 *
 * Exposed so the row time-edit widget can reason about editability without
 * duplicating the neighbor-lookup logic. Pure — no store writes, no side
 * effects on `seg`.
 */
/** Resolve the authoritative audio EOF (ms) for a seg from the audio_manifest
 *  sidecar — URL-keyed first (covers by_ayah deliveries) then chapter-keyed
 *  (covers by_surah). Returns undefined when no sidecar info is available. */
export function getAudioEndMsForSeg(seg: Segment): number | undefined {
    const allData = get(segAllData);
    if (!allData) return undefined;
    const chapterKey = String(seg.chapter ?? 0);
    const audioUrl = seg.audio_url || allData.audio_by_chapter?.[chapterKey] || '';
    if (audioUrl && allData.duration_ms_by_url?.[audioUrl]) {
        return allData.duration_ms_by_url[audioUrl];
    }
    return allData.chapter_duration_ms_by_chapter?.[chapterKey];
}

export function computeTrimBounds(
    seg: Segment,
    chapterSegs: Segment[],
    cfg: { trimPadLeft: number; trimPadRight: number },
    peaksDurationMs?: number,
): { windowStart: number; windowEnd: number; audioUrl: string } {
    const segIdx = chapterSegs.findIndex((s) => s.index === seg.index);
    const prevEnd = segIdx > 0 ? (chapterSegs[segIdx - 1]?.time_end ?? 0) : 0;
    const chapterKey = String(seg.chapter ?? 0);
    const allData = get(segAllData);
    const audioUrl = seg.audio_url || allData?.audio_by_chapter?.[chapterKey] || '';
    const fallbackPad = Math.max(1000, cfg.trimPadRight);
    // Authoritative audio EOF from the audio_manifest sidecar — beats both
    // the per-URL peaks duration (only populated after whole-chapter peaks
    // load) and `seg.time_end + fallbackPad` (which can run past actual EOF
    // for the last verse and produce an empty waveform draw).
    const audioEndMs = getAudioEndMsForSeg(seg);
    const isLastSeg = segIdx >= 0 && segIdx >= chapterSegs.length - 1;
    let nextStart: number;
    if (!isLastSeg) {
        nextStart = chapterSegs[segIdx + 1]?.time_start ?? seg.time_end + fallbackPad;
    } else if (audioEndMs && audioEndMs > seg.time_end) {
        nextStart = audioEndMs;
    } else if (peaksDurationMs && peaksDurationMs > seg.time_end) {
        nextStart = peaksDurationMs;
    } else {
        nextStart = seg.time_end + fallbackPad;
    }
    const windowStart = Math.max(prevEnd, seg.time_start - cfg.trimPadLeft);
    let windowEnd = Math.min(nextStart, seg.time_end + cfg.trimPadRight);
    if (audioEndMs && windowEnd > audioEndMs) windowEnd = audioEndMs;
    return { windowStart, windowEnd, audioUrl };
}

export function enterTrimMode(seg: Segment, row: HTMLElement, mountId: symbol | null = null): void {
    if (get(editMode)) {
        console.warn('[trim] blocked: already in edit mode:', get(editMode));
        return;
    }
    setEdit('trim', seg.segment_uid ?? null, mountId);
    setEditingSegIndex(seg.index);
    setEditStatusText('');
    setActiveTrimBoundary('start');

    const canvas = row.querySelector<SegCanvas>('canvas');
    if (!canvas) return;

    const chStr = get(selectedChapter);
    const chapter = seg.chapter || parseInt(chStr);
    const currentChapter = parseInt(chStr);
    const chapterSegs = (chapter === currentChapter) ? getCurrentChapterSegs() : getChapterSegments(chapter);
    const cfg = get(segConfig);
    const peaksDuration = getWaveformPeaks(seg.audio_url || get(segAllData)?.audio_by_chapter?.[String(chapter)] || '')?.duration_ms;
    const { windowStart, windowEnd, audioUrl } = computeTrimBounds(
        { ...seg, chapter },
        chapterSegs,
        cfg,
        peaksDuration,
    );
    // Init view = full clamp window (no zoom). Reset on every entry — zoom
    // state is intentionally not preserved across edit sessions (req #9).
    canvas._trimWindow = {
        windowStart, windowEnd,
        viewStart: windowStart, viewEnd: windowEnd,
        currentStart: seg.time_start, currentEnd: seg.time_end,
        audioUrl,
    };
    setTrimWindow({ ...canvas._trimWindow });
    canvas._wfCache = null;
    canvas._trimBaseCache = null;
    // Populate the editCanvas store synchronously. SegmentRow.svelte publishes
    // it too via a reactive block, but that fires on the next microtask — the
    // synchronous set lets any user-gesture-driven preview (footer ▶, future
    // Replay button) read `editCanvas` via `get(editCanvas)` and thread it
    // into the `_playRange` rAF loop without a one-frame null window.
    setEditCanvas(canvas);

    drawTrimWaveform(canvas);
    setupTrimDragHandles(canvas, seg);

    // Launch the trim preview loop. `previewTrimAudio` branches warm vs.
    // cold internally: chapter audio playing within the trim window →
    // warm-attach the playhead rAF without re-seeking (audio continues
    // from where it plays, loop-back catches the wrap at currentEnd);
    // paused or playhead outside the window → cold-start the loop via
    // `_playRange` (seek to currentStart + play). The call must stay
    // synchronous inside the click handler chain so Chrome accepts the
    // play promise on the cold path.
    previewTrimAudio(canvas);
    void _fetchPeaksForClick(seg, chapter).then(() => {
        if (!canvas._trimWindow) return; // user exited trim mode mid-fetch

        const loadedDuration = getWaveformPeaks(seg.audio_url || get(segAllData)?.audio_by_chapter?.[String(chapter)] || '')?.duration_ms;
        if (loadedDuration) {
            const { windowStart, windowEnd } = computeTrimBounds(
                { ...seg, chapter },
                chapterSegs,
                cfg,
                loadedDuration,
            );
            const tw = canvas._trimWindow;
            tw.windowStart = windowStart;
            tw.windowEnd = windowEnd;
            tw.viewStart = Math.max(windowStart, tw.viewStart);
            tw.viewEnd = Math.min(windowEnd, tw.viewEnd);
            
            // Constrain cursors. If they were already dragged past the new true end, snap them back.
            tw.currentStart = Math.max(windowStart, Math.min(tw.currentStart, windowEnd - EDIT_MIN_DURATION_MS));
            tw.currentEnd = Math.min(windowEnd, Math.max(tw.currentEnd, tw.currentStart + EDIT_MIN_DURATION_MS));
            
            setTrimWindow({ ...tw });
        }

        drawTrimWaveform(canvas);
    });
}

// ---------------------------------------------------------------------------
// setupTrimDragHandles — mouse event handlers for trim handles
// ---------------------------------------------------------------------------

export function setupTrimDragHandles(canvas: SegCanvas, seg: Segment): void {
    void seg; // reserved for future per-seg snap tuning
    let dragging: 'start' | 'end' | null = null;
    let didDrag = false;
    const HANDLE_THRESHOLD = TRIM_HANDLE_HIT_RADIUS_PX;

    /** Visible x-coords of the start + end cursors, with strict per-side
     *  clipping when the cursor's actual time is outside the visible window:
     *  start clips to LEFT edge (x=0), end clips to RIGHT edge (x=width).
     *  Drag/click hit-detection uses these clipped coords so a clamped
     *  cursor at the canvas edge is still grabbable. */
    function _getHandleXs(): { startX: number; endX: number } {
        const tw = canvas._trimWindow!;
        const w = canvas.width;
        const span = tw.viewEnd - tw.viewStart;
        const sxRaw = ((tw.currentStart - tw.viewStart) / span) * w;
        const exRaw = ((tw.currentEnd - tw.viewStart) / span) * w;
        const startOff = tw.currentStart < tw.viewStart || tw.currentStart > tw.viewEnd;
        const endOff   = tw.currentEnd   < tw.viewStart || tw.currentEnd   > tw.viewEnd;
        return {
            startX: startOff ? 0 : sxRaw,
            endX:   endOff   ? w : exRaw,
        };
    }

    function onMousedown(e: MouseEvent): void {
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) * (canvas.width / rect.width);
        const { startX, endX } = _getHandleXs();
        didDrag = false;

        if (Math.abs(x - startX) < HANDLE_THRESHOLD) dragging = 'start';
        else if (Math.abs(x - endX) < HANDLE_THRESHOLD) dragging = 'end';
        if (dragging) canvas.style.cursor = 'col-resize';
    }

    function onMousemove(e: MouseEvent): void {
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) * (canvas.width / rect.width);
        const tw = canvas._trimWindow;
        if (!tw) return;
        const width = canvas.width;

        if (!dragging) {
            const { startX, endX } = _getHandleXs();
            canvas.style.cursor = (Math.abs(x - startX) < HANDLE_THRESHOLD || Math.abs(x - endX) < HANDLE_THRESHOLD) ? 'col-resize' : 'pointer';
            return;
        }
        didDrag = true;
        // Pixel→time uses the VISIBLE window (so dragging a clamped cursor
        // jumps the actual time to the dragged pixel's time, per req #6).
        // Final boundary still clamps to [windowStart, windowEnd].
        const timeAtX = tw.viewStart + (x / width) * (tw.viewEnd - tw.viewStart);
        const snapped = Math.round(timeAtX / EDIT_SNAP_MS) * EDIT_SNAP_MS;

        if (dragging === 'start') {
            tw.currentStart = Math.max(tw.windowStart, Math.min(snapped, tw.currentEnd - EDIT_MIN_DURATION_MS));
        } else {
            tw.currentEnd = Math.max(tw.currentStart + EDIT_MIN_DURATION_MS, Math.min(snapped, tw.windowEnd));
        }
        updateTrimWindow((w) => w ? { ...w, currentStart: tw.currentStart, currentEnd: tw.currentEnd } : w);
        drawTrimWaveform(canvas);
    }

    function onMouseup(e: MouseEvent): void {
        if (!dragging && !didDrag) {
            const rect = canvas.getBoundingClientRect();
            const x = (e.clientX - rect.left) * (canvas.width / rect.width);
            const tw = canvas._trimWindow;
            if (!tw) return;
            // Click-to-seek also uses the visible window for pixel→time.
            const timeAtX = tw.viewStart + (x / canvas.width) * (tw.viewEnd - tw.viewStart);
            const snapped = Math.round(timeAtX / EDIT_SNAP_MS) * EDIT_SNAP_MS;
            _playRange(snapped, tw.currentEnd);
        }
        dragging = null;
        canvas.style.cursor = '';
    }
    function onMouseleave(): void { dragging = null; canvas.style.cursor = ''; }

    /** Wheel zoom on the trim canvas. Suppressed mid-drag (req #8) so a
     *  user can't accidentally rescale the time-axis underneath an in-flight
     *  cursor drag. `passive: false` is required so `preventDefault()` can
     *  stop the page from scrolling while the wheel is over the canvas. */
    function onWheel(e: WheelEvent): void {
        if (dragging) return;
        e.preventDefault();
        applyWheelZoom(canvas, e.clientX, e.deltaY);
    }

    canvas.addEventListener('mousedown', onMousedown);
    canvas.addEventListener('mousemove', onMousemove);
    canvas.addEventListener('mouseup', onMouseup);
    canvas.addEventListener('mouseleave', onMouseleave);
    canvas.addEventListener('wheel', onWheel, { passive: false });

    canvas._editCleanup = (): void => {
        canvas.removeEventListener('mousedown', onMousedown);
        canvas.removeEventListener('mousemove', onMousemove);
        canvas.removeEventListener('mouseup', onMouseup);
        canvas.removeEventListener('mouseleave', onMouseleave);
        canvas.removeEventListener('wheel', onWheel);
    };
}

// ---------------------------------------------------------------------------
// nudgeTrimBoundary — step a cursor by ±deltaMs (called by TrimPanel steppers)
// ---------------------------------------------------------------------------

/**
 * Move one trim cursor by `deltaMs`, clamped to `[windowStart, windowEnd]`
 * AND respecting `EDIT_MIN_DURATION_MS` against the opposite handle. Mirrors
 * the live drag/typed-edit flow: writes both the store (for Svelte
 * subscribers — TrimPanel duration, TimeRange display) and the canvas-local
 * `_trimWindow` (for the imperative draw + drag-handle math), then redraws.
 *
 * **Snap-to-visual-border** (req #7): when the cursor is currently OFF-VIEW
 * (its actual time falls outside `[viewStart, viewEnd]` because the user
 * zoomed in past it), the step is anchored at the cursor's *visual* clamp
 * position rather than its actual time. Per strict clipping: start cursor
 * always clamps to LEFT (anchor = `viewStart`); end cursor always clamps
 * to RIGHT (anchor = `viewEnd`). So pressing `>` on a left-clamped start
 * with `viewStart=1000, deltaMs=50` lands at `1050` regardless of the
 * cursor's current actual time — making it pop back into view.
 *
 * The TrimPanel disable gates separately suppress the "away from view"
 * presses (e.g. `<` on a left-clamped start) since those produce no visible
 * feedback.
 *
 * Returns the new boundary position (or the unchanged value if the step
 * couldn't move because of clamping). UI uses this return + the bounds
 * arithmetic to decide whether the corresponding stepper button should be
 * disabled.
 */
export function nudgeTrimBoundary(side: 'start' | 'end', deltaMs: number): number | null {
    const canvas = get(editCanvas);
    const tw = canvas?._trimWindow;
    if (!canvas || !tw) return null;
    const minDur = EDIT_MIN_DURATION_MS;

    let next: number;
    if (side === 'start') {
        const onView = tw.currentStart >= tw.viewStart && tw.currentStart <= tw.viewEnd;
        const anchor = onView ? tw.currentStart : tw.viewStart; // strict-clip LEFT
        next = Math.max(tw.windowStart, Math.min(anchor + deltaMs, tw.currentEnd - minDur));
        if (next === tw.currentStart) return next;
        tw.currentStart = next;
    } else {
        const onView = tw.currentEnd >= tw.viewStart && tw.currentEnd <= tw.viewEnd;
        const anchor = onView ? tw.currentEnd : tw.viewEnd; // strict-clip RIGHT
        next = Math.max(tw.currentStart + minDur, Math.min(anchor + deltaMs, tw.windowEnd));
        if (next === tw.currentEnd) return next;
        tw.currentEnd = next;
    }
    updateTrimWindow((w) => w ? { ...w, currentStart: tw.currentStart, currentEnd: tw.currentEnd } : w);
    drawTrimWaveform(canvas);
    return next;
}

/** Toggle which trim handle the keyboard stepper drives (Tab in trim mode). */
export function cycleTrimBoundary(): void {
    setActiveTrimBoundary(get(activeTrimBoundary) === 'start' ? 'end' : 'start');
}

/** Nudge the keyboard-active trim handle by `deltaMs` (←/→ in trim mode). */
export function nudgeActiveTrimBoundary(deltaMs: number): number | null {
    return nudgeTrimBoundary(get(activeTrimBoundary), deltaMs);
}

// ---------------------------------------------------------------------------
// confirmTrim — apply the trim and finalize
// ---------------------------------------------------------------------------

export function confirmTrim(seg: Segment, canvas?: SegCanvas | null): void {
    const c = canvas ?? get(editCanvas);
    // Block Apply when a TimeEdit has an open, invalid typed value. The user
    // pressed Enter on an out-of-range value and got a whole-widget red
    // border; we must not silently exit edit mode and persist the LAST VALID
    // `canvas._trimWindow` value — that'd discard their typed input without
    // feedback. Keep the edit open so they can fix or Cancel.
    const editRow = c?.closest('.seg-row');
    if (editRow?.querySelector('.seg-text-time-editing.invalid')) {
        setEditStatusText('Fix invalid time first');
        return;
    }
    const tw = c?._trimWindow;
    const newStart = tw?.currentStart;
    const newEnd = tw?.currentEnd;
    if (newStart == null || newEnd == null || newStart >= newEnd) {
        setEditStatusText('Invalid time range');
        return;
    }

    const chStr = get(selectedChapter);
    const chapter = seg.chapter || parseInt(chStr);
    const currentChapter = parseInt(chStr);
    const chapterSegs = chapter === currentChapter ? getCurrentChapterSegs() : getChapterSegments(chapter);
    const segIdx = chapterSegs.findIndex(s => s.index === seg.index);
    const prevSeg = segIdx > 0 ? chapterSegs[segIdx - 1] : null;
    const nextSeg = (segIdx >= 0 && segIdx < chapterSegs.length - 1) ? chapterSegs[segIdx + 1] : null;

    if (prevSeg && prevSeg.audio_url === seg.audio_url && newStart < prevSeg.time_end) {
        setEditStatusText('Start overlaps with previous segment');
        return;
    }
    if (nextSeg && nextSeg.audio_url === seg.audio_url && newEnd > nextSeg.time_start) {
        setEditStatusText('End overlaps with next segment');
        return;
    }

    const pending = getPendingOp();
    const ctxCat = pending?.op_context_category ?? null;
    const uid = seg.segment_uid;
    if (!uid) {
        seg.time_start = newStart;
        seg.time_end = newEnd;
        seg.confidence = 1.0;
        markDirty(chapter, undefined, true);
        exitEditMode();
        refreshSegInStore(seg);
        return;
    }

    commitTrim(seg, { time_start: newStart, time_end: newEnd }, ctxCat, exitEditMode);
}

/**
 * Dispatch a `trim` of `seg` to `delta` and land it in the store (bounds,
 * confidence, verdicts). `onApplied` runs after the chapter sync, before the
 * op is finalized (the edit-mode caller exits there).
 */
export function commitTrim(
    seg: Segment,
    delta: { time_start?: number; time_end?: number },
    contextCategory: string | null,
    onApplied?: () => void,
): void {
    const uid = seg.segment_uid;
    if (!uid) return;
    const chapter = seg.chapter || parseInt(get(selectedChapter));
    const result = applyCommand(
        {
            byId: { [uid]: seg },
            idsByChapter: { [chapter]: [uid] },
            selectedChapter: chapter,
        },
        {
            type: 'trim',
            segmentUid: uid,
            delta,
            sourceCategory: contextCategory ?? undefined,
            contextCategory: contextCategory ?? undefined,
        },
    );
    const updated = result.nextState.byId[uid];
    if (updated) {
        seg.time_start = updated.time_start;
        seg.time_end = updated.time_end;
        seg.confidence = updated.confidence;
        if (updated.join_verdicts) seg.join_verdicts = updated.join_verdicts;
        if (updated.ignored_categories) {
            seg.ignored_categories = [...updated.ignored_categories];
        }
    }
    markDirty(chapter, undefined, true);

    const curData = get(segData);
    if (chapter !== parseInt(get(selectedChapter)) || !curData?.segments) {
        // A trim outside the current chapter: refreshSegInStore below patches the
        // cache; dropping the chapter's index covers a cache-miss rebuild.
        invalidateChapterIndexFor(chapter);
    } else {
        syncChapterSegsToAll();
    }

    onApplied?.();
    refreshSegInStore(seg);
    finalizeEdit(result.operation, chapter, [seg], { skipAccordion: true, patch: result.patch });
}

// ---------------------------------------------------------------------------
// previewTrimAudio — toggle looping preview of trimmed region
// ---------------------------------------------------------------------------

/** Launch the trim-window preview loop.
 *
 *  Default (`mode: 'auto'`) — branches on the live audio state:
 *  - Playing → warm-attach the playhead rAF without seeking. Audio
 *    continues from where it plays. If `live >= currentEnd` we prime
 *    `_previewJustSeeked = true` so the rAF's first frame doesn't fire
 *    the wrap-back; subsequent forward-crossings of `currentEnd` (after
 *    audio falls below the boundary) wrap normally.
 *  - Paused → cold-start via `_playRange` (seek + play + loop).
 *
 *  `mode: 'cold'` — always cold-start, regardless of audio state. Used
 *  by the Replay button's "restart from currentStart" gesture.
 *
 *  Idempotent — calling while a loop is already running cancels the prior
 *  rAF inside `_setupPreviewLoop`. Play/pause toggling is still owned by
 *  `onSegPlayClick` in playback.ts. */
export function previewTrimAudio(
    canvas?: SegCanvas | null,
    opts?: { mode?: 'auto' | 'cold' },
): void {
    const c = canvas ?? get(editCanvas);
    const tw = c?._trimWindow;
    if (!tw || !c) return;
    editPreviewPlaying.set(true);
    setPreviewLooping('trim');

    if (opts?.mode === 'cold' || segPort.paused) {
        _playRange(tw.currentStart, tw.currentEnd);
        return;
    }

    // Warm-attach. Skip the first wrap if audio is already past the
    // window so we don't snap backward to currentStart on entry.
    const live = segPort.currentTimeMs();
    setPreviewJustSeeked(live >= tw.currentEnd);
    attachPreviewLoop(tw.currentStart, tw.currentEnd);
}
