/**
 * Audio playback, animation, highlight tracking, and play status.
 *
 * Chapter-continuous model: `segPort` holds the full chapter audio behind it
 * (loaded by `loadChapterData`). Clicking a segment simply seeks to that
 * segment's `time_start` and plays forward — the chapter audio plays through
 * naturally, and `onSegTimeUpdate`'s time→segment lookup keeps the active
 * highlight in sync as playback crosses segment boundaries.
 *
 * The legacy `AudioRange` per-segment clamp and autoplay advance policy were
 * removed when the player became chapter-continuous. Edit-mode preview loops
 * (Trim / Split L/R / Split region) still live in `play-range.ts` — that
 * module is untouched.
 *
 * One narrow exception to "play through naturally": deleting a segment is a
 * pure state edit that does NOT touch the chapter audio file, so the bytes of
 * a deleted region linger between its surviving neighbours. `_maybeSkipDeletedGap`
 * (driven by `_drawLoop`) reconciles chapter-continuous playback against the
 * CURRENT segment list — once the playhead crosses into a region covered by no
 * displayed segment it seeks forward to the next segment that still exists,
 * instead of bleeding through the orphaned audio. Contiguous boundaries leave
 * no gap, so normal playback stays seamless.
 *
 * A small rAF (`_drawLoop`) is started by `startSegAnimation` (DOM 'play'
 * event re-emission via the AudioPort) and stopped by `stopSegAnimation`
 * (DOM 'pause' / 'ended'). Each frame draws the playhead on the active row
 * and reconciles the row highlight against `segCurrentIdx`.
 *
 * Coordinate space: every `timeMs` value flowing through this module is
 * **file-absolute milliseconds**. The `segPort` port translates to / from
 * the `<audio>.currentTime` clip-relative space internally for VBR clips.
 * Callers never see clip offsets.
 */

import { get } from 'svelte/store';

import { displayTimeMs } from '../../../../lib/playback/audio-graph';
import { AudioRange } from '../../../../lib/playback/audio-range';
import type { Segment } from '../../../../lib/types/view-models';
import { type AnimationLoop,createAnimationLoop } from '../../../../lib/utils/animation';
import { audioSrcMatches } from '../../../../lib/utils/audio';
import {
    getChapterSegments,
    getSegByChapterIndex,
    pickerDisplayChapter,
    segAllData,
    segCurrentIdx,
    segData,
    selectedChapter,
    selectedReciter,
} from '../../stores/chapter';
import { editCanvas, editMode, splitPreviewSelection } from '../../stores/edit';
import { displayedSegments } from '../../stores/filters';
import {
    accordionNavCursor,
    activeAudioSource,
    autoPlayEnabled,
    isMainAudioPlaying,
    playbackSpeed,
    playButtonLabel,
    playingSegmentIndex,
    segAudioBuffering,
    segmentEndChimeEnabled,
    segPort,
    setAccordionNavCursor,
    setActiveWordCursor,
    setPlayingSegment,
    setStagedPlayheadWindow,
} from '../../stores/playback';
import type { SegCanvas } from '../../types/segments-waveform';
import { accordionStep, isCurrentStop } from '../accordion-nav';
import { CHIME_TOTAL_MS, playSegmentEndChime } from './chime';
import { drawSegPlayhead, drawWaveformFromPeaksForSeg } from '../waveform/draw-seg';
import { _fetchPeaksForClick } from '../waveform/utils';
import {
    _playRange,
    editPreviewPlaying,
    getPlayRangeRAF,
    setPreviewLooping,
} from './play-range';
import { nextDisplayedSeg, nextSiblingSeg } from './resolvers';
import { getRowEntriesFor } from './row-registry';
import { resolveSegSource } from './source';
import { warmSeg } from './warmup';
import { wordIndexAt } from '../samples/word-timing';

// ---------------------------------------------------------------------------
// Module-local state
// ---------------------------------------------------------------------------

/** Last drawn (chapter, index) pair so the animation loop can erase the
 *  playhead on the previous row when playback advances. Carries the chapter
 *  so cross-chapter advance (accordion -> another chapter's row) erases from
 *  the right canvas. */
let _prevPlaying: { chapter: number; index: number } | null = null;

/** Canvases of staged (not-yet-dispatched) split pieces that currently carry a
 *  playhead. Staged pieces slice one parent window between them, so when the
 *  cursor crosses out of a piece that piece needs its waveform repainted once
 *  to erase the stale line — this set is what makes "once" possible instead of
 *  repainting every idle piece on every frame. WeakSet so an unmounted row's
 *  canvas is collectable without any teardown bookkeeping. */
const _stagedCursorCanvases = new WeakSet<SegCanvas>();

/**
 * End of the contiguous piece GROUP the current play spans, or null when the
 * play covers a single segment. `ensureBoundedRange` rebuilds `_segRange` from
 * the pair's own segment — which after a split is one piece — so without this
 * a rebuild mid-play re-bounds a group play to that piece's end and the audio
 * stops there.
 */
let _activeGroupEndMs: number | null = null;

/** Active segment-bounded range. Used for accordion plays (always bounded
 *  to the played segment) and chapter-mode plays when autoplay is OFF.
 *  Chapter-mode + autoplay ON plays through the chapter — no range. */
let _segRange: AudioRange | null = null;

/** Sample word review owns a loop independently of autoplay. An old row's
 * unmount must not tear down a newer row's preview. */
let _wordTimingRange: AudioRange | null = null;
let _wordTimingOwner: string | null = null;

/** Playhead-draw rAF. Replaces the AudioRange-owned tick for the chapter-
 *  continuous path. Under segment-bounded play, AudioRange owns its own
 *  rAF that fires `_onRangeTick` — we keep this rAF off in that case.
 *  Runs only while audio is playing AND not in a CANVAS-REPLACING edit
 *  mode. Reference edit leaves the row's normal waveform canvas in place,
 *  so the chapter cursor must keep advancing through it; only 'trim' /
 *  'split' modes hand the canvas off to `_playRange`'s preview rAF. */
const _drawLoop: AnimationLoop = createAnimationLoop(() => {
    if (_wordTimingRange) return;
    const m = get(editMode);
    if (m === 'trim' || m === 'split') return;
    const t = segPort.currentTimeMs();
    // Skip past deleted-segment gaps before drawing — on a skip the playhead
    // is seeked this frame, so let the next tick draw it at the new position.
    if (_maybeSkipDeletedGap(t)) return;
    drawActivePlayhead(t);
    updateSegHighlight();
});

/**
 * Chapter-continuous autoplay gap-skip.
 *
 * Deleting a segment drops it from the list but leaves the chapter audio file
 * untouched, so the deleted region's bytes still sit between its surviving
 * neighbours. Under chapter-continuous playback (autoplay ON, CBR) nothing
 * bounds the playhead, so it would otherwise sail straight through that
 * now-orphaned audio. This reconciles playback against the CURRENT segment
 * list: once the playhead crosses the active segment's end into a region
 * covered by NO displayed segment, seek forward to the next segment that still
 * exists (chosen by time, so a manual scrub into a far gap jumps forward, never
 * back). If nothing remains ahead, stop rather than play the chapter's trailing
 * audio.
 *
 * Contiguous boundaries leave no gap — the playhead lands inside the next
 * segment, so segment-to-segment playback stays seamless and this is a no-op.
 * VBR is unaffected: it plays isolated per-segment clips and never reaches the
 * chapter-continuous draw loop. Bounded plays (autoplay-OFF, accordion) enforce
 * their own `stop` policy via `_segRange` and are short-circuited here.
 *
 * @returns true when it seeked or stopped (caller skips this frame's draw).
 */
