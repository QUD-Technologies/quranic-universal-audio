/**
 * Split edit mode: enter, drag handle, preview, confirm.
 *
 * Supports N cursors (length≥1). N=1 = today's binary split (one cursor → two
 * pieces, with Play L/R + click-to-seek). N≥2 = auto-split for repetitions
 * (N cursors → N+1 pieces, with per-region playback and no click-to-seek so
 * cursor drag is unambiguous).
 */

import { get } from 'svelte/store';

import { quranRefs } from '../../../../lib/refs/quran-refs';
import type { Segment } from '../../../../lib/types/view-models';
import { getWaveformPeaks } from '../../../../lib/utils/waveform-cache';
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
    getPendingOp,
    markDirty,
} from '../../stores/dirty';
import {
    editCanvas,
    editMode,
    markWaslPending,
    pendingChainTargets,
    setEdit,
    setEditCanvas,
    setEditingSegIndex,
    setEditStatusText,
    setSplitPreviewSelection,
    setSplitState,
    splitPreviewSelection,
    updateSplitState,
} from '../../stores/edit';
import { clearFlashForChapter, targetSegmentIndex } from '../../stores/navigation';
import { segPort } from '../../stores/playback';
import type { SegCanvas } from '../../types/segments-waveform';
import { EDIT_MIN_DURATION_MS,EDIT_SNAP_MS } from '../constants';
import {
    _suggestSplitRefs as _suggestSplitRefsLib,
    dkTextForRef,
    getVerseWordCounts,
    parseSegRef,
} from '../data/references';
import { editPreviewPlaying, setPreviewJustSeeked, setPreviewLooping } from '../playback/play-range';
import { reconcilePlayingAfterMutation } from '../playback/playback';
import { getRowEntryForMount } from '../playback/row-registry';
import { _ensureSplitBaseCache, drawSplitWaveform } from '../waveform/split-draw';
import { _fetchPeaksForClick } from '../waveform/utils';
import { _playRange, attachPreviewLoop, exitEditMode } from './common';
import { beginRefEdit, pickProgrammaticMountId } from './reference';
import { commitSplit, finalizeSplit } from './split-commit';
import {
    animateSplitZoomTo,
    applySplitWheelZoom,
    computeRegionView,
    computeSweepDurationMs,
} from './split-zoom';
import { getAudioEndMsForSeg } from './trim';

function _suggestSplitRefs(ref: Parameters<typeof _suggestSplitRefsLib>[0]): ReturnType<typeof _suggestSplitRefsLib> {
    return _suggestSplitRefsLib(ref, getVerseWordCounts());
}

// Re-export draw functions for registration sites.
export { _ensureSplitBaseCache, drawSplitWaveform };

// ---------------------------------------------------------------------------
// enterSplitMode
// ---------------------------------------------------------------------------

