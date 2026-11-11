/**
 * AudioPort — single chokepoint for audio-element transport.
 *
 * Owns the `<audio>` element, the src-swap decision (chapter MP3 vs.
 * per-segment server clip), and the offset bookkeeping that lets every
 * caller speak file-absolute milliseconds against a logical `AudioSource`,
 * regardless of whether the underlying transport is CBR (chapter URL,
 * file-absolute `currentTime`) or VBR (server-clip URL, clip-relative
 * `currentTime` + `offsetMs` to recover file-absolute).
 *
 * Why the abstraction: VBR-MP3 chapters can't be byte-seeked in their
 * full chapter URL (no Xing header → mis-seeks), so we route per-segment
 * clips through `/api/seg/segment-clip/<reciter>?...`. The clip plays
 * from byte 0; `offsetMs` recovers file-absolute time. Without this port,
 * the clip-vs-full distinction leaked into every editing surface — seek
 * handlers, edit-mode preview, save-preview, history. After this port,
 * all callers speak file-absolute time and never see the transport.
 *
 * Composes existing infra:
 *   - `lib/utils/audio.ts::safePlay` / `audioSrcMatches`
 *   - `lib/playback/audio-graph.ts::cutAudio` / `uncutAudio`
 *
 * No tab-specific store imports — every consumer hands the port its own
 * `AudioSource` via `setSource(...)`, so the port is reusable across the
 * segments, timestamps, and audio tabs (and per-panel preview ports).
 */

import { audioSrcMatches, safePlay } from '../utils/audio';
import { cutAudio, uncutAudio } from './audio-graph';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Logical audio source. Distinct from `<audio>.src` (which is a transport
 *  detail); callers describe the source they want to play, the port picks
 *  the actual URL. */
export interface AudioSource {
    /** Canonical CDN URL — used for VBR clip URL building. The clip
     *  endpoint expects the unwrapped URL in its query string. */
    audioUrl: string;
    /** What to set as `<audio>.src` in CBR mode. Defaults to `audioUrl`
     *  when omitted. Callers that need an audio-proxy wrap (for CORS on
     *  by_surah reciters) compute the wrapped URL and pass it here; the
     *  port doesn't second-guess. VBR mode ignores this field — the clip
     *  endpoint is always built from `audioUrl`. */
    cbrSrc?: string;
    /** Reciter slug. Required for VBR clip routing. null disables it
     *  (port falls through to CBR using `cbrSrc ?? audioUrl`). */
    reciter: string | null;
    /** When true, `loadCovering` builds a server-clip URL for the requested
     *  window. Set from `segData.vbr` (or whatever per-source store carries
     *  the chapter's encoding flag). */
    vbr: boolean;
}

/** Snapshot of what the audio element currently has loaded. */
export interface LoadedWindow {
    /** File-absolute ms of the loaded resource's start. 0 for CBR. */
    startMs: number;
    /** File-absolute ms of the loaded resource's end. Infinity for CBR
     *  (the chapter file covers everything inside its real duration). */
    endMs: number;
    /** ms to add to `audioEl.currentTime * 1000` to get file-absolute time.
     *  0 for CBR; equals `startMs` for VBR clip loads. */
    offsetMs: number;
    /** The actual URL the audio element holds (post-proxy-wrap for CBR). */
    src: string;
    /** True when the loaded src is a server clip (VBR routing). */
    isClip: boolean;
}

/** Returned by `loadCovering`. `ready` resolves on canplay (or sync if
 *  no swap happened). `swapped` lets callers tell instantly whether they
 *  need to await. */
export interface LoadCoveringResult {
    ready: Promise<void>;
    swapped: boolean;
    window: LoadedWindow;
}