function _maybeSkipDeletedGap(timeMs: number): boolean {
    if (get(editMode)) return false;
    if (_segRange) return false; // bounded range owns its own boundary policy
    if (!get(autoPlayEnabled)) return false;
    const active = get(playingSegmentIndex);
    if (!active || active.origin === 'accordion') return false;
    const seg = getSegByChapterIndex(active.chapter, active.index);
    if (!seg) return false;
    // Still inside the active segment — nothing to skip.
    if (timeMs < seg.time_end) return false;

    const displayed = get(displayedSegments);
    if (!displayed) return false;
    const activeUrl = _curChapterUrl();

    // Rolled into a contiguous neighbour? Let chapter-continuous playback carry
    // on; `onSegTimeUpdate` migrates the highlight as the playhead crosses in.
    const inSegment = displayed.some(
        (s) => timeMs >= s.time_start && timeMs < s.time_end
            && audioSrcMatches(s.audio_url, activeUrl),
    );
    if (inSegment) return false;

    // In a gap. Find the nearest segment that still exists ahead of the
    // playhead (smallest time_start ≥ timeMs on the active chapter audio).
    let next: Segment | null = null;
    for (const s of displayed) {
        if (!audioSrcMatches(s.audio_url, activeUrl)) continue;
        if (s.time_start >= timeMs && (!next || s.time_start < next.time_start)) {
            next = s;
        }
    }

    if (!next) {
        // No surviving segment ahead — stop at the gap instead of bleeding into
        // the chapter's trailing audio.
        segPort.pause();
        setPlayingSegment(null);
        segCurrentIdx.set(-1);
        _drawLoop.stop();
        return true;
    }

    // Jump the active pair (drives class:playing + the playhead row) and seek to
    // the surviving segment. `segCurrentIdx` MUST advance in lockstep: this same
    // rAF loop calls `updateSegHighlight` right after us, and it forces
    // `playingSegmentIndex` back onto `segCurrentIdx` every frame. Leaving
    // `segCurrentIdx` on the segment we just left lets that bridge snap the
    // active pair backwards until `onSegTimeUpdate`'s slower tick catches up —
    // the highlight stalls/repeats on the previous segment across the gap.
    // Move the active pair immediately (the highlight should track the jump
    // even while the chime's gap holds the audio), then let the chime decide
    // whether the seek happens now or after its gap.
    setPlayingSegment({ chapter: next.chapter ?? active.chapter, index: next.index });
    segCurrentIdx.set(next.index);
    const target = next.time_start;
    const wasPlaying = !segPort.paused;
    _chimeSegmentEnd(() => {
        if (wasPlaying && segPort.paused) segPort.seekAndPlay(target);
        else segPort.seek(target);
    });
    return true;
}

/** Reset playhead draw-state refs so the draw layer does not point to nodes
 *  destroyed by the next {#each} reconciliation. Called by filters-apply.ts
 *  before re-rendering the list. The playing-row highlight is Svelte-owned
 *  now (class:playing driven by playingSegmentIndex) so only the canvas
 *  playhead draw state lives here. */
export function resetHighlightRefs(): void {
    _prevPlaying = null;
}

/** Tear down active playback rAF and any segment-bounded AudioRange.
 *  Called explicitly on edit-mode entry, per-reciter clear, and chapter
 *  swap. */
export function disposeSegPlayback(): void {
    cancelChimeGap();
    _drawLoop.stop();
    segAudioBuffering.set(false);
    _segRange?.dispose();
    _segRange = null;
    _wordTimingRange?.dispose();
    _wordTimingRange = null;
    _wordTimingOwner = null;
}

/** Repeat a sample segment or a selected word. All times are file-absolute. */
export function startWordTimingPreview(
    seg: Segment, chapter: number, startMs: number, endMs: number, seekMs = startMs,
): void {
    if (!segPort.element || !seg.segment_uid || endMs <= startMs) return;
    cancelChimeGap();
    _drawLoop.stop();
    _segRange?.dispose();
    _segRange = null;
    _wordTimingRange?.dispose();
    _wordTimingRange = null;
    const source = resolveSegSource(seg, chapter);
    if (source) segPort.setSource(source);
    _wordTimingOwner = seg.segment_uid;
    segCurrentIdx.set(seg.index);
    setPlayingSegment({ chapter, index: seg.index, origin: 'main' });
    _wordTimingRange = new AudioRange({
        port: segPort,
        range: { startMs: Math.max(startMs, Math.min(seekMs, endMs - 1)), endMs },
        policy: { kind: 'loop' },
        onTick: (timeMs) => drawActivePlayhead(timeMs),
        playbackRate: () => get(playbackSpeed),
    });
    _wordTimingRange.start();
    // The initial seek can be inside the range; subsequent repeats begin at its start.
    _wordTimingRange.setRange({ startMs, endMs });
    void _fetchPeaksForClick(seg, chapter);
}

/** A dragged locked word changes its loop without reloading the audio. */
export function updateWordTimingPreview(ownerUid: string, startMs: number, endMs: number): void {
    if (_wordTimingOwner !== ownerUid || !_wordTimingRange || endMs <= startMs) return;
    _wordTimingRange.setRange({ startMs, endMs });
    const currentMs = segPort.currentTimeMs();
    if (currentMs < startMs || currentMs >= endMs) segPort.seek(startMs);
}

export function stopWordTimingPreview(ownerUid: string): void {
    if (_wordTimingOwner !== ownerUid) return;
    _wordTimingRange?.dispose();
    _wordTimingRange = null;
    _wordTimingOwner = null;
    setPlayingSegment(null);
    segCurrentIdx.set(-1);
    drawActivePlayhead();
}

/** Back-compat alias — older call sites import this name. */
export const disposeSegRange = disposeSegPlayback;

/** Active-chapter audio URL — independent of which segment's source the
 *  port is currently bound to. Used by `onSegTimeUpdate`'s cross-segment
 *  scan (filters `displayed` to active-chapter rows by URL match) and by
 *  warmup's "skip if next seg shares chapter audio" gate. Reads from
 *  `segData.audio_url` (the active chapter's canonical CDN URL set by
 *  `loadChapterData`) rather than `segPort.source.audioUrl` so per-row
 *  source rebinding doesn't break either consumer when an accordion row
 *  from another chapter is the most recent thing the port loaded. */
function _curChapterUrl(): string {
    return get(segData)?.audio_url ?? '';
}

// ---------------------------------------------------------------------------
// Segment-bounded AudioRange wiring
// ---------------------------------------------------------------------------

/**
 * Sound the segment-end chime, if the user asked for one.
 *
 * Gated on `autoPlayEnabled` as well as the toggle: the chime announces "that
 * segment ended, here comes the next", which only exists under autoplay. The
 * footer disables the toggle when autoplay is off, but a persisted `true` from
 * an earlier session would otherwise survive into a non-autoplay play.
 */
function _chimeArmed(): boolean {
    return get(segmentEndChimeEnabled) && get(autoPlayEnabled);
}

/**
 * How long playback is held silent around the chime. The chime itself is
 * `CHIME_TOTAL_MS`; the remainder is air on either side so the beep reads as a
 * deliberate marker in a gap rather than something clipped onto the front of
 * the next segment.
 */
const CHIME_GAP_MS = CHIME_TOTAL_MS + 130;

