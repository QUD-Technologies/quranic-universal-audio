/**
 * _playRange — preview playback with animated playhead overlay.
 *
 * Edit-mode (trim / split) preview, including loop-back. Owns its own
 * rAF loop because the playhead also drives a per-frame canvas redraw
 * (it can't ride AudioRange's onTick directly without extra wiring).
 *
 * Coordinate space: file-absolute milliseconds throughout. The `segPort`
 * port translates to clip-relative `<audio>.currentTime` internally for
 * VBR clip loads. Callers never touch `clipFileOffsetMs`.
 *
 * Edit-mode pre-load: callers (enterTrimMode / enterSplitMode) issue a
 * `segPort.loadCovering(seg.time_start, seg.time_end, EDIT_LOAD_PAD_MS)`
 * ONCE on entry. Subsequent `_playRange` calls (split-left / split-right
 * toggles, trim-handle nudges) hit the port's idempotent fast path —
 * no fresh ffmpeg invocation per press. Drags that push beyond the
 * loaded window can re-issue `loadCovering` to widen.
 */

import { get, writable } from 'svelte/store';

import { themeColor } from '../../../../lib/utils/canvas-theme';
import { PREVIEW_PLAYHEAD_COLOR } from '../../../../lib/utils/constants';
import { editCanvas } from '../../stores/edit';
import { playbackSpeed, segPort } from '../../stores/playback';
import type { PreviewLoopMode, RafHandle } from '../../types/segments';
import { drawSplitWaveform } from '../waveform/split-draw';
import { drawTrimWaveform } from '../waveform/trim-draw';

// ---------------------------------------------------------------------------
// Module-local state
// ---------------------------------------------------------------------------

export const previewLooping = writable<PreviewLoopMode>(false);

/** True while the user wants an edit-mode preview to be playing. Set by
 *  the centralized play/pause handler in playback.ts on cold-start and
 *  resume; cleared on user-initiated pause and on exitEditMode. Distinct
 *  from `previewLooping` (which tracks WHICH range is the loop target —
 *  stays set across pause/resume so the rAF can resume seamlessly).
 *  Drives the footer play-button glyph in edit mode. */
export const editPreviewPlaying = writable<boolean>(false);

let _previewJustSeeked = false;
let _playRangeRAF: RafHandle | null = null;
let _previewStopHandler: ((ev: Event) => void) | null = null;
/** Teardown for the `timeupdate` boundary backstop — see `_setupPreviewLoop`. */
let _previewBoundaryUnsub: (() => void) | null = null;

export function getPreviewLooping(): PreviewLoopMode { return get(previewLooping); }
export function setPreviewLooping(v: PreviewLoopMode): void { previewLooping.set(v); }

export function setPreviewJustSeeked(v: boolean): void { _previewJustSeeked = v; }

export function getPlayRangeRAF(): RafHandle | null { return _playRangeRAF; }
export function clearPlayRangeRAF(): void {
    if (_playRangeRAF) { cancelAnimationFrame(_playRangeRAF); _playRangeRAF = null; }
    _previewBoundaryUnsub?.();
    _previewBoundaryUnsub = null;
}

/** No-op kept for back-compat with `exitEditMode`. The port owns canplay
 *  listener lifecycle; when the user cancels an edit before the load
 *  resolves, the next `setSource` / `loadCovering` call (or the segments
 *  main-list `playFromSegment`) will abort the pending load with
 *  AbortError and we silently swallow it. */
export function clearPreviewCanplayHandler(): void {
    /* no-op — port owns canplay */
}

export function getPreviewStopHandler(): ((ev: Event) => void) | null { return _previewStopHandler; }
export function setPreviewStopHandler(h: ((ev: Event) => void) | null): void { _previewStopHandler = h; }

// ---------------------------------------------------------------------------
// _playRange / attachPreviewLoop — preview-loop rAF (cold seek vs warm attach)
// ---------------------------------------------------------------------------

/** Cold-start a preview loop — `loadCovering` + `seekAndPlay(startMs)` then
 *  attach the playhead rAF. Use when the audio is paused, or playing a
 *  different region than the one we want to loop. */