export interface AudioPortOptions {
    /** Default coverage padding (ms) for `loadCovering` when the caller
     *  doesn't pass one. CBR ignores it; VBR uses it only as post-roll.
     *  VBR clips must still begin at the requested playback start so the
     *  browser plays from clip time 0 rather than seeking inside a streamed
     *  clip. Default 0. */
    defaultPadMs?: number;
    /** Opt out of the Web Audio kill-switch (cutAudio / uncutAudio). When
     *  true, `play`/`seekAndPlay`/`pauseAndFlush`/`uncut` skip the
     *  `MediaElementAudioSourceNode` routing in `audio-graph.ts`. The
     *  audible-tail bug returns at pause (~50–300 ms OS buffer), but the
     *  `<audio>` element stays on its default sink — which means cross-
     *  origin media plays correctly. Without this opt-out, constructing
     *  the MediaElementSource on a cross-origin URL silently mutes
     *  playback per the Web Audio spec. Set this for ports that play
     *  full chapter MP3s and don't need sample-accurate end-of-region
     *  cutoff (dashboard). Default false. */
    disableKillSwitch?: boolean;
}

type Unsub = () => void;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** Construct the `/api/seg/segment-clip/<reciter>?…` URL.
 *
 *  Deterministic on (reciter, audioUrl, startMs, endMs) so the browser HTTP
 *  cache absorbs repeat plays of the same window. Mirrors the helper that
 *  used to live in `tabs/segments/utils/playback/range-spec.ts` — moved
 *  here so the port owns clip URL construction end-to-end. */
function buildClipUrl(reciter: string, audioUrl: string, startMs: number, endMs: number): string {
    const params = new URLSearchParams({
        url: audioUrl,
        start_ms: String(startMs),
        end_ms: String(endMs),
    });
    return `/api/seg/segment-clip/${encodeURIComponent(reciter)}?${params.toString()}`;
}

function isAbortError(e: unknown): boolean {
    return !!e && typeof e === 'object' && (e as { name?: string }).name === 'AbortError';
}

// ---------------------------------------------------------------------------
// AudioPort
// ---------------------------------------------------------------------------

export class AudioPort {
    private el: HTMLAudioElement | null = null;
    private _source: AudioSource | null = null;
    private _window: LoadedWindow | null = null;
    private readonly defaultPadMs: number;
    private readonly killSwitchEnabled: boolean;
    private desiredPlaybackRate = 1;
    private _timelineVersion = 0;

    /** Changes on seeks and transport replacement, never on playback ticks.
     * Visual cursors use this to distinguish a discontinuity from clock jitter. */
    get timelineVersion(): number {
        return this._timelineVersion;
    }

    /** Bumped on every src swap. Pending canplay handlers compare this
     *  against the gen they captured at attach-time; mismatched gens are
     *  silently dropped. Lets a newer `loadCovering` cancel an older one. */
    private loadGen = 0;
    private pendingCanplay: (() => void) | null = null;
    private pendingReady: { resolve: () => void; reject: (e: unknown) => void } | null = null;
    /** The `ready` promise of an in-flight swap. Held so a follow-up
     *  `loadCovering` whose request is covered by the in-flight target window
     *  can return that same promise (with `swapped: true`) and have the
     *  caller wait for canplay — instead of the caller seeing `swapped:false`
     *  against the OLD `_window` and seeking/playing into a still-loading
     *  element with mismatched offsetMs. Cleared when canplay fires or the
     *  load is aborted. */
    private pendingPromise: Promise<void> | null = null;

    /** DOM listeners attached on `attachElement` and detached on
     *  `attachElement(null)`. Kept so we can re-emit through subscribers
     *  with file-absolute time on timeupdate. */
    private domListeners: Array<[string, EventListener]> = [];

    private loadSubs = new Set<(w: LoadedWindow) => void>();
    private playSubs = new Set<() => void>();
    private pauseSubs = new Set<() => void>();
    private endedSubs = new Set<() => void>();
    private timeUpdateSubs = new Set<(fileMs: number) => void>();
    private errorSubs = new Set<(err: MediaError | null) => void>();
    private waitingSubs = new Set<() => void>();
    private playingSubs = new Set<() => void>();

    constructor(opts: AudioPortOptions = {}) {
        this.defaultPadMs = opts.defaultPadMs ?? 0;
        this.killSwitchEnabled = !opts.disableKillSwitch;
    }

    // -----------------------------------------------------------------------
    // Lifecycle
    // -----------------------------------------------------------------------

