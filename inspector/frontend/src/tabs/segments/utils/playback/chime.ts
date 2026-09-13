/**
 * Segment-end chime — a short two-tone beep marking the boundary between one
 * segment and the next during autoplay.
 *
 * Synthesized on the shared `AudioContext` rather than shipped as an asset:
 * an oscillator is a handful of lines, needs no network fetch, and sidesteps
 * the LFS-pointer trap that bites binaries under `public/` in an HF Space
 * build. The context is the same one the kill-switch graph uses, so it is
 * already unlocked by the user gesture that started playback.
 *
 * Independent of the media element — the chime plays over (or between)
 * whatever the element is doing and never touches its graph.
 */

import { _getCtx } from '../../../../lib/playback/audio-graph';

/** Peak gain of the chime. Deliberately quiet: it is a boundary marker
 *  alongside recitation, not an alert. */
const CHIME_PEAK_GAIN = 0.16;
/** Per-tone length. Two of these play back to back. */
const CHIME_TONE_SEC = 0.075;
/** Attack/release on each tone — without a ramp an abrupt gain step clicks. */
const CHIME_RAMP_SEC = 0.012;
/** Silent tail held after the second tone. The chime node stays alive (at zero
 *  gain) for this long, so the gap a caller opens around the chime is owned by
 *  the Web Audio clock rather than by a `setTimeout` — which browsers clamp to
 *  >=1s in a hidden tab, stretching the gap exactly when the user is relying on
 *  the beep to follow along without looking. */
const CHIME_REST_SEC = 0.13;
/** Wall-clock length of the chime plus its silent tail — how long playback is
 *  held. Callers use it only for the no-Web-Audio fallback path; when the
 *  oscillator exists it reports its own completion instead. */
export const CHIME_TOTAL_MS = (CHIME_TONE_SEC * 2 + CHIME_REST_SEC) * 1000;
/** Rising pair, in Hz. Well clear of the recitation's fundamental so it
 *  reads as a marker rather than a note in the recitation. */
const CHIME_TONES_HZ = [880, 1320] as const;
/** Floor between chimes. Guards against a boundary that resolves twice
 *  (e.g. a rAF frame and the timeupdate backstop racing) double-beeping. */
const CHIME_MIN_INTERVAL_MS = 250;

let _lastChimeAt = 0;

/**
 * Play the segment-end chime.
 *
 * `onDone` fires when the chime AND its silent tail have finished, off the Web
 * Audio clock — callers resume playback there so the gap stays tight in a
 * hidden tab, where `setTimeout` would be clamped to a second or more.
 *
 * Returns false when nothing was scheduled (no Web Audio, or a chime already
 * played within `CHIME_MIN_INTERVAL_MS`); `onDone` is NOT called in that case,
 * so a caller holding playback must fall back to its own timer.
 *
 * Callers gate on the user's toggle — this function does not read it, so it
 * stays usable for a settings preview.
 */
export function playSegmentEndChime(onDone?: () => void): boolean {
    const ctx = _getCtx();
    if (!ctx) return false;

    const now = Date.now();
    if (now - _lastChimeAt < CHIME_MIN_INTERVAL_MS) return false;
    _lastChimeAt = now;

    // A suspended context yields silence rather than throwing; resume is
    // fire-and-forget because the scheduled tones start far enough ahead
    // (one tone length) to survive the resume round-trip.
    if (ctx.state === 'suspended') void ctx.resume();

    try {
        const start = ctx.currentTime;
        const last = CHIME_TONES_HZ.length - 1;
        CHIME_TONES_HZ.forEach((hz, i) => {
            const t0 = start + i * CHIME_TONE_SEC;
            const t1 = t0 + CHIME_TONE_SEC;
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(hz, t0);
            gain.gain.setValueAtTime(0, t0);
            gain.gain.linearRampToValueAtTime(CHIME_PEAK_GAIN, t0 + CHIME_RAMP_SEC);
            gain.gain.setValueAtTime(CHIME_PEAK_GAIN, t1 - CHIME_RAMP_SEC);
            gain.gain.linearRampToValueAtTime(0, t1);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(t0);
            // The final tone runs on silently through the rest, so its `ended`
            // marks the end of the whole gap, not just the audible part.
            osc.stop(i === last ? t1 + CHIME_REST_SEC : t1);
            // Oscillators are one-shot; drop the graph edge once done so the
            // nodes are collectable instead of piling up across a long session.
            osc.onended = () => {
                osc.disconnect();
                gain.disconnect();
                if (i === last) onDone?.();
            };
        });
        return true;
    } catch {
        // A context torn down mid-call (tab teardown) throws on createOscillator.
        // The chime is cosmetic — never let it break the advance it accompanies.
        return false;
    }
}

/** Test seam: forget the last-chime timestamp so the rate limit doesn't
 *  leak across cases. */
export function _resetChimeThrottle(): void {
    _lastChimeAt = 0;
}