/** Fallback timer for an in-flight chime gap. At most one — a second boundary
 *  landing mid-gap supersedes the first rather than stacking. */
let _chimeGapTimer: ReturnType<typeof setTimeout> | null = null;
/** True while a chime gap is holding playback, whichever clock ends it. Read by
 *  `stopSegAnimation` to tell our own transient pause from a real one. */
let _chimeGapActive = false;
/** Invalidates the resume of a superseded gap: each gap captures the token it
 *  was started with and refuses to run if it no longer matches. */
let _chimeGapToken = 0;

/** Abandon an in-flight chime gap WITHOUT running its resume. Called whenever
 *  something else takes over the transport (user pause, a new play, teardown)
 *  so a stale gap can't restart audio the user just stopped. */
export function cancelChimeGap(): void {
    _chimeGapToken++;
    _chimeGapActive = false;
    if (_chimeGapTimer === null) return;
    clearTimeout(_chimeGapTimer);
    _chimeGapTimer = null;
}

/**
 * Sound the segment-end chime in a short silent gap, then hand control back.
 *
 * `resume` is what actually moves playback to the next segment. With the chime
 * off it runs immediately (a microtask, matching the previous behaviour — never
 * synchronously, so callers can still be inside a boundary callback). With the
 * chime on, the port is paused first so the beep lands in silence instead of
 * over the next segment's opening, and `resume` runs once the gap elapses.
 *
 * Gated on `autoPlayEnabled` as well as the toggle: the chime announces "that
 * segment ended, here comes the next", which only exists under autoplay. The
 * footer disables the toggle when autoplay is off, but a persisted `true` from
 * an earlier session would otherwise survive into a non-autoplay play.
 */
function _chimeSegmentEnd(resume: () => void): void {
    cancelChimeGap();
    if (!_chimeArmed()) {
        queueMicrotask(resume);
        return;
    }
    // pauseAndFlush drains the OS sink too, so the gap is genuinely silent rather than
    // trailing ~50-200ms of pre-decoded samples over the beep.
    segPort.pauseAndFlush();
    _chimeGapActive = true;
    const token = _chimeGapToken;
    const done = (): void => {
        if (token !== _chimeGapToken) return;   // superseded
        _chimeGapToken++;
        _chimeGapActive = false;
        if (_chimeGapTimer !== null) {
            clearTimeout(_chimeGapTimer);
            _chimeGapTimer = null;
        }
        resume();
    };
    // Prefer the chime's own Web Audio clock to end the gap: a hidden tab
    // clamps `setTimeout` to >=1s, which would stretch every boundary into a
    // second of dead air precisely when the user is listening rather than
    // watching. The timer stays armed as a fallback (no Web Audio, or a
    // throttled chime that never scheduled) at a duration that can't fire
    // before the audio clock would have.
    const scheduled = playSegmentEndChime(done);
    _chimeGapTimer = setTimeout(done, scheduled ? CHIME_GAP_MS * 3 : CHIME_GAP_MS);
}

/**
 * Keep the pair on the piece the playhead is inside during a GROUP play.
 *
 * A card's pieces are contiguous, so a group play crosses from one to the next
 * with no new `play()`. `onSegTimeUpdate` cannot be relied on for that: it
 * bails whenever the port isn't playing the *active* chapter (accordion cards
 * routinely mount rows from another chapter) and it only scans the main list's
 * filtered slice. Without this the pair — and with it the row highlight and
 * the waveform cursor — stuck on the piece that started the play while the
 * audio ran on into the next one.
 *
 * Runs on the bounded-range tick, which is exactly the accordion/group-play
 * path, and moves `segCurrentIdx` + the nav cursor in lockstep (see
 * `updateSegHighlight`, which forces the pair back onto `segCurrentIdx`).
 *
 * `groupEndMs` is the caller's group bound (`_activeGroupEndMs` on the tick);
 * null means this play covers a single segment and there is nothing to follow.
 */
export function followGroupPlayhead(timeMs: number, groupEndMs: number | null): void {
    if (groupEndMs == null) return;
    const active = get(playingSegmentIndex);
    if (!active) return;
    const cur = getSegByChapterIndex(active.chapter, active.index);
    if (cur && timeMs >= cur.time_start && timeMs < cur.time_end) return; // still inside
    for (const seg of getChapterSegments(active.chapter)) {
        if (timeMs < seg.time_start || timeMs >= seg.time_end) continue;
        if (seg.index === active.index) return;
        setPlayingSegment({ chapter: active.chapter, index: seg.index });
        segCurrentIdx.set(seg.index);
        setAccordionNavCursor({
            uid: seg.segment_uid ?? '',
            chapter: active.chapter,
            index: seg.index,
            startMs: seg.time_start,
            endMs: seg.time_end,
        });
        return;
    }
}

function _onRangeTick(timeMs: number): void {
    followGroupPlayhead(timeMs, _activeGroupEndMs);
    drawActivePlayhead(timeMs);
    updateSegHighlight();
}

function _onRangeBoundary(ev: { reason: string }): void {
    if (ev.reason === 'stop') {
        // Segment ended in bounded mode. With autoplay ON inside an accordion,
        // advance to the next card in the accordion sequence (the one narrow
        // case where accordion playback does NOT stop). Deferred off this tick
        // so we don't dispose this range from inside its own boundary callback.
        // Otherwise the port stays paused at seg.time_end and the DOM 'pause'
        // event resets the play-button glyph.
        const active = get(playingSegmentIndex);
        if (get(autoPlayEnabled) && active?.origin === 'accordion') {
            const next = accordionStep(1);
            if (next && !isCurrentStop(next)) {
                // The chime (when on) inserts its own silent gap here, so the
                // beep sits between the two cards instead of over the next
                // card's opening. Chime off = a microtask, as before.
                _chimeSegmentEnd(() => {
                    const cur = get(playingSegmentIndex);
                    if (cur?.origin !== 'accordion') return; // superseded
                    playFromSegment(next.index, next.chapter, next.startMs, {
                        isAccordionPlay: true,
                        piece: { uid: next.uid, startMs: next.startMs, endMs: next.endMs },
                    });
                });
            }
            return;
        }
        // Main-list autoplay, bounded only because the chime is armed (see
        // `ensureBoundedRange`). The chapter audio would have run straight on,
        // so "advancing" is just resuming where we stopped — then re-point the
        // range at the segment we are now inside so the next boundary fires.
        if (get(autoPlayEnabled) && _chimeArmed() && active) {
            const resumeAt = segPort.currentTimeMs();
            _chimeSegmentEnd(() => {
                const nextSeg = _segAtOrAfter(resumeAt);
                if (!nextSeg) {
                    // Nothing left on this chapter — stay stopped rather than
                    // bleeding into the file's trailing audio.
                    setPlayingSegment(null);
                    segCurrentIdx.set(-1);
                    _segRange?.dispose();
                    _segRange = null;
                    return;
                }
                setPlayingSegment({ chapter: nextSeg.chapter ?? active.chapter, index: nextSeg.index });
                segCurrentIdx.set(nextSeg.index);
                _segRange?.setRange({ startMs: nextSeg.time_start, endMs: nextSeg.time_end });
                segPort.setPlaybackRate(get(playbackSpeed));
                segPort.seekAndPlay(Math.max(resumeAt, nextSeg.time_start));
                // The `stop` boundary halted the range's rAF loop. Re-arm it or
                // nothing ticks (frozen cursor) and no further boundary fires
                // (one chime, then the chapter runs on silently).
                _segRange?.attach();
            });
        }
        return;
    }
}