    /** Bind to an `<audio>` element. Pass `null` to detach.
     *
     *  Reattaching to a different element auto-detaches the prior one and
     *  cancels any pending canplay (the prior load's `ready` rejects with
     *  AbortError). Idempotent for the same element. */
    attachElement(el: HTMLAudioElement | null): void {
        if (el === this.el) return;
        this._timelineVersion++;
        if (this.el) {
            this._detachDomListeners();
            this._abortPendingLoad();
            this._window = null;
        }
        this.el = el;
        if (el) {
            // Kill-switch ports route the element through Web Audio's
            // `MediaElementAudioSourceNode`, which outputs SILENCE for a
            // cross-origin src unless the element is CORS-enabled. Chapter
            // MP3s now load straight from CORS-capable CDNs (play-url.ts),
            // so the attribute must be on before any src is assigned.
            // Non-kill-switch ports (dashboard) skip it: some dashboard
            // sources are raw cross-origin links without ACAO, and a
            // `crossorigin` element refuses to load those at all.
            if (this.killSwitchEnabled && !el.crossOrigin) el.crossOrigin = 'anonymous';
            el.playbackRate = this.desiredPlaybackRate;
            this._attachDomListeners(el);
        }
    }

    /** Adopt a warm element that already has its `src` loaded — typically
     *  one returned from `shadow-audio.ts::consumeWarm`. Skips the load +
     *  canplay wait the normal `attachElement` + `loadCovering` flow incurs,
     *  because the element is already past `canplay` and (usually) seeked
     *  to a target offset.
     *
     *  - Detaches any prior element, re-binds DOM listeners to the new one.
     *  - Synthesises a CBR `_window` matching the element's current `src` so
     *    a subsequent `loadCovering` call short-circuits via fast path 1.
     *  - Does NOT call `play()` — the caller decides when to start playback
     *    (typically via `seekAndPlay(verseStartMs)` right after adopt).
     *
     *  VBR is not supported by adopt — the clip-URL routing requires the
     *  port to know the window's `startMs` at adopt time, and the warm
     *  element pool only stores CBR full-chapter URLs. */
    adoptElement(el: HTMLAudioElement, srcUrl: string): void {
        this.attachElement(el);
        el.playbackRate = this.desiredPlaybackRate;
        this._window = {
            startMs: 0,
            endMs: Number.POSITIVE_INFINITY,
            offsetMs: 0,
            src: srcUrl,
            isClip: false,
        };
    }

    get element(): HTMLAudioElement | null {
        return this.el;
    }

    /** Tear down: detach element, cancel pending load, drop subscribers.
     *  After dispose the port is unusable; construct a fresh one to reuse. */
    dispose(): void {
        this.attachElement(null);
        this.loadSubs.clear();
        this.playSubs.clear();
        this.pauseSubs.clear();
        this.endedSubs.clear();
        this.timeUpdateSubs.clear();
        this.errorSubs.clear();
        this.waitingSubs.clear();
        this.playingSubs.clear();
    }

    // -----------------------------------------------------------------------
    // Source binding
    // -----------------------------------------------------------------------

    /** Bind a logical source. Calling with a different `(audioUrl, reciter,
     *  vbr)` triple invalidates the current window and aborts any pending
     *  load. Calling with the same triple is a no-op. Pass `null` to
     *  detach the current source and clear the audio element's src. */
    setSource(src: AudioSource | null): void {
        if (this._sameSource(src)) return;
        this._timelineVersion++;
        this._abortPendingLoad();
        this._window = null;
        this._source = src;
        if (!src && this.el) {
            this.el.src = '';
            // Mirror existing `chapter-actions.ts` semantics — clearing src
            // followed by load() ensures the element forgets the prior
            // resource cleanly across browsers.
            this.el.load();
        }
    }

    get source(): AudioSource | null {
        return this._source;
    }

    private _sameSource(b: AudioSource | null): boolean {
        const a = this._source;
        if (a === b) return true;
        if (!a || !b) return false;
        return a.audioUrl === b.audioUrl && a.reciter === b.reciter && a.vbr === b.vbr;
    }

    // -----------------------------------------------------------------------
    // Coverage / loading
    // -----------------------------------------------------------------------

