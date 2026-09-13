/**
 * AudioRange — unified [startMs, endMs] window playback primitive.
 *
 * Owns ONE rAF loop and ONE boundary check per frame against a caller-supplied
 * range on a caller-supplied audio surface. A `timeupdate` backstop runs the
 * same check whenever rAF is starved — a hidden browser tab / backgrounded
 * window freezes rAF entirely while the audio element keeps playing, so
 * without it the range boundary never fires and playback runs on past `endMs`
 * (the reported "switching tab keeps playing the card" bug). Pluggable boundary policy
 * (stop / loop / advance) handles the three behaviors the inspector needs:
 * single-segment play, trim/split loop preview, and autoplay advance.
 *
 * Two construction modes:
 *
 *   - **Port mode (preferred)**: pass `port: AudioPort`. Spec is purely
 *     file-absolute (`{startMs, endMs}`). The port owns transport
 *     (chapter URL vs server-clip URL), src-swap, and offset bookkeeping;
 *     AudioRange just enforces the boundary against `port.currentTimeMs()`.
 *   - **Legacy mode**: pass `audioEl: HTMLAudioElement`. Spec may be
 *     clip-relative when `clipFileOffsetMs` is set; AudioRange does its
 *     own canplay-based src-swap. For non-port callers; new surfaces
 *     should use port mode.
 *
 * Composes existing infra:
 *   - `lib/utils/animation.ts::createAnimationLoop` — rAF wrapper.
 *   - `lib/utils/audio.ts::safePlay` / `audioSrcMatches` (legacy mode).
 *   - `lib/playback/audio-graph.ts::cutAudio` / `uncutAudio` (legacy mode).
 *
 * No store imports — callers wire `onTick` / `onBoundary` to their own state.
 *
 * Caller contract: only ONE AudioRange may run on a given audio element /
 * port at a time. `enterEditMode` must `dispose()` the segments-main range
 * before starting an edit-preview range; `exitEditMode` does the reverse.
 */

import { type AnimationLoop,createAnimationLoop } from '../utils/animation';
import { audioSrcMatches, safePlay } from '../utils/audio';
import { cutAudio, uncutAudio } from './audio-graph';
import type { AudioPort } from './audio-port';

export interface AudioRangeSpec {
    /** Window start. **Port mode**: file-absolute ms. **Legacy**: clip-
     *  relative when `clipFileOffsetMs` is set, file-absolute otherwise. */
    startMs: number;
    /** Window end. Same coordinate space as `startMs`. */
    endMs: number;
    /** Legacy mode only — when set and `!audioSrcMatches(audioEl.src, src)`,
     *  `start()` swaps the audio element's src and waits for `canplay`
     *  before seeking + playing. Ignored in port mode (the port owns
     *  src-swap via `loadCovering`). */
    src?: string | null;
    /** Legacy mode only — when set, `audioEl.currentTime` is clip-relative;
     *  file-absolute time is `clipFileOffsetMs + audioEl.currentTime * 1000`.
     *  Used by the rAF tick so the playhead and segment-detection see
     *  file-absolute time while the audio element seeks against the byte-0
     *  clip. Undefined or 0 means the audio plays the chapter file directly.
     *  Ignored in port mode (the port owns offset bookkeeping). */
    clipFileOffsetMs?: number;
}

export type RangePolicy =
    | { kind: 'stop' }
    | { kind: 'loop' }
    | {
          kind: 'advance';
          gapMs: number;
          nextRange: () => AudioRangeSpec | null;
      };

export type BoundaryReason = 'stop' | 'loop' | 'advance';

export interface BoundaryEvent {
    reason: BoundaryReason;
    /** Set on `advance` boundaries; mirrors the spec the primitive will load
     *  after the gap completes. */
    nextRange?: AudioRangeSpec;
}

export interface AudioRangeOptions {
    /** Provide exactly ONE of `port` or `audioEl`. Port-mode is preferred;
     *  audioEl is the legacy path retained for transitional consumers. */
    port?: AudioPort;
    audioEl?: HTMLAudioElement;
    range: AudioRangeSpec;
    policy: RangePolicy;
    onTick?: (timeMs: number) => void;
    onBoundary?: (ev: BoundaryEvent) => void;
    /** Called on every play() / resume so callers can apply the live playback
     *  rate without re-reading a store inside the primitive. */
    playbackRate?: () => number;
}

export class AudioRange {
    private readonly port: AudioPort | null;
    private readonly audioEl: HTMLAudioElement;
    private range: AudioRangeSpec;
    private policy: RangePolicy;
    private readonly onTick: ((ms: number) => void) | undefined;
    private readonly onBoundary: ((ev: BoundaryEvent) => void) | undefined;
    private readonly playbackRate: (() => number) | undefined;