/** The displayed segment covering `timeMs` on the active chapter's audio, or
 *  the nearest one starting after it (a deleted-segment gap leaves the
 *  playhead covered by nothing). Null when the chapter has nothing left ahead. */
function _segAtOrAfter(timeMs: number): Segment | null {
    const displayed = get(displayedSegments);
    if (!displayed) return null;
    const activeUrl = _curChapterUrl();
    let ahead: Segment | null = null;
    for (const seg of displayed) {
        if (!audioSrcMatches(seg.audio_url, activeUrl)) continue;
        if (timeMs >= seg.time_start && timeMs < seg.time_end) return seg;
        if (seg.time_start >= timeMs && (!ahead || seg.time_start < ahead.time_start)) ahead = seg;
    }
    return ahead;
}

/**
 * Reconcile `_segRange` against the current playback state.
 *
 * Centralised so every mode transition that can flip the bounded-vs-
 * continuous invariant goes through one function:
 *   - autoPlayEnabled toggled MID-PLAY (subscribe below).
 *   - `onSegPlayClick` resume from a paused state — covers the case
 *     where exiting an edit mode left audio paused, then the user hit ▶.
 *   - `exitEditMode` tail — audio is unpaused via the edit preview
 *     (Apply / "preview kept playing") AND no main rAF or bounded
 *     range exists yet.
 *
 * Rules:
 *   - No active segment → drop any range; play (if running) is chapter-
 *     wide draw-only.
 *   - Accordion-origin play OR `!autoPlayEnabled` for a main-list play
 *     → bounded (stop policy at seg.time_end).
 *   - Main-list + autoplay-on → no range; chapter-continuous.
 *
 * Idempotent: same state on entry + exit is a no-op. Safe to over-call.
 */
export function ensureBoundedRange(): void {
    if (!segPort.element) return;
    if (_wordTimingRange) return;
    if (get(editMode)) return; // edit-preview owns the port

    const active = get(playingSegmentIndex);
    if (!active) {
        _segRange?.dispose();
        _segRange = null;
        return;
    }

    // The chime needs a real per-segment boundary. Chapter-continuous playback
    // has none — it infers segment changes from `timeupdate`, which a hidden
    // tab throttles to ~1/s, long enough to sail past several short segments
    // between ticks. So an armed chime opts main-list autoplay into the same
    // bounded range the accordion uses; `_onRangeBoundary` then chimes and
    // resumes. Chime off, this is exactly the old chapter-continuous path.
    const needBounded = active.origin === 'accordion' || !get(autoPlayEnabled) || _chimeArmed();

    if (needBounded && !_segRange) {
        // Wrap the currently-playing segment in a stop-policy range so
        // playback pauses at the upcoming boundary. `attach()` starts the
        // rAF without re-seeking, enforcing from the live playhead.
        const seg = getSegByChapterIndex(active.chapter, active.index);
        if (!seg) return;
        // Honour the group the current play spans (see `_activeGroupEndMs`):
        // rebuilding at `seg.time_end` re-bounds a group play to the piece the
        // pair names and stops the audio at its edge.
        const boundEnd = _activeGroupEndMs != null && _activeGroupEndMs > seg.time_end
            ? _activeGroupEndMs
            : seg.time_end;
        _drawLoop.stop(); // AudioRange owns the playhead rAF in bounded mode
        _segRange = new AudioRange({
            port: segPort,
            range: { startMs: segPort.currentTimeMs(), endMs: boundEnd },
            policy: { kind: 'stop' },
            onTick: _onRangeTick,
            onBoundary: _onRangeBoundary,
            playbackRate: () => get(playbackSpeed),
        });
        _segRange.attach();
        return;
    }

    if (!needBounded && _segRange) {
        // Switch to chapter-continuous: drop the bounded range so the
        // chapter audio plays through naturally; start the playhead-draw
        // rAF the chapter-continuous path needs (only while audio is
        // actually running).
        _segRange.dispose();
        _segRange = null;
        if (!segPort.paused) _drawLoop.start();
    }
}

// Mid-play autoplay toggles route through the same reconcile.
autoPlayEnabled.subscribe(() => ensureBoundedRange());
// Arming/disarming the chime flips main-list autoplay between bounded and
// chapter-continuous, so it reconciles through the same path.
segmentEndChimeEnabled.subscribe(() => ensureBoundedRange());

// ---------------------------------------------------------------------------
// Public play API
// ---------------------------------------------------------------------------

