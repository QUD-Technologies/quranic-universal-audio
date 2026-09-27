/**
 * The playhead position the listener is HEARING, for every visual decision
 * about which segment / piece is sounding.
 *
 * `el.currentTime` leads the speakers by the platform output latency, so the
 * waveform cursor draws at `displayTimeMs(raw)`. The row highlight and the
 * piece the cursor sits in must follow that same clock: switching them on the
 * raw clock moves the highlight into the next piece while the previous one is
 * still audible, with the cursor pinned at the new piece's left edge.
 *
 * `floorMs` is where the current play started. Subtracting the latency from a
 * fresh seek would otherwise land in the segment BEFORE the one just played;
 * the floor holds the position at the play start until the audio catches up.
 * It only applies while the raw clock is at or past it, so a seek backwards
 * inside the play is not pinned.
 *
 * Visual only — boundary enforcement and seeks stay on the raw clock.
 */

import { displayTimeMs } from '../../../../lib/playback/audio-graph';

export function heardTimeMs(rawMs: number, floorMs: number | null): number {
    const heard = displayTimeMs(rawMs);
    if (floorMs == null || rawMs < floorMs) return heard;
    return Math.max(floorMs, heard);
}
