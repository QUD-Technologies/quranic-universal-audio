<script lang="ts">
    /**
     * SegmentWaveformCanvas — per-segment-row waveform primitive.
     *
     * Wraps <WaveformCanvas> with segment-specific concerns: resolves peaks
     * from the chapter-wide payload via startMs/endMs sub-ranging, and exposes
     * getCanvas() so imperative overlay code (playhead, trim/split/merge
     * highlight descriptors) can draw at 60fps without triggering Svelte
     * reactive re-renders. Svelte owns the structural waveform render (peaks
     * change); imperative code owns the overlay draw.
     */

    import { onMount } from 'svelte';

    import WaveformCanvas from '../../../../lib/components/WaveformCanvas.svelte';
    import { THEME_CHANGE_EVENT } from '../../../../lib/stores/theme.svelte';
    import type { AudioPeaks } from '../../../../lib/types/peaks-transport';
import type { Segment } from '../../../../lib/types/view-models';
    import type { SegCanvas } from '../../types/segments-waveform';

    // ---------------------------------------------------------------------------
    // Props
    // ---------------------------------------------------------------------------

    /** The segment this waveform row represents. */
    export let seg: Segment;

    /**
     * Chapter-wide peaks payload. When provided together with startMs/endMs/
     * totalDurationMs, WaveformCanvas renders just the segment's time slice.
     * Pass null to show an empty canvas.
     */
    export let chapterPeaks: AudioPeaks | null = null;

    /** Sub-range start in milliseconds — typically seg.time_start. */
    export let startMs: number | undefined = undefined;

    /** Sub-range end in milliseconds — typically seg.time_end. */
    export let endMs: number | undefined = undefined;

    /**
     * Total duration the chapter peaks array covers, in milliseconds.
     * Required together with startMs/endMs for sub-ranging.
     */
    export let totalDurationMs: number | undefined = undefined;

    /** Canvas display width in pixels. */
    export let width = 300;

    /** Canvas display height in pixels. */
    export let height = 60;

    // ---------------------------------------------------------------------------
    // Internal refs
    // ---------------------------------------------------------------------------

    let waveformCanvas: WaveformCanvas;

    // ---------------------------------------------------------------------------
    // Public API
    // ---------------------------------------------------------------------------

    /**
     * Return the underlying HTMLCanvasElement as SegCanvas so imperative overlay
     * code (playhead draw, trim/split/merge highlight descriptors) can attach
     * ad-hoc fields and draw without triggering Svelte reactivity.
     */
    export function getCanvas(): SegCanvas {
        return waveformCanvas.getCanvas() as SegCanvas;
    }

    onMount(() => {
        // The child WaveformCanvas repaints its structural peaks on theme flip.
        // The imperative overlay/playhead layer (draw-seg.ts) caches the base
        // render as ImageData on the canvas; drop those caches so the next
        // playhead tick / overlay refresh rebuilds them with fresh theme
        // colours instead of blitting the stale-coloured snapshot.
        const onThemeChange = (): void => {
            const c = waveformCanvas?.getCanvas() as SegCanvas | undefined;
            if (!c) return;
            c._wfCache = null;
            c._trimBaseCache = null;
            c._splitBaseCache = null;
        };
        window.addEventListener(THEME_CHANGE_EVENT, onThemeChange);
        return () => window.removeEventListener(THEME_CHANGE_EVENT, onThemeChange);
    });
</script>

<!--
    data-seg-index / data-seg-chapter are read by the IntersectionObserver in
    lib/utils/segments/waveform-utils.ts to look up the segment from the store.
-->
<div
    data-seg-index={seg.index}
    data-seg-chapter={seg.chapter}
    class="seg-waveform-canvas-root"
>
    <WaveformCanvas
        bind:this={waveformCanvas}
        peaks={chapterPeaks?.peaks ?? null}
        {width}
        {height}
        {startMs}
        {endMs}
        {totalDurationMs}
        willReadFrequently
    />
</div>

<style>
    .seg-waveform-canvas-root {
        display: contents;
    }
</style>