export function playFromSegment(
    segIndex: number,
    chapterOverride?: number | null,
    seekToMs?: number | null,
    opts?: {
        isAccordionPlay?: boolean,
        /** Rendered sibling list of the accordion card that initiated this
         *  play. When supplied (only by accordion rows), warmup warms the
         *  next sibling's clip URL by *list position* rather than chapter
         *  mode's `displayedSegments` + `index + 1` resolver. May span
         *  chapters; the per-reciter VBR map decides clip-vs-chapter URL
         *  per sibling. */
        accordionSiblings?: Segment[] | null,
        /** The PIECE to play, when the target is one slice of a segment (a
         *  staged cross-verse piece). Seeks to `startMs`, bounds the range at
         *  `endMs` — so a piece plays as its own segment instead of running on
         *  to the parent's end — and records the nav cursor under `uid` so ↑/↓
         *  and the autoplay advance step from this piece. */
        piece?: { uid: string; startMs: number; endMs: number } | null,
        /** End of the contiguous piece GROUP this row belongs to (a card's
         *  multi-piece split group). Extends the bounded range so the audio
         *  runs through the whole group — the pieces are contiguous slices of
         *  one window, so stopping at each piece edge cut the audio and made
         *  the advance start a fresh play of the next piece. Only ever
         *  extends: a value inside this segment is ignored. */
        groupEndMs?: number | null,
    },
): void {
    // Any new play supersedes a chime gap still waiting to resume the old one.
    // The accordion advance's own resume reaches here with the timer already
    // cleared, so this only ever drops a genuinely stale gap.
    cancelChimeGap();
    if (_wordTimingRange) {
        _wordTimingRange.dispose();
        _wordTimingRange = null;
        _wordTimingOwner = null;
    }
    const _playClickAt = performance.now();
    const _trace = (typeof localStorage !== 'undefined'
        && localStorage.getItem('insp_warmup_log') === 'true');
    if (_trace) {

        console.log(`[play] click seg=${segIndex} ch=${chapterOverride} readyState=${segPort.element?.readyState} reused=${segPort.window != null ? 'window-exists' : 'fresh'}`);
        const el = segPort.element;
        if (el) {
            const onPlaying = (): void => {

                console.log(`[play] FIRST audible frame ${Math.round(performance.now() - _playClickAt)}ms after click`);
                el.removeEventListener('playing', onPlaying);
            };
            el.addEventListener('playing', onPlaying);
        }
    }
    const allData = get(segAllData);
    if (!allData) return;
    if (!segPort.element) return;
    activeAudioSource.set('main');
    const _chStr = get(selectedChapter);
    const chapter = chapterOverride ?? (_chStr ? parseInt(_chStr) : null);
    const displayed = get(displayedSegments);
    const seg = chapter != null
        ? getSegByChapterIndex(chapter, segIndex)
        : (displayed ? displayed.find(s => s.index === segIndex) : null);
    if (!seg) return;
    // Resolve chapter from the segment itself when still unknown; playingSegment
    // must always carry a concrete chapter so SegmentRow's class:playing match
    // disambiguates same-index rows in other chapters.
    const resolvedChapter = chapter ?? seg.chapter ?? 0;
    const isAccordionPlay = opts?.isAccordionPlay ?? false;

    // Raise the buffering flag the instant a play is committed: the button
    // flips to "pause" and the playhead jumps to seg.time_start now, but the
    // clicked segment's audio bytes may not be buffered, so the first audible
    // frame is hundreds of ms away. Cleared on the `playing` event (see the
    // SegmentsFooter `onPlaying` sub) so the spinner spans exactly the
    // click→audible gap (issue #172).
    segAudioBuffering.set(true);

    // Bind the port to THIS seg's source. Cross-chapter accordion rows
    // (validation cards mounting rows from other chapters) and main-list
    // rows in the active chapter both flow through here. setSource is a
    // no-op for the active chapter; for cross-chapter rows it invalidates
    // `_window` so the next `loadCovering` issues a fresh swap against
    // the row's chapter URL.
    const segSource = resolveSegSource(seg, resolvedChapter);
    if (segSource) segPort.setSource(segSource);

    const piece = opts?.piece ?? null;
    const seekMs = seekToMs ?? piece?.startMs ?? seg.time_start;

    // Record the segment-level cursor for accordion nav. Playback initiation
    // is the ONLY writer — labelling a boundary never moves it.
    setAccordionNavCursor({
        uid: piece?.uid ?? seg.segment_uid ?? '',
        chapter: resolvedChapter,
        index: segIndex,
        startMs: piece?.startMs ?? seg.time_start,
        endMs: piece?.endMs ?? seg.time_end,
    });

    // Tear down any prior segment-bounded range. Playback regimes:
    //   - chapter mode + autoplay ON  → no AudioRange. Seek + play, chapter
    //                                    audio plays through naturally...
    //   - ...UNLESS the segment-end chime is armed, which needs a real
    //                                    boundary to fire on. Then bounded,
    //                                    and `_onRangeBoundary` chimes and
    //                                    resumes. MUST match the condition in
    //                                    `ensureBoundedRange`, which is what
    //                                    reconciles a mid-play toggle.
    //   - chapter mode + autoplay OFF → AudioRange with `stop` policy.
    //                                    Pauses at seg.time_end.
    //   - accordion play              → AudioRange with `stop` policy.
    //                                    Always bounded regardless of toggle.
    _segRange?.dispose();
    _segRange = null;

    // NOT bounded at the piece end. A multi-piece segment's pieces are
    // contiguous slices of one window, so cutting the range at a piece edge
    // stopped the audio early and the advance then started a FRESH play of the
    // next piece — audible as "piece 1 stops early and jumps to piece 2".
    // Play through the whole window instead and let the cursor walk from piece
    // to piece (the staged draw branch below republishes the nav cursor as it
    // crosses each edge, so ↑/↓ and the advance still step per segment).
    const groupEnd = opts?.groupEndMs ?? null;
    const endMs = groupEnd != null && groupEnd > seg.time_end ? groupEnd : seg.time_end;
    // Remember it: `ensureBoundedRange` rebuilds the range from the pair's own
    // segment, which after a split is ONE piece — re-bounding a group play to
    // piece 0's end pauses the audio there. Any toggle (autoplay, chime) or an
    // edit-mode exit can trigger that rebuild mid-play.
    _activeGroupEndMs = endMs > seg.time_end ? endMs : null;
    const bounded = isAccordionPlay || !get(autoPlayEnabled) || _chimeArmed();

    if (bounded) {
        _segRange = new AudioRange({
            port: segPort,
            range: { startMs: seekMs, endMs },
            policy: { kind: 'stop' },
            onTick: _onRangeTick,
            onBoundary: _onRangeBoundary,
            playbackRate: () => get(playbackSpeed),
        });
        _segRange.start();
    } else {
        // Chapter-continuous: load (or fast-path-reuse), then seek + play.
        // For CBR same-chapter the `loadCovering` short-circuits via fast-path 1
        // and `seekAndPlay` runs synchronously. For VBR or cross-chapter
        // accordion it awaits canplay before seeking.
        const { ready, swapped } = segPort.loadCovering(seg.time_start, seg.time_end);
        const startPlayback = (): void => {
            segPort.setPlaybackRate(get(playbackSpeed));
            segPort.seekAndPlay(seekMs);
        };
        if (!swapped) {
            startPlayback();
        } else {
            ready.then(() => {
                // Another play/source-swap may have superseded us — bail if the
                // active pair has changed targets in the meantime.
                const cur = get(playingSegmentIndex);
                if (!cur || cur.chapter !== resolvedChapter || cur.index !== segIndex) return;
                startPlayback();
            }).catch((e: unknown) => {
                if (e && (e as { name?: string }).name !== 'AbortError') console.error(e);
            });
        }
    }

    segCurrentIdx.set(segIndex);
    // Authoritative (chapter, index) for the active play — every downstream
    // reader (drawActivePlayhead, SegmentRow's class:playing) consults this
    // instead of inferring chapter from selectedChapter. `origin` lets the
    // main-list autoscroll (and any future follow-the-playhead UI) stay put
    // when the play came from an accordion-mounted row.
    setPlayingSegment({
        chapter: resolvedChapter,
        index: segIndex,
        origin: isAccordionPlay ? 'accordion' : 'main',
    });

    // Cross-chapter accordion play: update the picker's displayed chapter
    // PROGRAMMATICALLY (visual-only — does NOT touch `selectedChapter` or
    // trigger `loadChapterData`). Same-chapter accordion plays clear the
    // override so the picker reads `selectedChapter` normally. Main-list
    // plays never write here; the picker reflects `selectedChapter`.
    if (isAccordionPlay) {
        const cur = get(selectedChapter);
        const curNum = cur ? parseInt(cur) : NaN;
        pickerDisplayChapter.set(resolvedChapter !== curNum ? resolvedChapter : null);
    }

    // Accordion plays warm their card's next *sibling* (list position,
    // possibly cross-chapter); main-list plays warm the next displayed
    // segment by `Segment.index + 1`. Warmup no-ops on VBR + missing-kbps
    // (the segment-clip route handles VBR per-seg separately).
    const reciter = get(selectedReciter);
    const nextSeg = isAccordionPlay && opts?.accordionSiblings
        ? nextSiblingSeg(opts.accordionSiblings, resolvedChapter, segIndex)
        : nextDisplayedSeg(displayed, segIndex);
    warmSeg(nextSeg, reciter, seg);

    // Fetch waveform peaks on-demand via ffmpeg HTTP Range (brief delay expected).
    void _fetchPeaksForClick(seg, resolvedChapter);
}

/**
 * Universal play/pause entry point — the single source of truth for every
 * play/pause action in the Segments tab. Both the footer's ▶ button and
 * the spacebar shortcut route here so their behavior is identical.
 *
 * Dispatch:
 *   - In trim / split edit modes:
 *       * If a _playRange loop is alive (paused OR playing): toggle
 *         segPort.pause() / segPort.play() WITHOUT touching previewLooping
 *         or the canvas — the rAF is pause-resilient, so the cursor stays
 *         drawn and resume continues from the paused position.
 *       * Otherwise (no loop yet): cold-start the loop based on the
 *         active edit mode + split preview selection.
 *   - In normal mode:
 *       * If paused with no active segment: start at first displayed.
 *       * Otherwise: toggle segPort.pause() / segPort.play().
 */