    /** Ensure the audio element has a src that covers the requested
     *  file-absolute window. Idempotent — if the current `_window` is reusable
     *  for the requested transport/window, returns a sync-resolved `ready` and
     *  `swapped: false`. For CBR, reusable means broad coverage. For VBR,
     *  reusable also requires the same clip start, because playback must begin
     *  from clip time 0. Otherwise swaps the src, attaches a canplay listener,
     *  and resolves `ready` when canplay fires.
     *
     *  Padding is per-call — not stored. Under VBR it extends only the clip
     *  end, never the start.
     *
     *  Throws if no element is attached or no source is bound. */
    loadCovering(startMs: number, endMs: number, pad?: number): LoadCoveringResult {
        if (!this.el) throw new Error('AudioPort.loadCovering: no element attached');
        if (!this._source) throw new Error('AudioPort.loadCovering: no source bound');
        // Allow `endMs = Infinity` for callers that just want "anything in
        // the file" coverage (Audio-tab full-file play). startMs must be
        // finite and not greater than endMs.
        if (!Number.isFinite(startMs) || Number.isNaN(endMs) || startMs > endMs) {
            throw new Error(`AudioPort.loadCovering: bad range [${startMs}, ${endMs}]`);
        }

        const padMs = pad ?? this.defaultPadMs;
        const needStart = startMs - padMs;
        const needEnd = endMs + padMs;
        const src = this._source;

        // Compute the window we'd load if we had to swap. Done up front so
        // we can compare the request against both the (loaded or in-flight)
        // current `_window` and decide between fast-path / pending-reuse /
        // fresh swap with a single rule.
        const useVbr = !!(src.vbr && src.reciter && src.audioUrl);
        let desiredUrl: string;
        let desiredWin: LoadedWindow;
        if (useVbr) {
            // VBR correctness depends on avoiding browser-side seeks. The
            // segment-clip endpoint is a streamed response, and a seek into a
            // not-yet-buffered clip can stall or be ignored. Keep the clip
            // start pinned to the requested playback start; padding is
            // post-roll only so boundary flushes have real audio to drain.
            const clipStart = Math.max(0, startMs);
            const clipEnd = needEnd;
            desiredUrl = buildClipUrl(src.reciter as string, src.audioUrl, clipStart, clipEnd);
            desiredWin = {
                startMs: clipStart,
                endMs: clipEnd,
                offsetMs: clipStart,
                src: desiredUrl,
                isClip: true,
            };
        } else {
            const cbrSrc = src.cbrSrc ?? src.audioUrl;
            desiredUrl = cbrSrc;
            desiredWin = {
                startMs: 0,
                endMs: Number.POSITIVE_INFINITY,
                offsetMs: 0,
                src: cbrSrc,
                isClip: false,
            };
        }

        // Fast path 1: the current `_window` (loaded OR in-flight) already
        // covers the request and matches the desired transport. Note that
        // `_window` is set synchronously by `_swapTo` so a follow-up call
        // arriving DURING a swap correctly sees the in-flight target — not
        // stale data from the prior load. When a swap is still pending
        // (canplay hasn't fired), we hand back the pending promise with
        // `swapped: true` so the caller awaits canplay before playing.
        if (this._window
            && this._window.isClip === desiredWin.isClip
            && this._canReuseWindow(this._window, desiredWin, needStart, needEnd)) {
            if (this.pendingPromise) {
                return { ready: this.pendingPromise, swapped: true, window: this._window };
            }
            return { ready: Promise.resolve(), swapped: false, window: this._window };
        }

        // Fast path 2: CBR-only synthesis. No `_window` yet (post-attach,
        // post-remount, or test fixture preset) but the element's live src
        // already matches the chapter URL. Synthesize the window without a
        // swap so subsequent calls hit fast path 1.
        if (!desiredWin.isClip
            && !this._window
            && this.el.src
            && audioSrcMatches(this.el.src, desiredUrl)) {
            this._window = desiredWin;
            return { ready: Promise.resolve(), swapped: false, window: desiredWin };
        }

        return this._swapTo(desiredUrl, desiredWin);
    }

