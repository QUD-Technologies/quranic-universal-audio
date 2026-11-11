import { get } from 'svelte/store';

import type { AudioPeaks, PeakBucket } from '../../../../lib/types/peaks-transport';
import type { Segment } from '../../../../lib/types/view-models';
import { themeColor } from '../../../../lib/utils/canvas-theme';
import {
    PREVIEW_PLAYHEAD_COLOR,
    WAVEFORM_BG_COLOR,
    WAVEFORM_DIM_OVERLAY_COLOR,
} from '../../../../lib/utils/constants';
import { viewPeaks } from '../../../../lib/utils/peaks-view';
import { getWaveformPeaks } from '../../../../lib/utils/waveform-cache';
import { drawWaveformPeaks } from '../../../../lib/utils/waveform-draw';
import { segAllData } from '../../stores/chapter';
import type { SegCanvas } from '../../types/segments-waveform';
import { _findCoveringPeaks } from './peaks-cache';
import { readback2d } from '../../../../lib/utils/canvas-2d';

type Peaks = PeakBucket[] | Int8Array;

/**
 * Draw a waveform from a full peaks array for a sub-range [startMs, endMs].
 * Delegates the draw algorithm to lib/utils/waveform-draw.ts::drawWaveformPeaks,
 * then clears the SegCanvas image-data cache so the next playhead draw
 * picks up the fresh waveform.
 */
export function drawSegmentWaveformFromPeaks(
    canvas: SegCanvas,
    startMs: number,
    endMs: number,
    peaks: Peaks,
    totalDurationMs: number,
): void {
    const ctx = readback2d(canvas);
    if (!ctx) return;
    drawWaveformPeaks(ctx, peaks, {
        width: canvas.width,
        height: canvas.height,
        startMs,
        endMs,
        totalDurationMs,
    });
    canvas._wfCache = null;
}

/** Draw waveform from peaks for a segment, resolving its audio URL. Returns true if drawn. */
export function drawWaveformFromPeaksForSeg(canvas: SegCanvas, seg: Segment, chapter: number | string): boolean {
    const audioUrl = seg.audio_url || get(segAllData)?.audio_by_chapter?.[String(chapter)] || '';
    const pe = getWaveformPeaks(audioUrl);
    if (pe?.peaks?.length) {
        drawSegmentWaveformFromPeaks(canvas, seg.time_start, seg.time_end, pe.peaks, pe.duration_ms);
        return true;
    }
    const covering = _findCoveringPeaks(audioUrl, seg.time_start, seg.time_end);
    if (covering?.peaks?.length) {
        const rs = covering.start_ms ?? 0;
        drawSegmentWaveformFromPeaks(canvas, seg.time_start - rs, seg.time_end - rs, covering.peaks as Peaks, covering.duration_ms);
        return true;
    }
    return false;
}

/**
 * Bg fill → waveform → history overlays → capture `_wfCache`.
 *
 * `startMs`/`endMs` describe the canvas's *visual* range — wider than the
 * playback range for split leaves (parent's union peak with the leaf
 * slice highlighted in green). The split / trim / merge overlay
 * descriptors live on the canvas itself (`_splitHL`, `_trimHL`,
 * `_mergeHL`); applying them here means they're baked into the cached
 * ImageData and survive every subsequent `putImageData` blit during
 * playback.
 *
 * Returns true when real peaks were drawn — the IntersectionObserver
 * uses this to decide whether to keep observing. `drawSegPlayhead`
 * ignores the return; its cache always captures something so the
 * playhead has a clean base to draw on.
 */