export function onSegPlayClick(): void {
    if (!segPort.element) return;
    // A click during the chime's gap is the user taking over — drop the
    // pending resume so it can't restart audio a moment after they paused.
    cancelChimeGap();

    if (_wordTimingRange) {
        if (segPort.paused) {
            segPort.uncut();
            segPort.setPlaybackRate(get(playbackSpeed));
            segPort.play();
        } else segPort.pause();
        return;
    }

    const mode = get(editMode);
    if (mode === 'trim' || mode === 'split') {
        if (getPlayRangeRAF()) {
            // Loop alive — pure pause/resume. The rAF chain in _playRange is
            // pause-resilient (`if (segPort.paused) requestAnimationFrame(...)
            // return;`), so neither cursor nor loop state is lost.
            if (segPort.paused) {
                editPreviewPlaying.set(true);
                segPort.uncut();
                segPort.setPlaybackRate(get(playbackSpeed));
                segPort.play();
            } else {
                editPreviewPlaying.set(false);
                segPort.pause();
            }
            return;
        }
        // Cold-start.
        _coldStartEditPreview(mode);
        return;
    }

    // Normal mode.
    const displayed = get(displayedSegments);
    const curIdx = get(segCurrentIdx);
    if (segPort.paused) {
        if (displayed && displayed.length > 0 && curIdx < 0) {
            const first = displayed[0];
            if (first) playFromSegment(first.index, first.chapter);
        } else {
            // Resuming from pause. Reconcile `_segRange` against current
            // state BEFORE play — covers the case where exiting an edit
            // mode (Adjust / Split) tore down the prior bound and we now
            // need to rebuild it so accordion / autoplay-off plays don't
            // sail past their segment boundary on resume.
            ensureBoundedRange();
            segPort.setPlaybackRate(get(playbackSpeed));
            // Resume can also rebuffer (paused long enough for the forward
            // buffer to drain); show the spinner until the next audible frame.
            segAudioBuffering.set(true);
            segPort.play();
        }
    } else {
        segPort.pause();
    }
}

/** Cold-start an edit-preview loop based on the active edit mode and (for
 *  split) the user's range selection. Centralized here so onSegPlayClick
 *  routes through one path; the trim / split panels publish only selection
 *  state and never invoke `_playRange` directly. */
function _coldStartEditPreview(mode: 'trim' | 'split'): void {
    const canvas = get(editCanvas);
    if (!canvas) return;

    if (mode === 'trim') {
        const tw = canvas._trimWindow;
        if (!tw) return;
        editPreviewPlaying.set(true);
        setPreviewLooping('trim');
        _playRange(tw.currentStart, tw.currentEnd);
        return;
    }

    // Split: dispatch by current selection.
    const sd = canvas._splitData;
    if (!sd) return;
    const sel = get(splitPreviewSelection);
    const isBinary = sd.currentSplits.length === 1;
    editPreviewPlaying.set(true);

    if (isBinary) {
        const side: 'left' | 'right' = sel.kind === 'left'
            ? 'left'
            : sel.kind === 'right'
                ? 'right'
                : (sel.index === 0 ? 'left' : 'right');
        const splitTime = sd.currentSplits[0]!;
        setPreviewLooping(`split-${side}` as const);
        _playRange(
            side === 'left' ? sd.seg.time_start : splitTime,
            side === 'left' ? splitTime : sd.seg.time_end,
        );
        return;
    }

    // Multi-cursor: region selection.
    const n = sd.currentSplits.length;
    const idx = sel.kind === 'region'
        ? Math.max(0, Math.min(n, sel.index))
        : (sel.kind === 'left' ? 0 : n);
    const start = idx === 0 ? sd.seg.time_start : sd.currentSplits[idx - 1]!;
    const end = idx === n ? sd.seg.time_end : sd.currentSplits[idx]!;
    setPreviewLooping(`split-region-${idx}` as `split-region-${number}`);
    _playRange(start, end);
}

// ---------------------------------------------------------------------------
// Audio event handlers
// ---------------------------------------------------------------------------

export function onSegTimeUpdate(fileMs?: number): void {
    if (_wordTimingRange) return;
    // Edit-preview's rAF owns boundary enforcement on the edit canvas.
    if (get(editMode)) return;
    // VBR clip mode plays a one-segment clip from byte 0, so audioEl.src is
    // the clip URL, not the chapter audio URL. Cross-segment-within-shared-
    // audio detection (the body of this function) keys off matching the
    // chapter URL — useless in clip mode. Segment switches in VBR mode come
    // through explicit `playFromSegment` calls instead.
    if (segPort.window?.isClip) return;
    if (!segPort.element) return;
    // Cross-chapter accordion plays keep the port bound to the row's chapter
    // (via `playFromSegment`'s setSource), but `displayedSegments` and
    // `playingSegmentIndex` describe the ACTIVE chapter's view. The
    // file-absolute `timeMs` below would be interpreted in the ACCORDION
    // chapter's coordinate space, but compared against ACTIVE-chapter rows
    // whose `time_start/time_end` are in their own chapter's space —
    // false-positive matches happen when the numeric ms range overlaps
    // by coincidence. Skip the scan entirely whenever the port isn't
    // playing the active chapter; the playing pair set by `playFromSegment`
    // already points at the right (cross-chapter) row, and the rAF tick's
    // `drawActivePlayhead` keeps the cursor on that row's canvas.
    const portUrl = segPort.source?.audioUrl;
    const activeUrl = _curChapterUrl();
    if (!portUrl || !activeUrl || !audioSrcMatches(portUrl, activeUrl)) return;
    // `fileMs` comes from the port's `onTimeUpdate` subscription (file-
    // absolute). Fall back to a fresh read for direct callers (none today,
    // but the public export shape allows it).
    const timeMs = fileMs ?? segPort.currentTimeMs();
    const currentSrc = _curChapterUrl();
    const displayed = get(displayedSegments);
    const active = get(playingSegmentIndex);

    // Time → segment lookup. Under chapter-continuous playback the chapter
    // audio plays through; this scan migrates the active pair to whatever
    // segment currently contains the playhead. Also catches manual seek-bar
    // drags across multiple segment windows.
    const prevIdx = get(segCurrentIdx);
    let nextCurrentIdx = -1;
    let nextCurrentChapter = active?.chapter ?? null;
    if (displayed) {
        for (const seg of displayed) {
            if (timeMs >= seg.time_start && timeMs < seg.time_end) {
                if (currentSrc && !audioSrcMatches(seg.audio_url, currentSrc)) continue;
                nextCurrentIdx = seg.index;
                nextCurrentChapter = seg.chapter ?? nextCurrentChapter;
                break;
            }
        }
    }
    // Fallback: when the displayed-slice search missed (accordion playback
    // targeting a chapter other than the displayed one), hold the active pair
    // instead of clobbering it with -1.
    if (nextCurrentIdx === -1 && active) {
        const activeSeg = getSegByChapterIndex(active.chapter, active.index);
        if (activeSeg && audioSrcMatches(activeSeg.audio_url, currentSrc)
                && timeMs >= activeSeg.time_start && timeMs < activeSeg.time_end) {
            nextCurrentIdx = active.index;
            nextCurrentChapter = active.chapter;
        } else {
            // Past the active segment and the displayed slice can't say where
            // we are — scan the playing CHAPTER. Accordion playback runs
            // through a card's contiguous pieces, and those rows often aren't
            // in `displayedSegments`; without this the pair (and with it the
            // highlight and the waveform cursor) stuck on the piece that
            // started the play while the audio ran on into the next one.
            for (const s of getChapterSegments(active.chapter)) {
                if (timeMs >= s.time_start && timeMs < s.time_end) {
                    if (currentSrc && !audioSrcMatches(s.audio_url, currentSrc)) continue;
                    nextCurrentIdx = s.index;
                    nextCurrentChapter = active.chapter;
                    break;
                }
            }
        }
    }
    segCurrentIdx.set(nextCurrentIdx);

    if (nextCurrentIdx !== prevIdx && nextCurrentIdx >= 0 && nextCurrentChapter != null) {
        // Crossed into a new segment via chapter-continuous playback. Update
        // the active pair so the playhead and class:playing follow.
        // No chime here: with the chime armed this crossing is owned by the
        // bounded range in `_onRangeBoundary`, which fires at the exact
        // segment end rather than whenever `timeupdate` next happens to run.
        setPlayingSegment({ chapter: nextCurrentChapter, index: nextCurrentIdx });
        if (displayed) {
            const curSeg = displayed.find(s => s.index === nextCurrentIdx);
            // Continuous playback crossed into this segment without a new
            // play(), so move the nav cursor with it — otherwise ↑/↓ and the
            // autoplay advance keep stepping from the segment the user
            // originally pressed play on.
            if (curSeg) {
                setAccordionNavCursor({
                    uid: curSeg.segment_uid ?? '',
                    chapter: nextCurrentChapter,
                    index: nextCurrentIdx,
                    startMs: curSeg.time_start,
                    endMs: curSeg.time_end,
                });
            }
            const nextSeg = nextDisplayedSeg(displayed, nextCurrentIdx);
            warmSeg(nextSeg, get(selectedReciter), curSeg ?? null);
            if (curSeg) {
                const chapterForPeaks = curSeg.chapter ?? (get(selectedChapter) ? parseInt(get(selectedChapter)) : 0);
                if (chapterForPeaks) void _fetchPeaksForClick(curSeg, chapterForPeaks);
            }
        }
    }
}