    private loop: AnimationLoop;
    /** True between a loop seek-back and the first frame where `currentTime`
     *  drops below `endMs`. Prevents the boundary from firing twice on the
     *  same crossing if the audio decoder is laggy. Mirrors `_previewJustSeeked`
     *  in the legacy `play-range.ts:123`. */
    private seekedThisFrame = false;
    private gapTimeout: ReturnType<typeof setTimeout> | null = null;
    /** Legacy-mode canplay handler. Port-mode delegates to
     *  `port.loadCovering(...).ready`. */
    private canplayHandler: (() => void) | null = null;
    /** Teardown for the `timeupdate` boundary backstop (see `_frameFromTimeUpdate`). */
    private timeUpdateUnsub: (() => void) | null = null;
    private disposed = false;

    constructor(opts: AudioRangeOptions) {
        if (opts.port && opts.audioEl) {
            throw new Error('AudioRange: pass exactly one of port / audioEl');
        }
        this.port = opts.port ?? null;
        const el = opts.port?.element ?? opts.audioEl ?? null;
        if (!el) throw new Error('AudioRange: port has no element / audioEl missing');
        this.audioEl = el;
        this.range = opts.range;
        this.policy = opts.policy;
        this.onTick = opts.onTick;
        this.onBoundary = opts.onBoundary;
        this.playbackRate = opts.playbackRate;
        this.loop = createAnimationLoop(() => this._frame());
        this._attachTimeUpdateBackstop();
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    start(): void {
        if (this.disposed) return;
        if (this.port) {
            this._startWithPort(this.range);
            return;
        }
        const { src } = this.range;
        if (src && !audioSrcMatches(this.audioEl.src, src)) {
            this._loadAndStart(this.range);
        } else {
            this._seekAndPlay(this.range.startMs);
            this.loop.start();
        }
    }

    /**
     * Start the rAF boundary-watcher loop WITHOUT seeking or calling play().
     * Use when the caller manages seek + play separately (e.g. an existing
     * AudioPlayer component already loaded the source and seeked to the verse
     * start) and only needs AudioRange's frame-precise boundary enforcement.
     *
     * Idempotent — calling while already attached is a no-op.
     */
    attach(): void {
        if (this.disposed) return;
        if (!this.loop.running()) this.loop.start();
    }

    stop(): void {
        this._cancelGap();
        this._detachCanplay();
        this.loop.stop();
        // Port mode: pause via the port so we hit the LIVE element (the cached
        // `this.audioEl` is the construction-time snapshot and goes stale across
        // a `port.adoptElement(...)` rotation — pausing it would no-op the stale
        // node and leave the active element playing).
        if (this.port) this.port.pause();
        else if (!this.audioEl.paused) this.audioEl.pause();
    }

    setRange(spec: AudioRangeSpec): void {
        this.range = spec;
        // A fresh range invalidates any pending advance gap and the
        // seeked-this-frame guard from a prior loop.
        this._cancelGap();
        this.seekedThisFrame = false;
        // Mirror dispose(): a fresh range is the signal another play path is
        // about to use this element. Lift any in-flight gain ramp from the
        // previous range's _pauseAndFlush so the next play() isn't silent.
        // Repros as silent audio after auto-next/auto-random in the
        // Timestamps tab, where AudioPlayer.load() drives the next play()
        // (so AudioRange's internal _seekAndPlay → uncutAudio never runs).
        this._uncut();
    }

    setPolicy(p: RangePolicy): void {
        this.policy = p;
        this._cancelGap();
    }

    isRunning(): boolean {
        return !this.disposed && (this.loop.running() || this.gapTimeout !== null);
    }

    dispose(): void {
        this.disposed = true;
        this.timeUpdateUnsub?.();
        this.timeUpdateUnsub = null;
        this.stop();
        // Lift any in-flight gain ramp before another path (edit-preview,
        // direct seek) plays this element. Without this, a dispose that
        // lands mid-cut would leave the next play() silent.
        this._uncut();
    }

    // -----------------------------------------------------------------------
    // Internal: frame tick
    // -----------------------------------------------------------------------

    /**
     * Boundary backstop on the element's `timeupdate` event.
     *
     * `_frame` is otherwise only reachable from the rAF loop, and browsers
     * stop servicing rAF outright for a hidden tab / backgrounded window while
     * the media element keeps decoding. That starves the ONLY boundary check,
     * so a bounded play (accordion card, or main-list play with autoplay off)
     * sails past `endMs` and keeps playing the rest of the chapter until the
     * user comes back — ignoring both the `stop` and the `advance` policy.
     *
     * `timeupdate` is a media event, so it keeps firing while hidden (~4 Hz,
     * vs. rAF's 0 Hz), bounding the overshoot to roughly one event interval.
     * Port mode subscribes through the port so the handler follows an
     * `adoptElement` rotation; legacy mode binds the element directly.
     */
    private _attachTimeUpdateBackstop(): void {
        const onTimeUpdate = (): void => this._frameFromTimeUpdate();
        if (this.port) {
            this.timeUpdateUnsub = this.port.onTimeUpdate(onTimeUpdate);
            return;
        }
        const el = this.audioEl;
        el.addEventListener('timeupdate', onTimeUpdate);
        this.timeUpdateUnsub = () => el.removeEventListener('timeupdate', onTimeUpdate);
    }

    /** Run one boundary check outside rAF. No-op unless the rAF loop is
     *  supposed to be enforcing right now, so a stopped / disposed range never
     *  reacts to another play path's `timeupdate`s. */
    private _frameFromTimeUpdate(): void {
        if (this.disposed || !this.loop.running()) return;
        // A `stop`-policy boundary self-stops the loop by returning false from
        // the rAF tick. Reached from here that return value has nowhere to go,
        // so cancel the pending frame ourselves — otherwise the chain keeps
        // spinning once the tab is visible again.
        if (this._frame() === false) this.loop.stop();
    }

    private _frame(): boolean | void {
        if (this.disposed) return false;
        // Pause-resilient: keep the rAF chain alive while the audio is paused
        // (post-seek transient, advance gap cooldown, external user pause).
        // We just don't tick onTick or check the boundary — those resume on unpause.
        //
        // Port mode reads `port.paused` (delegates to the LIVE `port.element`)
        // instead of the cached `this.audioEl`. Otherwise an element rotation
        // via `port.adoptElement(...)` (TS-tab gapless shadow transplant)
        // leaves AudioRange polling the detached old element — which is paused
        // by definition post-swap, so every frame returns here and no tick / no
        // boundary fires. Manifests as: audio plays through the new verse but
        // highlights are frozen and auto-advance never triggers at verse end.
        const paused = this.port ? this.port.paused : this.audioEl.paused;
        if (paused) return;

        // Coordinate space:
        //   - Port mode: file-absolute everywhere. `port.currentTimeMs()`
        //     applies the offset; `range.startMs/endMs` are file-absolute.
        //   - Legacy mode: `audioEl.currentTime` is in the same space as
        //     `range.startMs/endMs` (clip-relative when `clipFileOffsetMs`
        //     is set, file-absolute otherwise). Boundary firing uses this
        //     value directly; `onTick` callers want file-absolute time.
        let cmpMs: number;  // value compared against range.endMs
        let fileMs: number; // value passed to onTick
        if (this.port) {
            fileMs = this.port.currentTimeMs();
            cmpMs = fileMs;
        } else {
            const clipMs = this.audioEl.currentTime * 1000;
            cmpMs = clipMs;
            fileMs = this.range.clipFileOffsetMs != null
                ? this.range.clipFileOffsetMs + clipMs
                : clipMs;
        }

        // Seeked-this-frame guard: clear once cmp has dropped below
        // endMs at least once after a loop seek-back.
        if (this.seekedThisFrame && cmpMs < this.range.endMs) {
            this.seekedThisFrame = false;
        }

        if (cmpMs >= this.range.endMs && !this.seekedThisFrame) {
            return this._handleBoundary();
        }

        this.onTick?.(fileMs);
        return;
    }

    /** Return file-absolute current time in ms, accounting for clip offset.
     *  Callers outside the rAF tick (e.g. timeupdate handler) read this so
     *  they see the same coordinate space the playhead draws in. */
    getCurrentTimeMs(): number {
        if (this.port) return this.port.currentTimeMs();
        const clipMs = this.audioEl.currentTime * 1000;
        return this.range.clipFileOffsetMs != null
            ? this.range.clipFileOffsetMs + clipMs
            : clipMs;
    }

    private _handleBoundary(): boolean | void {
        switch (this.policy.kind) {
            case 'stop': {
                this._pauseAndFlush();
                this.onBoundary?.({ reason: 'stop' });
                return false;
            }
            case 'loop': {
                this._seekClip(this.range.startMs);
                this.seekedThisFrame = true;
                this.onBoundary?.({ reason: 'loop' });
                return;
            }
            case 'advance': {
                const next = this.policy.nextRange();
                if (!next) {
                    this._pauseAndFlush();
                    this.onBoundary?.({ reason: 'stop' });
                    return false;
                }
                const gapMs = this.policy.gapMs;
                this._pauseAndFlush();
                this.onBoundary?.({ reason: 'advance', nextRange: next });
                this._scheduleAdvance(next, gapMs);
                return;
            }
        }
    }

    /** Pause the element and pin `currentTime` to `range.endMs`.
     *
     *  `pause()` halts the source-side renderer but does NOT flush the OS
     *  audio sink — Windows WASAPI holds ~50–200 ms of pre-decoded samples
     *  that keep playing out, leaving an audible tail past `endMs`. We
     *  silence those queued samples via a sample-accurate Web Audio gain
     *  ramp (`cutAudio`) before pausing; the next `_seekAndPlay` undoes
     *  the cut with `uncutAudio`. Requires `crossorigin="anonymous"` on
     *  the audio element (set in `lib/components/AudioElement.svelte`)
     *  and matching CORS headers from the audio route. */
    private _pauseAndFlush(): void {
        if (this.port) {
            this.port.pauseAndFlush();
            this.port.seek(this.range.endMs);
            return;
        }
        cutAudio(this.audioEl);
        this.audioEl.pause();
        this.audioEl.currentTime = this.range.endMs / 1000;
    }

    // -----------------------------------------------------------------------
    // Internal: advance gap
    // -----------------------------------------------------------------------

    private _scheduleAdvance(next: AudioRangeSpec, gapMs: number): void {
        this._cancelGap();
        this.gapTimeout = setTimeout(() => {
            this.gapTimeout = null;
            if (this.disposed) return;
            this.range = next;
            this.seekedThisFrame = false;
            if (this.port) {
                this._startWithPort(next);
                return;
            }
            if (next.src && !audioSrcMatches(this.audioEl.src, next.src)) {
                this._loadAndStart(next);
            } else {
                this._seekAndPlay(next.startMs);
                if (!this.loop.running()) this.loop.start();
            }
        }, gapMs);
    }

    private _cancelGap(): void {
        if (this.gapTimeout !== null) {
            clearTimeout(this.gapTimeout);
            this.gapTimeout = null;
        }
    }

    // -----------------------------------------------------------------------
    // Port-mode helpers
    // -----------------------------------------------------------------------

    private _startWithPort(spec: AudioRangeSpec): void {
        if (!this.port) return;
        const { ready, swapped } = this.port.loadCovering(spec.startMs, spec.endMs);
        if (!swapped) {
            this._seekAndPlayWithPort(spec.startMs);
            if (!this.loop.running()) this.loop.start();
            return;
        }
        // Async swap: defer seek + play until canplay resolves. The port
        // detaches its own canplay listener; AbortError bubbles when a
        // newer load supersedes this one.
        ready.then(() => {
            if (this.disposed) return;
            this._seekAndPlayWithPort(spec.startMs);
            if (!this.loop.running()) this.loop.start();
        }).catch((e: unknown) => {
            if (e && (e as { name?: string }).name !== 'AbortError') console.error(e);
        });
    }

    private _seekAndPlayWithPort(fileMs: number): void {
        if (!this.port) return;
        if (this.playbackRate) this.port.setPlaybackRate(this.playbackRate());
        this.port.seekAndPlay(fileMs);
    }

    private _seekClip(startMs: number): void {
        if (this.port) {
            // startMs is file-absolute under port mode.
            this.port.seek(startMs);
            return;
        }
        // Legacy: clip-relative or file-absolute depending on clipFileOffsetMs.
        this.audioEl.currentTime = startMs / 1000;
    }

    private _uncut(): void {
        if (this.port) { this.port.uncut(); return; }
        uncutAudio(this.audioEl);
    }

    // -----------------------------------------------------------------------
    // Internal: src-swap + seek + play (legacy mode)
    // -----------------------------------------------------------------------

    private _loadAndStart(spec: AudioRangeSpec): void {
        this._detachCanplay();
        const handler = (): void => {
            this.canplayHandler = null;
            if (this.disposed) return;
            this._seekAndPlay(spec.startMs);
            if (!this.loop.running()) this.loop.start();
        };
        this.canplayHandler = handler;
        this.audioEl.addEventListener('canplay', handler, { once: true });
        if (spec.src != null) this.audioEl.src = spec.src;
        this.audioEl.load();
    }

    private _detachCanplay(): void {
        if (this.canplayHandler) {
            this.audioEl.removeEventListener('canplay', this.canplayHandler);
            this.canplayHandler = null;
        }
    }

    private _seekAndPlay(startMs: number): void {
        this.audioEl.currentTime = startMs / 1000;
        if (this.playbackRate) this.audioEl.playbackRate = this.playbackRate();
        uncutAudio(this.audioEl);
        safePlay(this.audioEl);
    }
}
