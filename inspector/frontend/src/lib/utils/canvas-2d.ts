/**
 * 2D context for a canvas whose pixels are read back with `getImageData`.
 *
 * The Segments waveforms snapshot their base render on almost every draw and
 * blit it back under the playhead/overlays. On a GPU-accelerated canvas each
 * readback forces a GPU→CPU sync; across a long editing session with many rows
 * that churn has crashed the renderer (Chromium/Edge
 * `STATUS_INTEGER_DIVIDE_BY_ZERO`). `willReadFrequently` keeps the canvas on the
 * CPU so readbacks are plain memory copies.
 *
 * The FIRST `getContext('2d', …)` call on a canvas fixes its attributes, so every
 * call site touching these canvases must go through this helper.
 */
export function readback2d(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
    return canvas.getContext('2d', { willReadFrequently: true });
}
