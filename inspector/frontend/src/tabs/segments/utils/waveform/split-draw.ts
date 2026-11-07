/**
 * Canvas drawing functions for split mode — the waveform base cache,
 * split line, and right-region tinting.
 */

import { themeColor } from '../../../../lib/utils/canvas-theme';
import type { SegCanvas } from '../../types/segments-waveform';
import { drawEditPeakBase } from './draw-seg';
import { readback2d } from '../../../../lib/utils/canvas-2d';

// ---------------------------------------------------------------------------
// _ensureSplitBaseCache
// ---------------------------------------------------------------------------

export function _ensureSplitBaseCache(canvas: SegCanvas): boolean {
    const ctx = readback2d(canvas);
    if (!ctx) return false;
    if (canvas._splitBaseCache) return true;
    const sd = canvas._splitData;
    if (!sd) return false;
    const width = canvas.width;
    const height = canvas.height;

    // Slice peaks for the VISIBLE window (not the whole segment) — wheel zoom
    // rebuilds this cache after dropping the prior ImageData via
    // `_splitBaseCache = null`. drawEditPeakBase paints the shared bg + fill
    // + top/bottom outline so split matches every other surface. On miss
    // (peaks not yet fetched) return false without overlay text — enterSplitMode
    // kicks off a fetch and re-draws once peaks land; matches trim's behaviour.
    if (!drawEditPeakBase(canvas, sd.audioUrl || '', sd.viewStart, sd.viewEnd)) {
        return false;
    }

    canvas._splitBaseCache = ctx.getImageData(0, 0, width, height);
    return true;
}

// ---------------------------------------------------------------------------
// drawSplitWaveform
// ---------------------------------------------------------------------------

export function drawSplitWaveform(canvas: SegCanvas): void {
    const c = canvas;
    const hasCachedBase = _ensureSplitBaseCache(c);
    const ctx = readback2d(c);
    if (!ctx) return;
    const width = c.width;
    const height = c.height;
    const sd = c._splitData;
    if (!sd) return;

    if (hasCachedBase && c._splitBaseCache) ctx.putImageData(c._splitBaseCache, 0, 0);

    const span = sd.viewEnd - sd.viewStart;
    const cursors = sd.currentSplits;
    const N = cursors.length;

    // Region tinting: alternate-shade so cuts read as section breaks.
    // Region i runs from cursor[i-1] (or viewStart) to cursor[i] (or viewEnd)
    // in time, projected onto canvas x. Odd-indexed regions get the
    // orange tint — matches today's "shade the right half" for N=1 and
    // gives N≥2 a stable visual rhythm.
    ctx.fillStyle = themeColor('--wf-region-tint', 'rgba(255, 152, 0, 0.15)');
    for (let i = 0; i <= N; i++) {
        if (i % 2 === 0) continue;
        const t0 = i === 0 ? sd.viewStart : cursors[i - 1]!;
        const t1 = i === N ? sd.viewEnd : cursors[i]!;
        const x0 = Math.max(0, Math.min(width, ((t0 - sd.viewStart) / span) * width));
        const x1 = Math.max(0, Math.min(width, ((t1 - sd.viewStart) / span) * width));
        if (x1 > x0) ctx.fillRect(x0, 0, x1 - x0, height);
    }

    // Cursor lines. In single-cursor mode an off-view cursor visually clamps
    // to canvas middle (today's grab-target behaviour); in multi-cursor mode
    // neighbours can't cross so cursors stay on-canvas by construction.
    ctx.strokeStyle = themeColor('--wf-split-cursor', '#ffeb3b');
    ctx.lineWidth = 3;
    for (let i = 0; i < N; i++) {
        const t = cursors[i]!;
        let x: number;
        if (N === 1) {
            const sxRaw = ((t - sd.viewStart) / span) * width;
            const off = t < sd.viewStart || t > sd.viewEnd;
            x = off ? width / 2 : sxRaw;
        } else {
            x = ((t - sd.viewStart) / span) * width;
            if (x < 0 || x > width) continue;
        }
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
    }
}