export function drawSegBaseAndOverlays(
    canvas: SegCanvas,
    startMs: number,
    endMs: number,
    audioUrl: string,
): boolean {
    const ctx = readback2d(canvas);
    if (!ctx) return false;
    ctx.fillStyle = themeColor('--wf-bg', WAVEFORM_BG_COLOR);
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    let drew = false;
    if (audioUrl) {
        const pe = getWaveformPeaks(audioUrl);
        if (pe?.peaks?.length) {
            drawWaveformPeaks(ctx, pe.peaks, {
                width: canvas.width,
                height: canvas.height,
                startMs,
                endMs,
                totalDurationMs: pe.duration_ms,
            });
            drew = true;
        } else {
            const covering = _findCoveringPeaks(audioUrl, startMs, endMs);
            if (covering?.peaks?.length) {
                const rs = covering.start_ms ?? 0;
                drawWaveformPeaks(ctx, covering.peaks as Peaks, {
                    width: canvas.width,
                    height: canvas.height,
                    startMs: startMs - rs,
                    endMs: endMs - rs,
                    totalDurationMs: covering.duration_ms,
                });
                drew = true;
            }
        }
    }

    // Synthetic seg matching the canvas's visual range — overlay math
    // reads `seg.time_start/time_end` as the canvas's full-width bounds.
    const visSeg = { time_start: startMs, time_end: endMs, audio_url: audioUrl } as Segment;
    _drawSplitHighlight(canvas, visSeg);
    _drawTrimHighlight(canvas, visSeg);
    _drawMergeHighlight(canvas, visSeg);

    canvas._wfCache = ctx.getImageData(0, 0, canvas.width, canvas.height);
    canvas._wfCacheKey = `${startMs}:${endMs}`;
    return drew;
}

export function drawSegPlayhead(
    canvas: SegCanvas,
    startMs: number,
    endMs: number,
    currentTimeMs: number,
    audioUrl: string,
): void {
    const ctx = readback2d(canvas);
    if (!ctx) return;
    const cacheKey = `${startMs}:${endMs}`;
    if (canvas._wfCache && canvas._wfCacheKey === cacheKey) {
        ctx.putImageData(canvas._wfCache, 0, 0);
    } else {
        // Cache miss: rebuild bg + waveform + overlays so the cached
        // ImageData carries history overlays through every subsequent
        // tick. Without this, green/red overlays disappear the moment
        // playback starts (the cache rebuild used to capture the base
        // waveform only).
        drawSegBaseAndOverlays(canvas, startMs, endMs, audioUrl);
    }

    if (currentTimeMs < startMs || currentTimeMs > endMs) return;

    const width = canvas.width;
    const height = canvas.height;
    const progress = (currentTimeMs - startMs) / (endMs - startMs);
    const x = progress * width;

    const playheadColor = themeColor('--wf-preview-playhead', PREVIEW_PLAYHEAD_COLOR);
    ctx.strokeStyle = playheadColor;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();

    ctx.fillStyle = playheadColor;
    ctx.beginPath();
    ctx.moveTo(x - 4, 0);
    ctx.lineTo(x + 4, 0);
    ctx.lineTo(x, 6);
    ctx.closePath();
    ctx.fill();
}

interface SlicedPeaks {
    maxVals: Float32Array;
    minVals: Float32Array;
}

/**
 * Draw the shared blue peak-fill base used by trim and split edit modes.
 *
 * Slices peaks for `[startMs, endMs]` via the block-min/max resampler in
 * `_slicePeaks` (preserves transients on short clips), zips the per-pixel
 * min/max into a `PeakBucket[]` at canvas resolution, and delegates the
 * actual fill+stroke render to `drawWaveformPeaks` so every consumer shares
 * the same look (translucent fill + top-and-bottom 1px outline).
 *
 * Returns true when peaks were drawn, false when no peak data is available
 * — callers that need a textual "no data" fallback (e.g. split) render it
 * themselves on the false branch.
 */
export function drawEditPeakBase(
    canvas: SegCanvas,
    audioUrl: string,
    startMs: number,
    endMs: number,
): boolean {
    const ctx = readback2d(canvas);
    if (!ctx) return false;
    const width = canvas.width;
    const height = canvas.height;

    const data = _slicePeaks(audioUrl, startMs, endMs, width);
    if (!data) {
        ctx.fillStyle = themeColor('--wf-bg', WAVEFORM_BG_COLOR);
        ctx.fillRect(0, 0, width, height);
        return false;
    }

    // Zip the resampled per-pixel min/max into PeakBucket[min, max] at canvas
    // resolution. drawWaveformPeaks then paints bg + closed polygon (fill +
    // top/bottom stroke); its internal sampling becomes 1:1 at this density,
    // so the block-resampler's transients survive intact.
    const buckets: PeakBucket[] = new Array(width);
    for (let i = 0; i < width; i++) {
        buckets[i] = [data.minVals[i] ?? 0, data.maxVals[i] ?? 0];
    }
    drawWaveformPeaks(ctx, buckets, { width, height });

    return true;
}