export function startSegAnimation(): void {
    // UI state only — the rAF drives playhead drawing while audio is active.
    // The gate keeps the segment-row playhead off the edit canvas only when
    // the edit-preview rAF actually owns it: trim / split modes replace the
    // canvas. Reference edit leaves the row's normal waveform canvas in
    // place, so the chapter cursor must keep advancing through it.
    const _m = get(editMode);
    if (_m === 'trim' || _m === 'split') return;
    playButtonLabel.set('Pause');
    activeAudioSource.set('main');
    isMainAudioPlaying.set(true);
    // Skip the local rAF when a segment-bounded AudioRange is running — its
    // own onTick already calls drawActivePlayhead / updateSegHighlight, and
    // running both would double-draw the playhead and waste a frame budget.
    if (!_segRange && !_wordTimingRange) _drawLoop.start();
}

export function stopSegAnimation(): void {
    // Mirror the `editMode` gate from `startSegAnimation`. During edit-mode
    // preview, `_playRange`'s loop seek-back issues a transient
    // `pauseAndFlush()` to drain the OS audio sink — that fires a 'pause'
    // DOM event whose only intent is to silence the sink, not to surface
    // a paused state to the user. Without this gate the main play-button
    // label flickered to 'Play' on every loop iteration during Adjust /
    // Split preview.
    if (get(editMode)) return;
    // Same rationale for the segment-end chime's gap: the port is paused only
    // to open a silence for the beep and resumes ~200ms later on its own.
    // Surfacing that as a paused state flickers the play glyph at every
    // segment boundary. `cancelChimeGap` runs on any real stop, so a gap that
    // is still pending here is genuinely ours.
    if (_chimeGapActive) return;
    playButtonLabel.set('Play');
    if (get(activeAudioSource) === 'main') activeAudioSource.set(null);
    isMainAudioPlaying.set(false);
    // A pause cancels any in-flight startup buffer wait — drop the spinner.
    segAudioBuffering.set(false);
    _drawLoop.stop();
}

export function onSegAudioEnded(): void {
    // A word-review range may touch the actual end of its source file. The
    // browser can emit `ended` before the animation-frame boundary check.
    if (_wordTimingRange) {
        _wordTimingRange.start();
        return;
    }
    // Chapter audio file ended (user let it play through). Clear the active
    // pair, tear down any segment-bounded range, and stop the rAF.
    cancelChimeGap();
    setPlayingSegment(null);
    segAudioBuffering.set(false);
    _segRange?.dispose();
    _segRange = null;
    _drawLoop.stop();
}

// ---------------------------------------------------------------------------
// Highlight + playhead helpers
// ---------------------------------------------------------------------------

export function updateSegHighlight(): void {
    // setPlayingSegment() is identity-guarded — a same-value rAF tick is a
    // no-op for subscribers. The active pair (chapter, index) is set by
    // playFromSegment and maintained by onSegTimeUpdate for in-chapter
    // advance; this function bridges segCurrentIdx changes back to the pair
    // when they originate outside the time-update path (e.g. manual seek).
    const curIdx = get(segCurrentIdx);
    const active = get(playingSegmentIndex);
    if (curIdx < 0) return;
    if (!active || active.index !== curIdx) {
        const chapter = active?.chapter
            ?? (get(displayedSegments).find((s) => s.index === curIdx)?.chapter)
            ?? (get(selectedChapter) ? parseInt(get(selectedChapter)) : null);
        if (chapter != null) setPlayingSegment({ chapter, index: curIdx });
    }
}

/**
 * Reconcile the `playingSegmentIndex` pair after a structural mutation
 * (split/merge/delete) has re-indexed a chapter's segments.
 *
 * Callers capture `seg.segment_uid` BEFORE the mutation and pass it in.
 * Because split/merge preserve UIDs on the firstHalf / kept side, the playing
 * segment usually still exists under the same UID with a new index — we look
 * it up and update the active pair. If the playing seg was removed (delete,
 * or merge consumed it), we clear the pair and stop the rAF so the UI
 * doesn't keep drawing a playhead on a stale (chapter, index) pointer.
 */
export function reconcilePlayingAfterMutation(
    chapter: number,
    prePlayingUid: string | null,
): void {
    const active = get(playingSegmentIndex);
    if (!active || active.chapter !== chapter || !prePlayingUid) return;
    const allData = get(segAllData);
    if (!allData?.segments) return;
    const found = allData.segments.find(
        (s) => s.segment_uid === prePlayingUid && s.chapter === chapter,
    );
    if (found) {
        setPlayingSegment({ chapter, index: found.index });
    } else {
        setPlayingSegment(null);
        _drawLoop.stop();
    }
}

/**
 * Re-anchor the playing pair onto the piece the user is actually on, after a
 * split replaced one segment with several.
 *
 * `reconcilePlayingAfterMutation` maps the pre-mutation UID forward, and a
 * split preserves that UID on **piece 0** — so when the user is on a LATER
 * piece the pair lands on piece 0. Everything keyed off the pair then points
 * at the wrong row: the draw loop clamps the cursor to piece 0's window (it
 * sits frozen at that row's edge while its sibling plays) and the row
 * highlight jumps back to the first piece.
 *
 * Touches **nothing but the pair**. In particular it must never go near the
 * port or the live `AudioRange`: `dispose()` pauses and `start()` re-seeks, so
 * re-pointing a range mid-play is heard as a pause-and-jump-back — and there
 * is nothing to re-point anyway, since a split leaves each piece's window
 * exactly where the staged slice already was, so the existing range is still
 * correct.
 *
 * Target piece, in order:
 *  1. `accordionNavCursor` — the piece the user is on. A split preserves the
 *     staged children's UIDs, so this matches even though the rows re-render.
 *  2. The piece containing the playhead.
 *  3. The last piece starting at or before the playhead — the playhead parks
 *     exactly on a piece's end when a bounded play finishes, which belongs to
 *     the piece that just played, not to piece 0.
 */