    /** Pre-warm the audio element for the bound CBR source — sets `el.src`
     *  and calls `el.load()` so the browser fetches metadata + initial MP3
     *  frames and fires `canplay` BEFORE the user clicks play.
     *
     *  Why this matters: with `preload="none"` the element does no I/O
     *  until `loadCovering` runs on the play click. The user-perceived
     *  latency from "click play" to "audio starts" is dominated by:
     *   - HTTP fetch of the chapter MP3 (~200–500 ms cold)
     *   - MP3 header parse + decoder warmup (~100–300 ms)
     *   - `canplay` event firing
     *
     *  Pre-warming on chapter-load moves all of that off the play-click
     *  critical path. The user's subsequent `loadCovering` short-circuits
     *  via fast-path 1 (reuse) and the play feels as instant as the
     *  "seg N → seg N+1" case (which never swaps src in the first place).
     *
     *  No-op for VBR (clip URLs are per-segment; pre-warming the wrong
     *  clip would either be wasted or actively wrong) and when no source
     *  or element is bound. Returns a Promise that resolves on canplay
     *  (or rejects if a newer load supersedes); callers don't need to
     *  await it — fire-and-forget is the normal pattern. */
    prewarm(): Promise<void> {
        if (!this.el || !this._source) return Promise.resolve();
        if (this._source.vbr) return Promise.resolve();
        const startedAt = performance.now();
        const trace = (typeof localStorage !== 'undefined'
            && localStorage.getItem('insp_warmup_log') === 'true');
        if (trace) {
             
            console.log(`[prewarm] fire ${this._source.audioUrl}`);
        }
        // loadCovering(0, 0) builds the CBR window {start:0, end:Infinity}
        // and triggers _swapTo, which writes el.src + el.load() and adds
        // the canplay listener. After this resolves, _window covers any
        // future play call → fast path 1.
        const { ready } = this.loadCovering(0, 0);
        if (trace) {
            ready
                .then(() => {
                     
                    console.log(`[prewarm] canplay in ${Math.round(performance.now() - startedAt)}ms; ready=${this.el?.readyState}`);
                })
                .catch(() => {});
        }
        return ready;
    }

    /** True when the current loaded window covers `[startMs, endMs]` (no
     *  pad). Useful for callers that want to decide whether a fresh
     *  `loadCovering` call is necessary (e.g. trim drag handlers). */
    covers(startMs: number, endMs: number): boolean {
        const w = this._window;
        if (!w) return false;
        return w.startMs <= startMs && w.endMs >= endMs;
    }

    get window(): LoadedWindow | null {
        return this._window;
    }

    private _canReuseWindow(
        current: LoadedWindow,
        desired: LoadedWindow,
        needStart: number,
        needEnd: number,
    ): boolean {
        if (desired.isClip) {
            // For VBR clips, "covering" is not enough. Reusing a clip whose
            // start is earlier than the requested start forces currentTime to
            // seek inside the server stream, which is exactly the VBR failure
            // mode this abstraction is supposed to hide.
            return current.startMs === desired.startMs && current.endMs >= needEnd;
        }
        return current.startMs <= needStart && current.endMs >= needEnd;
    }

    // -----------------------------------------------------------------------
    // Coordinate translation (file-absolute outside, clip-relative inside)
    // -----------------------------------------------------------------------

    toClipMs(fileMs: number): number {
        const off = this._window?.offsetMs ?? 0;
        return fileMs - off;
    }

    toFileMs(clipMs: number): number {
        const off = this._window?.offsetMs ?? 0;
        return clipMs + off;
    }

    /** File-absolute current time in ms. Returns 0 when no element is
     *  attached. */
    currentTimeMs(): number {
        if (!this.el) return 0;
        const off = this._window?.offsetMs ?? 0;
        return this.el.currentTime * 1000 + off;
    }

    coordinates(): { offsetMs: number } {
        return { offsetMs: this._window?.offsetMs ?? 0 };
    }

    // -----------------------------------------------------------------------
    // Playback (file-absolute targets)
    // -----------------------------------------------------------------------

    /** Seek to a file-absolute position. Sync write — assigning to
     *  `audio.currentTime` is synchronous; the seeked event fires later
     *  but callers don't need to wait on that. Does NOT call play().
     *
     *  When the target is outside the currently-loaded window, the caller
     *  is expected to `loadCovering(...)` first; this method does NOT
     *  auto-load (that's a caller decision because pre-loads outside the
     *  port's knowledge are common, e.g. AudioRange's `start()`). */
    seek(fileMs: number): void {
        if (!this.el) return;
        const target = Math.max(0, this.toClipMs(fileMs));
        this.el.currentTime = target / 1000;
        this._timelineVersion++;
    }