/** Slice peaks for a time range and resample to `buckets` bins. */
function _slicePeaks(
    audioUrl: string,
    startMs: number,
    endMs: number,
    buckets: number,
): SlicedPeaks | null {
    let pe: AudioPeaks | undefined = getWaveformPeaks(audioUrl);
    if (!pe?.peaks) {
        pe = _findCoveringPeaks(audioUrl, startMs, endMs) ?? undefined;
    }
    if (!pe) return null;
    // Shape-agnostic length: PeakBucket[] uses .length; Int8Array(2N) uses
    // length >> 1 since values are interleaved (mn, mx) bytes.
    const peaksLen = pe.peaks instanceof Int8Array
        ? pe.peaks.length >> 1
        : (pe.peaks?.length ?? 0);
    if (peaksLen === 0) return null;
    const rs = pe.start_ms ?? 0;
    const pps = peaksLen / pe.duration_ms;
    const startIdx = Math.max(0, Math.floor((startMs - rs) * pps));
    const endIdx = Math.min(peaksLen, Math.ceil((endMs - rs) * pps));
    // Zero-copy slice in BYTES for Int8Array (factor of 2 because each
    // bucket is 2 bytes); Array.slice for the nested shape.
    const slice = pe.peaks instanceof Int8Array
        ? pe.peaks.subarray(startIdx * 2, endIdx * 2)
        : pe.peaks.slice(startIdx, endIdx);
    const sliceLen = slice instanceof Int8Array ? slice.length >> 1 : slice.length;
    if (sliceLen === 0) return null;
    const view = viewPeaks(slice);
    if (!view) return null;
    const maxVals = new Float32Array(buckets);
    const minVals = new Float32Array(buckets);
    // Map canvas bucket i ∈ [0, buckets) to slice-local fractional index
    // by going through ABSOLUTE TIME, not by stretching the slice across
    // the canvas. Fixes the bucket-snap drift the floor/ceil above
    // introduces: at 10 bps (post slim-peaks migration) each peak covers
    // 100 ms, so a segment shifted by half a bucket gets drawn ~50 px off
    // on a 600 px canvas. At 30 bps the same drift was ~17 px and looked
    // like rendering noise; at 10 bps it's a misaligned silence.
    const sliceIdxForFrac = (frac: number): number => {
        const tMs = startMs + frac * (endMs - startMs);
        return (tMs - rs) * pps - startIdx;
    };
    if (sliceLen >= buckets) {
        for (let i = 0; i < buckets; i++) {
            const from = Math.max(0, Math.floor(sliceIdxForFrac(i / buckets)));
            const to = Math.min(sliceLen, Math.ceil(sliceIdxForFrac((i + 1) / buckets)));
            let mx = -1, mn = 1;
            for (let j = from; j < to; j++) {
                const bMax = view.max(j);
                const bMin = view.min(j);
                if (bMax > mx) mx = bMax;
                if (bMin < mn) mn = bMin;
            }
            // Empty span (e.g. canvas bucket smaller than one peak bucket
            // at the edges) — fall through to silence so the canvas
            // doesn't show -1/+1 clipping artifacts.
            if (mx < mn) { mx = 0; mn = 0; }
            maxVals[i] = mx;
            minVals[i] = mn;
        }
    } else {
        for (let i = 0; i < buckets; i++) {
            const fi = sliceIdxForFrac((buckets > 1 ? i / (buckets - 1) : 0));
            if (fi < 0 || fi > sliceLen - 1) {
                minVals[i] = 0; maxVals[i] = 0;
                continue;
            }
            const lo = Math.floor(fi);
            const hi = Math.min(lo + 1, sliceLen - 1);
            const t = fi - lo;
            minVals[i] = view.min(lo) * (1 - t) + view.min(hi) * t;
            maxVals[i] = view.max(lo) * (1 - t) + view.max(hi) * t;
        }
    }
    return { maxVals, minVals };
}

