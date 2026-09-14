/**
 * Segments tab — playback control state.
 */

import { writable } from 'svelte/store';

import { AudioPort } from '../../../lib/playback/audio-port';
import { LS_KEYS } from '../../../lib/utils/constants';

/**
 * Whether autoplay is enabled — chapter-mode only. When ON, a main-list
 * play runs chapter-continuously through the rest of the chapter audio.
 * When OFF, a main-list play stops at the end of the played segment.
 * Accordion plays always stop at segment end regardless of this flag.
 * Persisted to localStorage via LS_KEYS.SEG_AUTOPLAY; default ON.
 */
export const autoPlayEnabled = writable<boolean>(
    localStorage.getItem(LS_KEYS.SEG_AUTOPLAY) !== 'false',
);

/**
 * Whether auto-scroll (keep the playing segment visible in #seg-list) is
 * enabled. Persisted to localStorage via LS_KEYS.SEG_AUTOSCROLL; default ON.
 */
export const autoScrollEnabled = writable<boolean>(
    localStorage.getItem(LS_KEYS.SEG_AUTOSCROLL) !== 'false',
);

/**
 * Whether the segment-end chime is enabled — a short beep marking the join
 * between one segment and the next while autoplay walks the list. Only
 * meaningful with `autoPlayEnabled` ON (autoplay OFF stops at the segment
 * end, so there is no "next one" to announce); the footer toggle is disabled
 * in that state, and every fire site gates on autoplay independently so a
 * stale persisted `true` can't leak a beep into non-autoplay playback.
 * Persisted to localStorage via LS_KEYS.SEG_END_CHIME; default OFF.
 */
export const segmentEndChimeEnabled = writable<boolean>(
    localStorage.getItem(LS_KEYS.SEG_END_CHIME) === 'true',
);

/** Which audio element is currently driving playback: 'main' = the main
 *  segments tab audio element, or `null` when idle. Retained as a typed
 *  token (rather than a bare boolean) so a future secondary audio source
 *  can extend the union. */
export const activeAudioSource = writable<'main' | null>(null);

/** The <audio> element driving segments-tab playback. Populated by
 *  SegmentsAudioControls.svelte via bind:this once AudioPlayer mounts.
 *  Consumers read via get(segAudioElement) and null-check.
 *
 *  @deprecated Use `segPort` instead. New code should never read the
 *  audio element directly — every operation (load, seek, currentTime,
 *  play, pause, src-swap) goes through the port so the CBR-vs-VBR
 *  transport is hidden. This export is retained transitionally for
 *  call sites still being migrated; deleted in the final cleanup phase. */
export const segAudioElement = writable<HTMLAudioElement | null>(null);

/** Single AudioPort instance for the segments tab. Module-scoped so its
 *  identity is stable across HMR and component remounts. SegmentsAudioControls
 *  attaches the bound `<audio>` element on mount; every other consumer
 *  imports this port directly and reads file-absolute milliseconds.
 *
 *  Coordinate space: file-absolute ms — always. The port translates to
 *  the element's clip-relative `currentTime` internally for VBR clips. */
export const segPort: AudioPort = new AudioPort();

/** True when `segPort` has an `<audio>` element bound. UI binding for
 *  `disabled={!$segPortReady}` on the play button (replaces the old
 *  `disabled={!audioEl}` reactive). */
export const segPortReady = writable<boolean>(false);

/** The #seg-list scroll container. Populated by SegmentsList.svelte via
 *  bind:this. Consumers read via get(segListElement) and null-check. */
export const segListElement = writable<HTMLDivElement | null>(null);

/** Set of mounted container elements that host seg-row canvases. Populated
 *  by each consuming component's onMount via registerWaveformContainer.
 *  `redrawPeaksWaveforms` iterates this set to find canvases needing redraw
 *  without hardcoding DOM IDs. */
export const waveformContainers = writable<Set<HTMLElement>>(new Set());

/** Register a container element that hosts seg-row canvases. Returns a
 *  cleanup function to call from the component's onMount teardown. */
export function registerWaveformContainer(el: HTMLElement): () => void {
    waveformContainers.update((s) => {
        s.add(el);
        return s;
    });
    return () => {
        waveformContainers.update((s) => {
            s.delete(el);
            return s;
        });
    };
}

/** Svelte action: registers the element as a waveform container for its
 *  lifetime in the DOM. Use as `<div use:waveformContainer>...</div>`.
 *  Re-runs correctly when the element is destroyed and recreated by an
 *  `{#if}` block. */