    /** Seek + play. Sync — issues `play()` via `safePlay` (which swallows
     *  AbortError). The play promise itself is fire-and-forget. */
    seekAndPlay(fileMs: number): void {
        if (!this.el) return;
        this.seek(fileMs);
        this.el.playbackRate = this.desiredPlaybackRate;
        // Restore gain ramp before playing; matches the legacy
        // `_seekAndPlay` ordering in `audio-range.ts`.
        if (this.killSwitchEnabled) uncutAudio(this.el);
        safePlay(this.el);
    }

    /** Resume play at the current position. Use for play/pause toggles where
     *  the AudioRange is still alive and the rAF chain is paused-but-ticking;
     *  no seek needed. */
    play(): void {
        if (!this.el) return;
        this.el.playbackRate = this.desiredPlaybackRate;
        if (this.killSwitchEnabled) uncutAudio(this.el);
        safePlay(this.el);
    }

    pause(): void {
        if (this.el && !this.el.paused) this.el.pause();
    }

    /** Pause + Web Audio gain ramp to 0 to silence OS-buffered samples
     *  (mirrors `audio-range.ts::_pauseAndFlush`). */
    pauseAndFlush(): void {
        if (!this.el) return;
        if (this.killSwitchEnabled) cutAudio(this.el);
        if (!this.el.paused) this.el.pause();
    }

    /** Restore gain to 1. Called before resuming play after a flush. */
    uncut(): void {
        if (this.el && this.killSwitchEnabled) uncutAudio(this.el);
    }

    get paused(): boolean {
        return this.el?.paused ?? true;
    }

    get playbackRate(): number {
        return this.el?.playbackRate ?? this.desiredPlaybackRate;
    }

    setPlaybackRate(rate: number): void {
        this.desiredPlaybackRate = rate;
        if (this.el) this.el.playbackRate = rate;
    }

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    onLoad(cb: (w: LoadedWindow) => void): Unsub {
        this.loadSubs.add(cb);
        return () => this.loadSubs.delete(cb);
    }

    onPlay(cb: () => void): Unsub {
        this.playSubs.add(cb);
        return () => this.playSubs.delete(cb);
    }

    onPause(cb: () => void): Unsub {
        this.pauseSubs.add(cb);
        return () => this.pauseSubs.delete(cb);
    }

    onEnded(cb: () => void): Unsub {
        this.endedSubs.add(cb);
        return () => this.endedSubs.delete(cb);
    }

    /** Fires on the element's `timeupdate` event with file-absolute ms. */
    onTimeUpdate(cb: (fileMs: number) => void): Unsub {
        this.timeUpdateSubs.add(cb);
        return () => this.timeUpdateSubs.delete(cb);
    }

    onError(cb: (err: MediaError | null) => void): Unsub {
        this.errorSubs.add(cb);
        return () => this.errorSubs.delete(cb);
    }

    /** Fires on the element's `waiting` event — playback has paused because
     *  the next frame isn't yet available. Use for buffering indicators.
     *  Distinct from `pause`: `pause` is user/programmatic; `waiting` is
     *  network/decoder starvation. */
    onWaiting(cb: () => void): Unsub {
        this.waitingSubs.add(cb);
        return () => this.waitingSubs.delete(cb);
    }

    /** Fires on the element's `playing` event — actual audible playback has
     *  started or resumed. Distinct from `play`, which fires synchronously
     *  when `.play()` is called even if the element is still loading. */
    onPlaying(cb: () => void): Unsub {
        this.playingSubs.add(cb);
        return () => this.playingSubs.delete(cb);
    }

    // -----------------------------------------------------------------------
    // Internal: src swap
    // -----------------------------------------------------------------------