export function _playRange(startMs: number, endMs: number): void {
    _setupPreviewLoop(startMs, endMs, /* coldSeek = */ true);
}

/** Warm-attach a preview loop — schedule the playhead rAF over [startMs,
 *  endMs] but DON'T seek or reload. Use when audio is already playing
 *  within the target range; the rAF's existing loop-back logic catches
 *  the wraparound at `effectiveEnd` and keeps the loop alive without
 *  the cold-start glitch (no extra `seekAndPlay`, no clip rebuild). */
export function attachPreviewLoop(startMs: number, endMs: number): void {
    _setupPreviewLoop(startMs, endMs, /* coldSeek = */ false);
}

function _setupPreviewLoop(startMs: number, endMs: number, coldSeek: boolean): void {
    if (!segPort.element) return;
    if (_previewStopHandler) {
        // Legacy timeupdate-based stop handler — port subscribes via
        // onTimeUpdate elsewhere; we keep removeEventListener for callers
        // still attaching it directly.
        segPort.element.removeEventListener('timeupdate', _previewStopHandler);
        _previewStopHandler = null;
    }
    if (_playRangeRAF) { cancelAnimationFrame(_playRangeRAF); _playRangeRAF = null; }
    _previewBoundaryUnsub?.();
    _previewBoundaryUnsub = null;
    const canvas = get(editCanvas);

    let wfStart: number, wfEnd: number;
    // Trim + Split modes: playhead x is mapped against the VISIBLE window
    // (not the full clamp / segment range) — so a zoomed-in view shows the
    // playhead aligned with the rendered peaks. Playback itself is unaffected
    // (still plays the full preview range); when curMs falls outside the
    // visible window the `if (curMs >= wfStart && curMs <= wfEnd)` gate
    // below simply skips drawing for that frame. The animatePlayhead loop
    // re-reads view bounds each frame so wheel-zoom mid-playback updates
    // the mapping live.
    if (canvas?._trimWindow) { wfStart = canvas._trimWindow.viewStart; wfEnd = canvas._trimWindow.viewEnd; }
    else if (canvas?._splitData) { wfStart = canvas._splitData.viewStart; wfEnd = canvas._splitData.viewEnd; }
    else { wfStart = startMs; wfEnd = endMs; }

    const cleanup = (): void => {
        if (_playRangeRAF) { cancelAnimationFrame(_playRangeRAF); _playRangeRAF = null; }
        _previewBoundaryUnsub?.();
        _previewBoundaryUnsub = null;
        if (canvas?._splitData) drawSplitWaveform(canvas);
        else if (canvas?._trimWindow) drawTrimWaveform(canvas);
    };

    const inEditMode = canvas && (canvas._splitData || canvas._trimWindow);
    let _playRangeSnapshot: ImageData | null = null;
    if (canvas && !inEditMode) {
        const ctx = canvas.getContext('2d');
        if (ctx) _playRangeSnapshot = ctx.getImageData(0, 0, canvas.width, canvas.height);
    }

    /**
     * The boundary half of the loop, callable outside rAF.
     *
     * `'idle'`     — paused, or nothing to do this tick; keep the chain alive.
     * `'seeked'`   — looped back to `loopStart`; skip drawing this tick.
     * `'stopped'`  — hit `effectiveEnd` under a non-loop preview; loop is over.
     * `'continue'` — mid-range at `curMs`; the caller may draw the playhead.
     */
    function checkBoundary(): { state: 'idle' | 'seeked' | 'stopped' } | { state: 'continue'; curMs: number } {
        if (segPort.paused) return { state: 'idle' };
        // File-absolute throughout — the port owns offset translation.
        const curMs = segPort.currentTimeMs();
        const loopMode = get(previewLooping);
        let effectiveEnd = endMs;
        let loopStart: number | null = null;
        if (loopMode === 'trim' && canvas?._trimWindow) {
            effectiveEnd = canvas._trimWindow.currentEnd;
            loopStart = canvas._trimWindow.currentStart;
        } else if (loopMode === 'split-left' && canvas?._splitData
                   && canvas._splitData.currentSplits.length === 1) {
            effectiveEnd = canvas._splitData.currentSplits[0]!;
            loopStart = canvas._splitData.seg.time_start;
        } else if (loopMode === 'split-right' && canvas?._splitData
                   && canvas._splitData.currentSplits.length === 1) {
            effectiveEnd = canvas._splitData.seg.time_end;
            loopStart = canvas._splitData.currentSplits[0]!;
        } else if (typeof loopMode === 'string'
                   && loopMode.startsWith('split-region-')
                   && canvas?._splitData) {
            // Region i: loop between cursor[i-1] (or seg start) and cursor[i]
            // (or seg end). Reading currentSplits live each frame lets the
            // user drag a cursor mid-loop and have the loop bounds follow.
            const sd = canvas._splitData;
            const i = parseInt(loopMode.slice('split-region-'.length), 10);
            const n = sd.currentSplits.length;
            if (!Number.isNaN(i) && i >= 0 && i <= n) {
                loopStart = i === 0 ? sd.seg.time_start : sd.currentSplits[i - 1]!;
                effectiveEnd = i === n ? sd.seg.time_end : sd.currentSplits[i]!;
            }
        } else if (canvas?._splitData && endMs !== canvas._splitData.seg.time_end
                   && canvas._splitData.currentSplits.length === 1) {
            effectiveEnd = canvas._splitData.currentSplits[0]!;
        }
        if (_previewJustSeeked && curMs < effectiveEnd) {
            _previewJustSeeked = false;
        }
        if (curMs >= effectiveEnd && !_previewJustSeeked) {
            if (loopMode && loopStart !== null) {
                // OS-sink flush across the loop seek-back. Without
                // pauseAndFlush the platform audio sink (Windows WASAPI
                // ~50–200ms of pre-decoded samples) keeps playing past
                // `effectiveEnd` while the seek queues — pre-refactor's
                // tight per-press clip ended at endMs so those queued
                // samples were silence/EOF, but the post-refactor wider
                // EDIT_LOAD_PAD_MS clip has REAL audio in the post-pad
                // zone. The cut+pause silences and drains the sink; the
                // seekAndPlay then uncuts and resumes from loopStart.
                // Port handles file-absolute → clip-relative translation.
                segPort.pauseAndFlush();
                segPort.seekAndPlay(loopStart);
                _previewJustSeeked = true;
                return { state: 'seeked' };
            }
            // Stop branch (non-loop preview reaching its endMs): same
            // sink-flush rationale — replace bare `pause()` with
            // `pauseAndFlush()` so the wider clip's post-pad samples
            // queued in the OS sink don't audibly leak after the boundary.
            segPort.pauseAndFlush();
            cleanup();
            return { state: 'stopped' };
        }
        return { state: 'continue', curMs };
    }

    /**
     * Boundary backstop on the element's `timeupdate`.
     *
     * The rAF chain below is the only thing enforcing `effectiveEnd`, and
     * browsers stop servicing rAF entirely for a hidden tab / backgrounded
     * window while the media element keeps decoding — so without this the
     * preview loop never wraps and never stops, it just plays on through the
     * chapter. `timeupdate` is a media event and keeps firing (~4 Hz) while
     * hidden, bounding the overshoot to about one event interval. Mirrors the
     * same backstop in `lib/playback/audio-range.ts`.
     */
    function boundaryBackstop(): void {
        if (_playRangeRAF === null) return;   // loop not running — not ours to enforce
        checkBoundary();                       // `cleanup()` cancels the rAF on 'stopped'
    }

    function animatePlayhead(): void {
        if (!canvas) return;
        // Keep the rAF chain alive across transient paused states (post-seek
        // before play() resolves, between loop-seek-back and resume, etc).
        // Bailing on paused here used to kill the chain on the first frame
        // after `doPlay` → the playhead never drew, loop-back never ran,
        // drag-updates-loop-live stopped working. Explicit cleanup uses
        // `clearPlayRangeRAF` to cancel the rAF.
        const res = checkBoundary();
        if (res.state === 'stopped') return;
        if (res.state !== 'continue') {
            _playRangeRAF = requestAnimationFrame(animatePlayhead);
            return;
        }
        const curMs = res.curMs;
        if (canvas._splitData) drawSplitWaveform(canvas);
        else if (canvas._trimWindow) drawTrimWaveform(canvas);
        else if (_playRangeSnapshot) {
            const ctx2 = canvas.getContext('2d');
            if (ctx2) ctx2.putImageData(_playRangeSnapshot, 0, 0);
        }
        // Re-read view bounds each frame so wheel-zoom mid-playback updates
        // the playhead mapping immediately (closure-captured values would
        // go stale otherwise). Both trim AND split support live re-scaling;
        // non-edit mode keeps its closure-captured bounds.
        const liveStart = canvas._trimWindow ? canvas._trimWindow.viewStart
            : canvas._splitData ? canvas._splitData.viewStart
            : wfStart;
        const liveEnd   = canvas._trimWindow ? canvas._trimWindow.viewEnd
            : canvas._splitData ? canvas._splitData.viewEnd
            : wfEnd;
        if (curMs >= liveStart && curMs <= liveEnd) {
            const ctx = canvas.getContext('2d');
            if (!ctx) { _playRangeRAF = requestAnimationFrame(animatePlayhead); return; }
            const w = canvas.width, h = canvas.height;
            const x = ((curMs - liveStart) / (liveEnd - liveStart)) * w;
            const playheadColor = themeColor('--wf-preview-playhead', PREVIEW_PLAYHEAD_COLOR);
            ctx.strokeStyle = playheadColor; ctx.lineWidth = 2;
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
            ctx.fillStyle = playheadColor;
            ctx.beginPath(); ctx.moveTo(x - 4, 0); ctx.lineTo(x + 4, 0); ctx.lineTo(x, 6); ctx.closePath(); ctx.fill();
        }
        _playRangeRAF = requestAnimationFrame(animatePlayhead);
    }

    // Subscribe the backstop before either start path schedules the rAF; it
    // no-ops until `_playRangeRAF` is set, and both `cleanup()` and
    // `clearPlayRangeRAF()` tear it down.
    _previewBoundaryUnsub = segPort.onTimeUpdate(boundaryBackstop);

    if (!coldSeek) {
        // Warm attach — audio is already playing within (or near) the loop
        // window. Skip loadCovering / seekAndPlay and just schedule the
        // playhead rAF; animatePlayhead's loop-back at effectiveEnd handles
        // wraparound from here without a fresh seek glitch.
        _playRangeRAF = requestAnimationFrame(animatePlayhead);
        return;
    }

    // Ensure the port is covering the requested file-absolute window. In
    // edit mode the caller has already pre-loaded the segment with post-roll.
    // CBR chapters always cover (window.endMs is Infinity). VBR only reuses
    // that clip when the requested playback start matches the loaded clip
    // start; a new start gets a fresh clip so play begins from clip time 0
    // rather than seeking inside a streamed response.
    const { ready, swapped } = segPort.loadCovering(startMs, endMs);

    const doPlay = (): void => {
        segPort.setPlaybackRate(get(playbackSpeed));
        segPort.seekAndPlay(startMs);
        // Start the rAF immediately. `animatePlayhead` is now resilient to
        // paused state (see the early-continue above), so whether `play`
        // has resolved yet doesn't matter — the chain keeps ticking and
        // starts drawing the playhead as soon as `port.paused` flips false.
        _playRangeRAF = requestAnimationFrame(animatePlayhead);
    };

    if (!swapped) {
        // Same src — play immediately.
        doPlay();
    } else {
        // New clip pending — wait for canplay then play. AbortError swallowed
        // (overlapping load supersedes us, e.g. user cancelled the edit).
        ready.then(() => doPlay()).catch((e: unknown) => {
            if (e && (e as { name?: string }).name !== 'AbortError') console.error(e);
        });
    }
}