/** Draw red/green overlay on history card waveforms to show trim changes.
 *
 * Paints the canvas region OUTSIDE [otherStart, otherEnd], optionally
 * intersected with [clipStart, clipEnd]. The clip is used by split-leaf
 * cards whose canvas spans the chain's wider union range — without it the
 * delta paint bleeds onto sibling leaves' portions of the canvas.
 */
export function _drawTrimHighlight(canvas: SegCanvas, seg: Segment): void {
    const hl = canvas._trimHL;
    if (!hl) return;
    const ctx = readback2d(canvas);
    if (!ctx) return;
    const w = canvas.width, h = canvas.height;
    const dur = seg.time_end - seg.time_start;
    if (dur <= 0) return;

    const clipStart = hl.clipStart ?? seg.time_start;
    const clipEnd = hl.clipEnd ?? seg.time_end;
    const toX = (ms: number): number => ((ms - seg.time_start) / dur) * w;

    const rgba = hl.color === 'red'
        ? themeColor('--wf-delta-remove', 'rgba(244, 67, 54, 0.3)')
        : themeColor('--wf-delta-add', 'rgba(76, 175, 80, 0.3)');
    ctx.fillStyle = rgba;

    // Left region: canvas ∩ clip ∩ [-∞, otherStart]
    {
        const lo = Math.max(seg.time_start, clipStart);
        const hi = Math.min(hl.otherStart, clipEnd, seg.time_end);
        if (hi > lo) {
            const x1 = Math.max(0, toX(lo));
            const x2 = Math.min(w, toX(hi));
            if (x2 > x1) ctx.fillRect(x1, 0, x2 - x1, h);
        }
    }
    // Right region: canvas ∩ clip ∩ [otherEnd, +∞]
    {
        const lo = Math.max(hl.otherEnd, clipStart, seg.time_start);
        const hi = Math.min(seg.time_end, clipEnd);
        if (hi > lo) {
            const x1 = Math.max(0, toX(lo));
            const x2 = Math.min(w, toX(hi));
            if (x2 > x1) ctx.fillRect(x1, 0, x2 - x1, h);
        }
    }
}

/** Draw dim + green overlay on split chain after-card waveforms. */
export function _drawSplitHighlight(canvas: SegCanvas, wfSeg: Segment): void {
    const hl = canvas._splitHL;
    if (!hl) return;
    const ctx = readback2d(canvas);
    if (!ctx) return;
    const w = canvas.width, h = canvas.height;
    const dur = wfSeg.time_end - wfSeg.time_start;
    if (dur <= 0) return;
    const toX = (ms: number): number => Math.max(0, Math.min(w, ((ms - wfSeg.time_start) / dur) * w));

    const hlStart = hl.hlStart ?? hl.wfStart;
    const hlEnd = hl.hlEnd ?? hl.wfEnd;
    const x1 = toX(hlStart);
    const x2 = toX(hlEnd);

    ctx.fillStyle = themeColor('--wf-dim-overlay', WAVEFORM_DIM_OVERLAY_COLOR);
    if (x1 > 0) ctx.fillRect(0, 0, x1, h);
    if (x2 < w) ctx.fillRect(x2, 0, w - x2, h);

    ctx.fillStyle = themeColor('--wf-delta-add', 'rgba(76, 175, 80, 0.3)');
    if (x2 > x1) ctx.fillRect(x1, 0, x2 - x1, h);
}

/** Draw yellow cursor on merge result card showing the point of merge. */
export function _drawMergeHighlight(canvas: SegCanvas, seg: Segment): void {
    const hl = canvas._mergeHL;
    if (!hl) return;
    const ctx = readback2d(canvas);
    if (!ctx) return;
    const w = canvas.width, h = canvas.height;
    const dur = seg.time_end - seg.time_start;
    if (dur <= 0) return;
    const toX = (ms: number): number => Math.max(0, Math.min(w, ((ms - seg.time_start) / dur) * w));

    const x = toX(hl.mergePoint);

    const mergeColor = themeColor('--wf-merge-cursor', '#eab308');
    ctx.strokeStyle = mergeColor;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();

    ctx.fillStyle = mergeColor;
    ctx.beginPath();
    ctx.moveTo(x - 4, 0);
    ctx.lineTo(x + 4, 0);
    ctx.lineTo(x, 6);
    ctx.closePath();
    ctx.fill();
}