    private _swapTo(url: string, win: LoadedWindow): LoadCoveringResult {
        if (!this.el) throw new Error('AudioPort._swapTo: no element');
        this._timelineVersion++;
        this._abortPendingLoad();
        const gen = ++this.loadGen;
        // Sync update: `_window` reflects the INTENDED window from the
        // moment the swap is initiated, not when canplay fires. A follow-up
        // `loadCovering` arriving during the swap sees the new window's
        // offsetMs and either reuses the in-flight ready (covers) or aborts
        // and starts a fresh swap (doesn't cover) — instead of fast-pathing
        // against stale data and writing wrong `currentTime` into a still-
        // loading element. The audible regression this fixes: in VBR Adjust
        // mode, an immediate split-left/right click after enter would seek
        // and play before the wider clip's canplay had updated `_window`,
        // landing the playhead in the wrong file-absolute position.
        this._window = win;
        const promise = new Promise<void>((resolve, reject) => {
            const handler = (): void => {
                if (gen !== this.loadGen) return;
                this.el?.removeEventListener('canplay', handler);
                this.pendingCanplay = null;
                this.pendingReady = null;
                this.pendingPromise = null;
                this._fanout(this.loadSubs, win);
                resolve();
            };
            this.pendingCanplay = handler;
            this.pendingReady = { resolve, reject };
            this.el!.addEventListener('canplay', handler, { once: true });
        });
        // Pin a no-op rejection handler so an internal abort (overlapping
        // load, setSource change, dispose) never trips Node's unhandled-
        // rejection warning when the caller hasn't yet attached its own
        // `.catch` / `await`. Promise subscribers are independent — any
        // external `await ready` still sees the rejection.
        promise.catch(() => {});
        this.pendingPromise = promise;
        this.el.playbackRate = this.desiredPlaybackRate;
        this.el.src = url;
        this.el.load();
        return { ready: promise, swapped: true, window: win };
    }

    private _abortPendingLoad(): void {
        if (this.pendingCanplay && this.el) {
            this.el.removeEventListener('canplay', this.pendingCanplay);
        }
        this.pendingCanplay = null;
        this.pendingPromise = null;
        if (this.pendingReady) {
            const r = this.pendingReady;
            this.pendingReady = null;
            const err = new DOMException('aborted by newer load', 'AbortError');
            r.reject(err);
        }
    }

    // -----------------------------------------------------------------------
    // Internal: DOM event re-emission
    // -----------------------------------------------------------------------

    private _attachDomListeners(el: HTMLAudioElement): void {
        const onPlay = (): void => this._fanout(this.playSubs);
        const onPause = (): void => this._fanout(this.pauseSubs);
        const onEnded = (): void => this._fanout(this.endedSubs);
        const onTimeUpdate = (): void => this._fanout(this.timeUpdateSubs, this.currentTimeMs());
        const onError = (): void => this._fanout(this.errorSubs, el.error);
        const onWaiting = (): void => this._fanout(this.waitingSubs);
        const onPlaying = (): void => this._fanout(this.playingSubs);
        const onSeeking = (): void => { this._timelineVersion++; };
        el.addEventListener('play', onPlay);
        el.addEventListener('pause', onPause);
        el.addEventListener('ended', onEnded);
        el.addEventListener('timeupdate', onTimeUpdate);
        el.addEventListener('error', onError);
        el.addEventListener('waiting', onWaiting);
        el.addEventListener('playing', onPlaying);
        el.addEventListener('seeking', onSeeking);
        this.domListeners = [
            ['play', onPlay],
            ['pause', onPause],
            ['ended', onEnded],
            ['timeupdate', onTimeUpdate],
            ['error', onError],
            ['waiting', onWaiting],
            ['playing', onPlaying],
            ['seeking', onSeeking],
        ];
    }

    private _detachDomListeners(): void {
        if (!this.el) { this.domListeners = []; return; }
        for (const [type, fn] of this.domListeners) {
            this.el.removeEventListener(type, fn);
        }
        this.domListeners = [];
    }

    private _fanout<T>(subs: Set<(arg: T) => void>, arg?: T): void {
        // Snapshot — subscribers may unsubscribe during their own callback.
        for (const fn of [...subs]) {
            try { fn(arg as T); }
            catch (e) {
                // Don't let a buggy subscriber take out the whole fanout.
                if (!isAbortError(e)) console.error('[AudioPort] subscriber threw', e);
            }
        }
    }
}