export function reanchorPlayingAfterSplit(chapter: number, pieces: Segment[]): void {
    const active = get(playingSegmentIndex);
    if (!active || active.chapter !== chapter) return;
    if (pieces.length < 2) return;

    const target = _pieceForNavCursor(pieces) ?? _pieceAtPlayhead(pieces);
    if (!target || target.index === active.index) return;

    setPlayingSegment({ chapter, index: target.index });
    // `segCurrentIdx` MUST move in lockstep — `updateSegHighlight` forces
    // `playingSegmentIndex` back onto it on every rAF frame, so leaving it on
    // the pre-split index snaps the pair straight back to piece 0 and the
    // highlight + cursor sit on the first piece (the same trap the
    // deleted-gap jump documents above).
    segCurrentIdx.set(target.index);
    // The pieces are real rows now, so the staged-slice signal is stale; the
    // real row lights up from the pair alone.
    setStagedPlayheadWindow(null);
}

/** The piece the accordion nav cursor names, by uid then by window. */
function _pieceForNavCursor(pieces: Segment[]): Segment | null {
    const cursor = get(accordionNavCursor);
    if (!cursor) return null;
    if (cursor.uid) {
        const byUid = pieces.find((p) => p.segment_uid === cursor.uid);
        if (byUid) return byUid;
    }
    return pieces.find((p) => p.time_start === cursor.startMs) ?? null;
}

/** The piece the playhead is inside, else the last one it has reached. */
function _pieceAtPlayhead(pieces: Segment[]): Segment | null {
    const time = segPort.currentTimeMs();
    const inside = pieces.find((p) => time >= p.time_start && time < p.time_end);
    if (inside) return inside;
    let reached: Segment | null = null;
    for (const p of pieces) {
        if (time >= p.time_start) reached = p;
    }
    return reached;
}

export function drawActivePlayhead(timeMs?: number): void {
    // Hoist above the pair-change erase branch (below): when the edit-preview
    // rAF owns the row's canvas, the erase branch iterates
    // `getRowEntriesFor(_prevPlaying)` — which includes the edit canvas when
    // adjusting the previously-active segment — and clobbers trim handles
    // with plain peaks via `drawWaveformFromPeaksForSeg`. Only trim / split
    // replace the canvas; reference edit leaves it intact, so the chapter
    // cursor must keep drawing through the ref-edit lifetime.
    const _m = get(editMode);
    if (_m === 'trim' || _m === 'split') return;
    const allData = get(segAllData);
    const active = get(playingSegmentIndex);
    if (!allData) return;
    if (!segPort.element) return;
    // `timeMs` is the file-absolute time supplied by the rAF tick. For direct
    // callers (manual seek handler) fall back to the live port reading — the
    // port owns offset translation so file-absolute is identical regardless
    // of CBR vs VBR transport.
    const time = timeMs ?? segPort.currentTimeMs();

    const prev = _prevPlaying;
    const pairChanged = !prev || !active
        || prev.chapter !== active.chapter
        || prev.index !== active.index;

    // On pair change: erase the previous playhead by redrawing the waveform on
    // EVERY mounted twin for the old (chapter, index). Both the main-list row
    // and any accordion row showing the same segment must be cleaned up.
    if (prev && pairChanged) {
        const prevSeg = getSegByChapterIndex(prev.chapter, prev.index);
        for (const entry of getRowEntriesFor(prev.chapter, prev.index)) {
            // Staged pieces erase against their OWN window — the store segment
            // is their undivided parent.
            const rowSeg = entry.segOverride ?? prevSeg;
            if (!entry.canvas || !rowSeg) continue;
            drawWaveformFromPeaksForSeg(entry.canvas, rowSeg, prev.chapter);
            _stagedCursorCanvases.delete(entry.canvas);
        }
    }

    _prevPlaying = active ? { chapter: active.chapter, index: active.index } : null;

    if (!active) {
        setStagedPlayheadWindow(null);
        setActiveWordCursor(null);
        return;
    }

    const seg = getSegByChapterIndex(active.chapter, active.index);
    if (!seg) {
        setActiveWordCursor(null);
        return;
    }
    const audioUrl = seg.audio_url || allData?.audio_by_chapter?.[String(active.chapter)] || '';

    // Compensate the visual playhead for platform output latency: `time` is the
    // media/decode clock, which leads the audible recitation by the OS+Web-Audio
    // output latency. Subtract it so the playhead tracks what the user HEARS.
    // Clamp into the segment window so it pins at the left edge during the
    // initial latency window instead of vanishing (drawSegPlayhead skips
    // out-of-range times). Display-only — control paths keep the raw clock.
    const displayT = Math.min(seg.time_end, Math.max(seg.time_start, displayTimeMs(time)));
    const activeWordIndex = wordIndexAt(displayT, seg.word_timings);
    setActiveWordCursor(activeWordIndex >= 0
        ? { chapter: active.chapter, index: active.index, wordIndex: activeWordIndex }
        : null);

    // Draw the playhead on EVERY mounted twin for this (chapter, index) — main
    // list row and any accordion rows showing the same segment. Both need the
    // synchronized playhead per spec.
    //
    // Staged pre-split pieces are twins too, but each renders a SLICE of the
    // parent's window and they all share the parent's (chapter, index). Draw
    // them against their own window, and only while the playhead is inside it
    // — so the cursor travels through the piece that is actually sounding and
    // the pieces on either side stay clean. Leaving a piece erases its cursor
    // once (tracked below) rather than repainting peaks every frame. The
    // piece that holds the cursor is published as `stagedPlayheadWindow` so
    // its row alone carries the playing state.
    let stagedWindow: { start: number; end: number } | null = null;
    let stagedCursorSeg: Segment | null = null;
    for (const entry of getRowEntriesFor(active.chapter, active.index)) {
        if (!entry.canvas) continue;
        const rowSeg = entry.segOverride ?? seg;
        if (entry.segOverride) {
            const inWindow = displayT >= rowSeg.time_start && displayT < rowSeg.time_end;
            if (!inWindow) {
                if (_stagedCursorCanvases.delete(entry.canvas)) {
                    drawWaveformFromPeaksForSeg(entry.canvas, rowSeg, active.chapter);
                }
                continue;
            }
            _stagedCursorCanvases.add(entry.canvas);
            stagedWindow = { start: rowSeg.time_start, end: rowSeg.time_end };
            // Continuous playback walks the cursor from piece to piece without
            // a new play() per piece, so the nav cursor has to follow it here
            // or ↑/↓ and the autoplay advance would keep stepping from the
            // piece the user originally pressed play on.
            stagedCursorSeg = rowSeg;
        }
        const rowT = Math.min(rowSeg.time_end, Math.max(rowSeg.time_start, displayT));
        drawSegPlayhead(entry.canvas, rowSeg.time_start, rowSeg.time_end, rowT, audioUrl);
    }
    setStagedPlayheadWindow(stagedWindow);
    if (stagedCursorSeg && active) {
        setAccordionNavCursor({
            uid: stagedCursorSeg.segment_uid ?? '',
            chapter: active.chapter,
            index: active.index,
            startMs: stagedCursorSeg.time_start,
            endMs: stagedCursorSeg.time_end,
        });
    }
}