export function waveformContainer(node: HTMLElement): { destroy(): void } {
    const cleanup = registerWaveformContainer(node);
    return { destroy: cleanup };
}

/** Current playback speed multiplier. Persisted to localStorage via
 *  LS_KEYS.SEG_SPEED. SegmentsAudioControls' speed <select> writes to it;
 *  hot paths that need to set audioEl.playbackRate read via
 *  get(playbackSpeed). */
export const playbackSpeed = writable<number>(1);

/** Label on the main play/pause button. Reactive markup in
 *  SegmentsAudioControls. */
export const playButtonLabel = writable<'Play' | 'Pause'>('Play');

/** The {chapter, index} pair currently being played back, or `null` when
 *  nothing is playing. SegmentRow reactively applies class:playing when both
 *  chapter and index match its seg. Written by updateSegHighlight from the
 *  rAF tick; the Svelte safe_not_equal check plus the getter identity guard
 *  in setPlayingSegment() make same-value sets no-ops, keeping the 60fps hot
 *  path from triggering unnecessary subscriber work.
 *
 *  Chapter-scoped so a segment with the same index in different chapters (as
 *  happens when the validation panel shows "All Chapters") does not light
 *  up every same-indexed row across the chapter set. */
export interface PlayingSegment {
    chapter: number;
    index: number;
    /**
     * Which surface initiated this play. `main` = main segment list row,
     * `accordion` = a row mounted inside a validation accordion card. Used
     * by main-list autoscroll (and any other "follow the playhead" UI) to
     * stay put when the play came from an accordion — accordion plays are
     * self-contained and must NOT yank the main list around.
     *
     * Optional on the wire so identity-preserving updates (rAF tick,
     * cross-segment advance, post-mutation reconcile) can omit it and
     * inherit the existing pair's origin via `setPlayingSegment`.
     */
    origin?: 'main' | 'accordion';
}
export const playingSegmentIndex = writable<PlayingSegment | null>(null);

/** Identity-guarded setter for `playingSegmentIndex` so the 60fps rAF tick
 *  does not allocate a fresh object when the active pair has not changed.
 *  Svelte's safe_not_equal returns true for any two object literals even
 *  when their contents match; this guard avoids the resulting subscriber
 *  wake-up storm on every frame.
 *
 *  When `next` omits `origin`, the previous pair's origin is preserved —
 *  so updates from rAF / time-update / advance ticks don't accidentally
 *  reclassify an accordion play as a main-list one. */
export function setPlayingSegment(next: PlayingSegment | null): void {
    playingSegmentIndex.update((cur) => {
        if (cur === next) return cur;
        if (next == null) return null;
        const origin = next.origin ?? cur?.origin ?? 'main';
        if (
            cur
            && cur.chapter === next.chapter
            && cur.index === next.index
            && cur.origin === origin
        ) return cur;
        return { chapter: next.chapter, index: next.index, origin };
    });
}

/** The staged (pre-split) piece the playhead is currently inside, as its
 *  own time window, or null when no staged piece is sounding. Staged pieces
 *  share their parent's (chapter, index), so `playingSegmentIndex` alone
 *  cannot say which of them is live — the draw layer publishes this so only
 *  that piece's row lights up. Identity-guarded: the rAF tick sets it every
 *  frame. */
export const stagedPlayheadWindow = writable<{ start: number; end: number } | null>(null);

export function setStagedPlayheadWindow(next: { start: number; end: number } | null): void {
    stagedPlayheadWindow.update((cur) => {
        if (cur === next) return cur;
        if (next == null) return null;
        if (cur && cur.start === next.start && cur.end === next.end) return cur;
        return { start: next.start, end: next.end };
    });
}

/** True when main-tab audio is playing (not paused, and activeAudioSource
 *  === 'main'). Drives the per-row play-button glyph (stop vs play). */
export const isMainAudioPlaying = writable<boolean>(false);

/** True between a play being requested and the FIRST audible frame
 *  (`playing` event). A play click flips the button to "pause" and jumps the
 *  playhead to the segment start synchronously, but the audio bytes for the
 *  clicked segment are often not yet buffered, so sound starts hundreds of ms
 *  later (cold CDN fetch / segment not prewarmed) — during which the UI was
 *  falsely signalling "playing" (issue #172). The footer play button shows a
 *  buffering spinner while this is true so the control is honest about the
 *  click→audible gap. Set by the play entry points, cleared on the first
 *  `playing` event (or pause/ended). Initial-gap only — mid-playback rebuffers
 *  do NOT re-raise it, to avoid spinner flicker at segment boundaries. */
export const segAudioBuffering = writable<boolean>(false);