export function enterSplitMode(
    seg: Segment,
    row: HTMLElement,
    prePausePlayMs: number | null = null,
    mountId: symbol | null = null,
    initialSplits: number[] | null = null,
    initialRefs: string[] | null = null,
): void {
    if (get(editMode)) {
        console.warn('[split] blocked: already in edit mode:', get(editMode));
        return;
    }
    setEdit('split', seg.segment_uid ?? null, mountId);
    setEditingSegIndex(seg.index);
    setEditStatusText('');

    const canvas = row.querySelector<SegCanvas>('canvas');
    if (!canvas) return;

    const mid = Math.round((seg.time_start + seg.time_end) / 2);
    const inSeg = (t: number): boolean => t > seg.time_start && t < seg.time_end;
    // Cursor seed precedence:
    //  - backend-provided initialSplits (auto-split) wins, after clamping
    //    + ascending-sort so two MFA cuts that landed close together don't
    //    inherit a crossed order;
    //  - else paused-playhead at a single midpoint (today's manual flow);
    //  - else seg midpoint.
    let currentSplits: number[];
    if (initialSplits && initialSplits.length > 0) {
        const cleaned = initialSplits
            .map((t) => Math.round(t))
            .filter(inSeg)
            .sort((a, b) => a - b);
        currentSplits = cleaned.length > 0 ? cleaned : [mid];
    } else if (prePausePlayMs !== null && inSeg(prePausePlayMs)) {
        currentSplits = [Math.round(prePausePlayMs)];
    } else {
        currentSplits = [mid];
    }

    canvas._wfCache = null;

    const chapter = seg.chapter || parseInt(get(selectedChapter));
    const splitAudioUrl = seg.audio_url || get(segAllData)?.audio_by_chapter?.[String(chapter)] || '';
    // Cap viewEnd against the authoritative audio EOF — extraction sometimes
    // leaves the last seg's time_end past actual audio EOF, and then asking
    // for peaks for that range returns truncated/empty data and the canvas
    // paints blank. Mirrors the trim-window clamp.
    const audioEndMs = getAudioEndMsForSeg({ ...seg, chapter });
    const cappedViewEnd = audioEndMs && audioEndMs > seg.time_start
        ? Math.min(seg.time_end, audioEndMs)
        : seg.time_end;
    // Init view = full segment range (no zoom). Reset on every entry — zoom
    // state is intentionally not preserved across edit sessions, mirroring
    // trim mode's wheel-zoom semantics.
    canvas._splitData = {
        seg, currentSplits,
        refs: initialRefs && initialRefs.length === currentSplits.length + 1
            ? initialRefs.slice()
            : undefined,
        viewStart: seg.time_start, viewEnd: cappedViewEnd,
        audioUrl: splitAudioUrl,
    };
    setSplitState({ ...canvas._splitData });
    canvas._splitBaseCache = null;
    // Populate editCanvas store synchronously so click-to-seek in the drag
    // handler (below) reads a non-null canvas on the first user click, before
    // SegmentRow's reactive setEditCanvas has fired. Same rationale as
    // enterTrimMode — avoids the rAF-on-null-canvas no-op.
    setEditCanvas(canvas);
    drawSplitWaveform(canvas);
    setupSplitDragHandle(canvas, seg);

    if (splitAudioUrl && !getWaveformPeaks(splitAudioUrl)) {
        void _fetchPeaksForClick(seg, chapter).then(() => {
            if (!canvas._splitData) return;
            canvas._splitBaseCache = null;
            drawSplitWaveform(canvas);
        });
    }

    // Pick the region to loop. Same rule for manual AND auto-seeded —
    // they only differ in where the cursors got seeded (live playhead
    // for manual, MFA output for auto). The loop target is then:
    //   - Playing → the region that contains the live playhead. Tie-break
    //     at the boundary (the manual binary case where cursor ≡ playhead)
    //     prefers 'right' / forward — no backward seek.
    //   - Paused  → the "second part" — region index 1 (binary 'right',
    //     multi 'region 1'). User can L/R-switch from there.
    //
    // `previewSplitAudio` / `previewSplitRegion` branch warm vs. cold
    // internally based on the live playhead position, so warm-attach is
    // automatic when audio is already running inside the chosen region.
    const target: SplitTarget = prePausePlayMs !== null
        ? splitRegionContaining(seg, currentSplits, prePausePlayMs)
        : (currentSplits.length === 1 ? { kind: 'right' } : { kind: 'region', index: 1 });

    if (target.kind === 'region') {
        setSplitPreviewSelection({ kind: 'region', index: target.index });
        previewSplitRegion(target.index, canvas);
    } else {
        setSplitPreviewSelection({ kind: target.kind });
        previewSplitAudio(target.kind, canvas);
    }
}

// ---------------------------------------------------------------------------
// splitRegionContaining — map a file-absolute ms onto its split region
// ---------------------------------------------------------------------------

type SplitTarget =
    | { kind: 'left' }
    | { kind: 'right' }
    | { kind: 'region', index: number };

/** Pure: which region [start, end) contains `ms` given the current split
 *  cursors. For binary splits (N=1) the answer is `'left'` or `'right'`;
 *  for multi-cursor (N≥2) it's `{ kind: 'region', index }`. At the L/R
 *  boundary (manual entry seeds the cursor at the live playhead, so
 *  `ms === currentSplits[0]`) the tie-break favors `'right'` — forward
 *  direction, no backward seek when warm-attaching. `ms` outside the seg
 *  falls back to the first region. */
function splitRegionContaining(seg: Segment, currentSplits: number[], ms: number): SplitTarget {
    if (currentSplits.length === 1) {
        return ms < currentSplits[0]! ? { kind: 'left' } : { kind: 'right' };
    }
    const n = currentSplits.length;
    for (let i = 0; i <= n; i++) {
        const s = i === 0 ? seg.time_start : currentSplits[i - 1]!;
        const e = i === n ? seg.time_end : currentSplits[i]!;
        if (ms >= s && ms < e) return { kind: 'region', index: i };
    }
    return { kind: 'region', index: 0 };
}

