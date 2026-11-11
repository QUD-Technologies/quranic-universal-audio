<script lang="ts">
    /**
     * WaveformCanvas — base canvas primitive for waveform display.
     *
     * Renders a peaks-based waveform using drawWaveformPeaks() from
     * lib/utils/waveform-draw.ts. Overlays (trim handles, split line, merge
     * highlight, playhead) are added by extension components.
     *
     * lib/utils/waveform-draw.ts is the portable pure helper.
     */

    import { onMount } from 'svelte';

    import { THEME_CHANGE_EVENT } from '../stores/theme.svelte';
    import type { PeakBucket } from '../types/peaks-transport';
    import { themeColor } from '../utils/canvas-theme';
    import { readback2d } from '../utils/canvas-2d';
    import { drawWaveformPeaks } from '../utils/waveform-draw';

    /** Peak data to render. null = show empty (black) canvas. Both nested
     *  `PeakBucket[]` and the drawer-int8 `Int8Array(2N)` are accepted;
     *  `drawWaveformPeaks` branches on shape internally. */
    export let peaks: PeakBucket[] | Int8Array | null = null;
    /** Canvas display width in pixels. */
    export let width = 300;
    /** Canvas display height in pixels. */
    export let height = 60;
    /** Optional inline style string forwarded to the <canvas> element. */
    export let style = '';

    /**
     * Sub-range start in milliseconds. When startMs, endMs, and
     * totalDurationMs are all provided, only that time slice of peaks
     * is rendered. Omit all three to draw the full array.
     */
    export let startMs: number | undefined = undefined;
    /** Sub-range end in milliseconds. See startMs. */
    export let endMs: number | undefined = undefined;
    /**
     * Total duration that the full peaks array covers, in milliseconds.
     * Required when startMs/endMs are set. See startMs.
     */
    export let totalDurationMs: number | undefined = undefined;
    /** Keep the canvas on the CPU — set by callers that read it back with
     *  getImageData on every draw (see lib/utils/canvas-2d.ts). */
    export let willReadFrequently = false;

    let canvas: HTMLCanvasElement;

    // Reactive trigger fires whenever any drawing input changes. Optional
    // sub-range props (startMs/endMs/totalDurationMs) must NOT gate the
    // condition — when callers omit them they're undefined (falsy) and the
    // guard would never match. drawWaveformPeaks handles undefined natively.
    $: if (canvas && peaks && width && height && (startMs, endMs, totalDurationMs, true)) redraw();
    $: if (canvas && !peaks) clearCanvas();

    function redraw(): void {
        if (!canvas || !peaks) return;
        const ctx = (willReadFrequently ? readback2d(canvas) : canvas.getContext('2d'));
        if (!ctx) return;
        drawWaveformPeaks(ctx, peaks, { width: canvas.width, height: canvas.height, startMs, endMs, totalDurationMs });
    }

    function clearCanvas(): void {
        const ctx = (willReadFrequently ? readback2d(canvas) : canvas.getContext('2d'));
        if (!ctx) return;
        ctx.fillStyle = themeColor('--wf-bg', '#0f0f23');
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }

    /** Repaint with the active theme's colours (peaks → redraw, else clear). */
    function repaint(): void {
        if (!canvas) return;
        if (peaks) redraw();
        else clearCanvas();
    }

    onMount(() => {
        repaint();
        window.addEventListener(THEME_CHANGE_EVENT, repaint);
        return () => window.removeEventListener(THEME_CHANGE_EVENT, repaint);
    });

    /** Expose the raw canvas element so extension components can draw overlays. */
    export function getCanvas(): HTMLCanvasElement {
        return canvas;
    }
</script>

<!-- dir="ltr": the waveform is a left→right time axis; overlays that extension
     components position by %-from-start must not mirror under RTL. -->
<canvas bind:this={canvas} {width} {height} {style} dir="ltr"></canvas>

<style>
    canvas {
        display: block;
        background: var(--wf-bg, #0f0f23);
    }
</style>