// ---------------------------------------------------------------------------
// setupSplitDragHandle — mouse event handlers for cursor lines
// ---------------------------------------------------------------------------

/** Per-cursor visual x. Off-view cursors collapse to canvas middle so they
 *  remain grab-targetable; only valid in N=1 (single cursor has no neighbour
 *  to dodge). In N≥2 mode the cursor stays on-canvas by construction (its
 *  neighbour clamp keeps it inside the seg span). */
function _splitXFor(canvas: SegCanvas, idx: number): number {
    const sd = canvas._splitData!;
    const t = sd.currentSplits[idx]!;
    const w = canvas.width;
    const span = sd.viewEnd - sd.viewStart;
    if (sd.currentSplits.length === 1
            && (t < sd.viewStart || t > sd.viewEnd)) {
        return w / 2;
    }
    return ((t - sd.viewStart) / span) * w;
}

export function setupSplitDragHandle(canvas: SegCanvas, seg: Segment): void {
    let dragging = false;
    let didDrag = false;
    let dragIdx: number | null = null;

    function _pickCursorIdx(x: number): number | null {
        const sd = canvas._splitData;
        if (!sd) return null;
        let best: number | null = null;
        let bestDist = 15;
        for (let i = 0; i < sd.currentSplits.length; i++) {
            const d = Math.abs(x - _splitXFor(canvas, i));
            if (d < bestDist) { bestDist = d; best = i; }
        }
        return best;
    }

    function onMousedown(e: MouseEvent): void {
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) * (canvas.width / rect.width);
        const sd = canvas._splitData;
        if (!sd) return;
        didDrag = false;
        const idx = _pickCursorIdx(x);
        if (idx !== null) {
            dragging = true;
            dragIdx = idx;
            canvas.style.cursor = 'col-resize';
        }
    }

    function onMousemove(e: MouseEvent): void {
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) * (canvas.width / rect.width);
        const sd = canvas._splitData;
        if (!sd) return;

        if (!dragging) {
            canvas.style.cursor = _pickCursorIdx(x) !== null ? 'col-resize' : 'pointer';
            return;
        }
        if (dragIdx === null) return;
        didDrag = true;
        // Pixel→time uses the visible window; clamp the dragged cursor so it
        // can't cross its neighbours (the previous/next cursor — or the seg
        // start/end at the array ends).
        const timeAtX = sd.viewStart + (x / canvas.width) * (sd.viewEnd - sd.viewStart);
        const snapped = Math.round(timeAtX / EDIT_SNAP_MS) * EDIT_SNAP_MS;
        const minDur = EDIT_MIN_DURATION_MS;
        const lo = (dragIdx > 0 ? sd.currentSplits[dragIdx - 1]! : seg.time_start) + minDur;
        const hi = (dragIdx < sd.currentSplits.length - 1
                    ? sd.currentSplits[dragIdx + 1]!
                    : seg.time_end) - minDur;
        const next = Math.max(lo, Math.min(snapped, hi));
        sd.currentSplits[dragIdx] = next;
        // Force a new array reference so derivedEq subscribers re-fire.
        const cursors = sd.currentSplits.slice();
        updateSplitState((s) => s ? { ...s, currentSplits: cursors } : s);
        drawSplitWaveform(canvas);
    }

    function onMouseup(e: MouseEvent): void {
        if (!dragging && !didDrag) {
            const sd = canvas._splitData;
            // Click-to-seek only meaningful in single-cursor mode; in multi
            // mode the per-region buttons own playback and a stray canvas
            // click is intentionally inert.
            if (sd && sd.currentSplits.length === 1) {
                const rect = canvas.getBoundingClientRect();
                const x = (e.clientX - rect.left) * (canvas.width / rect.width);
                const timeAtX = sd.viewStart + (x / canvas.width) * (sd.viewEnd - sd.viewStart);
                const split = sd.currentSplits[0]!;
                if (timeAtX < split) _playRange(timeAtX, split);
                else _playRange(timeAtX, seg.time_end);
            }
        }
        dragging = false;
        dragIdx = null;
        canvas.style.cursor = '';
    }
    function onMouseleave(): void { dragging = false; dragIdx = null; canvas.style.cursor = ''; }

    function onWheel(e: WheelEvent): void {
        if (dragging) return;
        e.preventDefault();
        applySplitWheelZoom(canvas, e.clientX, e.deltaY);
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
// nudgeSplitCursor — step an arbitrary cursor by ±deltaMs (SplitPanel only)
// ---------------------------------------------------------------------------

/**
 * Move split cursor `idx` by `deltaMs`, clamped against its neighbours
 * (the previous/next cursor, or the seg start/end at the array ends) with
 * `EDIT_MIN_DURATION_MS` headroom on each side — same clamp the drag handle
 * uses in `setupSplitDragHandle`. Returns the resolved time, or `null` when
 * there's no active split or `idx` is out of range.
 *
 * Used by SplitPanel for both modes: binary steps cursor 0; multi steps the
 * left- or right-edge cursor of the currently selected region.
 */
export function nudgeSplitCursor(idx: number, deltaMs: number): number | null {
    const canvas = get(editCanvas);
    const sd = canvas?._splitData;
    if (!canvas || !sd) return null;
    const cursors = sd.currentSplits;
    if (idx < 0 || idx >= cursors.length) return null;
    const { seg } = sd;
    const minDur = EDIT_MIN_DURATION_MS;
    const cur = cursors[idx]!;
    // Off-view cursor (zoomed past it) → anchor at the view centre so the step
    // lands somewhere visible rather than nudging an invisible point.
    const onView = cur >= sd.viewStart && cur <= sd.viewEnd;
    const anchor: number = onView ? cur : (sd.viewStart + sd.viewEnd) / 2;
    const lo = (idx > 0 ? cursors[idx - 1]! : seg.time_start) + minDur;
    const hi = (idx < cursors.length - 1 ? cursors[idx + 1]! : seg.time_end) - minDur;
    const next = Math.max(lo, Math.min(anchor + deltaMs, hi));
    if (next === cur) return next;
    cursors[idx] = next;
    // Force a new array reference so derivedEq subscribers re-fire.
    const updated = cursors.slice();
    updateSplitState((s) => s ? { ...s, currentSplits: updated } : s);
    drawSplitWaveform(canvas);
    return next;
}

/** Binary-mode convenience: step the single cursor (index 0). */
export function nudgeSplitBoundary(deltaMs: number): number | null {
    return nudgeSplitCursor(0, deltaMs);
}

// ---------------------------------------------------------------------------
// Selection switching — shared by SplitPanel clicks AND keyboard Tab cycling.
// Each switch also previews (plays) the newly-selected range; multi-cursor
// region switches additionally zoom the view to frame the region.
// ---------------------------------------------------------------------------

/** Select + cold-play a binary L/R side. */
export function selectSplitSide(side: 'left' | 'right', canvas?: SegCanvas | null): void {
    setSplitPreviewSelection({ kind: side });
    previewSplitAudio(side, canvas ?? get(editCanvas), { mode: 'cold' });
}

/** Select + cold-play a multi-cursor region, zooming the view to frame it. */
export function selectSplitRegion(idx: number, canvas?: SegCanvas | null): void {
    setSplitPreviewSelection({ kind: 'region', index: idx });
    previewSplitRegion(idx, canvas ?? get(editCanvas), { mode: 'cold', zoom: true });
}

/** Map the current preview selection onto the cursor index the keyboard
 *  stepper should move. Binary → cursor 0; region `i` → its right-edge cursor
 *  (`i`) when one exists, else the left-edge cursor (last region). */
function activeSplitCursorIndex(cursorCount: number): number {
    if (cursorCount <= 1) return 0;
    const sel = get(splitPreviewSelection);
    const i = sel.kind === 'region' ? sel.index : sel.kind === 'left' ? 0 : cursorCount;
    return Math.max(0, Math.min(i, cursorCount - 1));
}

/**
 * Cycle the split selection forward/back (Tab in split mode), switching which
 * region is previewed/played. Binary mode toggles L↔R; multi-cursor mode steps
 * through the N+1 regions (wrapping) and zooms to each. Mirrors the SplitPanel
 * pill clicks, so the >2-cursor zoom path fires here too.
 */
export function cycleSplitSelection(dir: 1 | -1, canvas?: SegCanvas | null): void {
    const c = canvas ?? get(editCanvas);
    const sd = c?._splitData;
    if (!sd) return;
    const n = sd.currentSplits.length;
    if (n === 1) {
        const sel = get(splitPreviewSelection);
        const cur: 'left' | 'right' = sel.kind === 'right' ? 'right' : 'left';
        selectSplitSide(cur === 'left' ? 'right' : 'left', c);
        return;
    }
    const regionCount = n + 1;
    const sel = get(splitPreviewSelection);
    const curIdx = sel.kind === 'region' ? sel.index : sel.kind === 'left' ? 0 : regionCount - 1;
    const nextIdx = (curIdx + dir + regionCount) % regionCount;
    selectSplitRegion(nextIdx, c);
}

/** Nudge the keyboard-active split cursor by `deltaMs` (←/→ in split mode). */
export function nudgeActiveSplitCursor(deltaMs: number): number | null {
    const c = get(editCanvas);
    const n = c?._splitData?.currentSplits.length ?? 0;
    if (n === 0) return null;
    return nudgeSplitCursor(activeSplitCursorIndex(n), deltaMs);
}

/** Replay (cold-start) the currently-selected split region/side without
 *  changing the selection or zooming — the keyboard `R` equivalent of the
 *  region-pill / footer-▶ replay. */
export function replayCurrentSplitSelection(canvas?: SegCanvas | null): void {
    const c = canvas ?? get(editCanvas);
    const sd = c?._splitData;
    if (!sd) return;
    const n = sd.currentSplits.length;
    const sel = get(splitPreviewSelection);
    if (n === 1) {
        previewSplitAudio(sel.kind === 'right' ? 'right' : 'left', c, { mode: 'cold' });
    } else {
        const idx = sel.kind === 'region' ? sel.index : sel.kind === 'left' ? 0 : n;
        previewSplitRegion(idx, c, { mode: 'cold' });
    }
}

// ---------------------------------------------------------------------------
// confirmSplit — apply the split and chain ref editing
// ---------------------------------------------------------------------------

export function confirmSplit(
    seg: Segment,
    canvas?: SegCanvas | null,
    mountId: symbol | null = null,
): void {
    const c = canvas ?? get(editCanvas);
    const sd = c?._splitData;
    if (!c || !sd) return;
    const cursors = sd.currentSplits;
    if (!cursors.length) return;

    const chStr = get(selectedChapter);
    const chapter = seg.chapter || parseInt(chStr);
    const initiatingEntry = mountId
        ? getRowEntryForMount(chapter, seg.index, mountId)
        : null;

    // Auto-split provides refs[] directly (cross-verse N=2 or repetition
    // N≥2); commitSplit falls back to the binary cross-verse suggestion
    // for the single-cursor case.
    const refs = sd.refs && sd.refs.length === cursors.length + 1 ? sd.refs.slice() : null;
    const commit = commitSplit(seg, cursors, { refs });
    if (!commit) return;
    const { pieces, contextCategory: chainCat } = commit;

    exitEditMode();
    finalizeSplit(commit);

    if (initiatingEntry?.instanceRole !== 'accordion') {
        targetSegmentIndex.set({ chapter, index: pieces[0]!.index });
    }

    const resolvedMountId = mountId ?? pickProgrammaticMountId(chapter, pieces[0]!.index);
    if (!resolvedMountId) return;

    // Chain ref edits for every piece after the first. Each one shifts the
    // queue head and seeds `beginRefEdit` with the next segment + its own
    // per-piece originalEnd anchor (used for the trailing endpoint when the
    // user edits the prior piece's ref).
    //
    // Use each piece's OWN matched_ref end as its anchor — not the parent
    // seg's full end. For binary cross-verse (N+1=2 pieces) the second piece's
    // ref ends at the parent's end, so the two coincide. For N+1≥3 (auto-split
    // across 3+ verses), each non-final piece's correct anchor is the end of
    // its own verse — otherwise the chain prefill spans `verse_i_start -
    // last_verse_end` instead of `verse_i_start - verse_i_end`, breaking the
    // per-verse-resolution pattern the binary case already has.
    const isCrossVerseSplit = chainCat === 'cross_verse';
    const queue = pieces.slice(1).map((p, i) => {
        const pParsed = parseSegRef(p.matched_ref);
        const originalEndRef = pParsed
            ? `${pParsed.surah}:${pParsed.ayah_to}:${pParsed.word_to}`
            : null;
        const prevPiece = pieces[i]!;
        return {
            seg: p,
            category: chainCat,
            originalEndRef,
            // For CV splits, name the previous piece so _handoffPendingChain
            // can gate this entry's ref-edit on the user picking WASL/WAQF
            // first. Non-CV splits leave the field unset and proceed
            // straight to ref-edit, same as today.
            ...(isCrossVerseSplit && prevPiece.segment_uid
                ? { prevPieceUid: prevPiece.segment_uid }
                : {}),
        };
    });
    pendingChainTargets.set(queue);

    // For cross-verse splits, mark every new inter-piece boundary as pending
    // a WASL/WAQF answer. The inline ``WaslBoundary`` picker inside the CV
    // accordion card reads this set to render both labels muted (forcing a
    // pick) vs. the committed reading.
    if (isCrossVerseSplit) {
        // pieces[0..N-2] are the LEFT side of each new inter-piece boundary.
        for (let i = 0; i < pieces.length - 1; i++) {
            const uid = pieces[i]!.segment_uid;
            if (uid) markWaslPending(uid);
        }
    }

    beginRefEdit(pieces[0]!, chainCat, resolvedMountId);
}

// ---------------------------------------------------------------------------
// previewSplitAudio — cold-start the binary-split L/R preview loop
// ---------------------------------------------------------------------------

/** Launch a binary-split (N=1) left/right preview loop.
 *
 *  Default (`mode: 'auto'`):
 *  - Playing → warm-attach without seeking. Audio continues; if it's
 *    already past the region's end, prime `_previewJustSeeked` to skip
 *    the wrap-back on the first frame.
 *  - Paused → cold-start via `_playRange`.
 *
 *  `mode: 'cold'` — always cold-start. Used by SplitPanel's L/R pill
 *  click ("play THIS side from its start, looping"). */
export function previewSplitAudio(
    side: 'left' | 'right',
    canvas?: SegCanvas | null,
    opts?: { mode?: 'auto' | 'cold' },
): void {
    const c = canvas ?? get(editCanvas);
    const sd = c?._splitData;
    if (!sd || !c || sd.currentSplits.length !== 1) return;
    editPreviewPlaying.set(true);
    setPreviewLooping(`split-${side}` as const);
    const splitTime = sd.currentSplits[0]!;
    const startMs = side === 'left' ? sd.seg.time_start : splitTime;
    const endMs = side === 'left' ? splitTime : sd.seg.time_end;

    if (opts?.mode === 'cold' || segPort.paused) {
        _playRange(startMs, endMs);
        return;
    }
    const live = segPort.currentTimeMs();
    setPreviewJustSeeked(live >= endMs);
    attachPreviewLoop(startMs, endMs);
}

// ---------------------------------------------------------------------------
// previewSplitRegion — cold-start a multi-cursor (N≥2) region preview loop
// ---------------------------------------------------------------------------

/** Loop region ``i`` (0-indexed) of the split. Region ``i`` runs from
 *  ``currentSplits[i-1] ?? seg.time_start`` to ``currentSplits[i] ?? seg.time_end``,
 *  so for N cursors there are N+1 regions. Sets the play-range loop key to
 *  ``'split-region-{i}'`` so the play-range RAF re-seeks correctly across
 *  cursor edits while looping. Same warm/cold branching as
 *  ``previewSplitAudio`` — `mode: 'cold'` forces cold-start for SplitPanel's
 *  region-pill click.
 *
 *  `zoom: true` (SplitPanel's pill click) animates the view window to frame
 *  this region with padding — a pan/zoom sweep from the current view. Omitted
 *  on the entry-time preview (`enterSplitMode`) so the initial auto-split
 *  selection stays fully zoomed out; only user clicks (switch OR replay-same)
 *  zoom. Only reachable in the multi-cursor regime (binary L/R uses
 *  ``previewSplitAudio``), so the "regions > 2" scope holds implicitly. */
export function previewSplitRegion(
    idx: number,
    canvas?: SegCanvas | null,
    opts?: { mode?: 'auto' | 'cold'; zoom?: boolean },
): void {
    const c = canvas ?? get(editCanvas);
    const sd = c?._splitData;
    if (!sd || !c) return;
    const n = sd.currentSplits.length;
    if (idx < 0 || idx > n) return;
    const start = idx === 0 ? sd.seg.time_start : sd.currentSplits[idx - 1]!;
    const end = idx === n ? sd.seg.time_end : sd.currentSplits[idx]!;

    if (opts?.zoom) {
        const target = computeRegionView(start, end, sd.seg.time_start, sd.seg.time_end);
        const from = { viewStart: sd.viewStart, viewEnd: sd.viewEnd };
        animateSplitZoomTo(c, target, computeSweepDurationMs(from, target));
    }

    editPreviewPlaying.set(true);
    setPreviewLooping(`split-region-${idx}` as `split-region-${number}`);

    if (opts?.mode === 'cold' || segPort.paused) {
        _playRange(start, end);
        return;
    }
    const live = segPort.currentTimeMs();
    setPreviewJustSeeked(live >= end);
    attachPreviewLoop(start, end);
}
