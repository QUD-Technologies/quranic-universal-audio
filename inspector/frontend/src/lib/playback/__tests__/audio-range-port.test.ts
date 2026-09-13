/**
 * AudioRange — port-mode tests.
 *
 * Mirrors the 8 boundary / policy / dispose cases from `audio-range.test.ts`
 * but runs them against an `AudioRange` constructed with a real `AudioPort`
 * instance. Parameterized over CBR (offset = 0) and VBR (offset = 5000) so
 * the whole battery passes identically under both transports — that's the
 * abstraction proof: AudioRange can't tell which transport is underneath.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { cutAudio, uncutAudio } from '../audio-graph';
import { AudioPort } from '../audio-port';
import { AudioRange, type AudioRangeSpec, type RangePolicy } from '../audio-range';
import { type AudioStub, installRafMock, makeAudioStub, type RafMock } from './raf-harness';

vi.mock('../audio-graph', () => ({
    cutAudio: vi.fn(),
    uncutAudio: vi.fn(),
    getAudioGraph: vi.fn(() => null),
    _getCtx: vi.fn(() => null),
}));

interface Variant {
    name: string;
    /** ms added to file-absolute time to get clip-relative `audio.currentTime`.
     *  CBR variant uses 0; VBR variant uses 5000 (clip starts at segment 5s). */
    offset: number;
    /** CBR can seek within a loaded chapter. VBR must reload when the desired
     *  playback start moves inside the existing clip, so play begins at clip 0. */
    reusesSubranges: boolean;
    setup: (port: AudioPort) => Promise<void>;
}

const VARIANTS: Variant[] = [
    {
        name: 'CBR',
        offset: 0,
        reusesSubranges: true,
        async setup(port) {
            port.setSource({ audioUrl: 'http://x/seg.mp3', reciter: null, vbr: false });
            const r = port.loadCovering(0, 10_000);
            (port.element as unknown as AudioStub)._fireEvent('canplay');
            await r.ready;
        },
    },
    {
        name: 'VBR (offset 5000)',
        offset: 5000,
        reusesSubranges: false,
        async setup(port) {
            port.setSource({ audioUrl: 'http://x/seg.mp3', reciter: 'r1', vbr: true });
            const r = port.loadCovering(5000, 15_000);
            (port.element as unknown as AudioStub)._fireEvent('canplay');
            await r.ready;
            // After load: audio.currentTime is clip-relative (0..clipDuration);
            // port.currentTimeMs returns clip*1000 + 5000.
        },
    },
];

describe.each(VARIANTS)('AudioRange port mode — $name', ({ offset, reusesSubranges, setup }) => {
    let raf: RafMock;
    let audio: AudioStub;
    let port: AudioPort;

    beforeEach(async () => {
        raf = installRafMock();
        audio = makeAudioStub({ src: '', readyState: 4 });
        port = new AudioPort();
        port.attachElement(audio as unknown as HTMLAudioElement);
        await setup(port);
        vi.mocked(cutAudio).mockClear();
        vi.mocked(uncutAudio).mockClear();
    });

    afterEach(() => {
        raf.restore();
        port.dispose();
        vi.useRealTimers();
    });

    /** Build a port-mode range with file-absolute startMs/endMs (offset added). */
    function buildRange(opts: {
        range?: { startMs: number; endMs: number };
        policy?: RangePolicy;
        onTick?: (ms: number) => void;
        onBoundary?: (ev: { reason: string; nextRange?: AudioRangeSpec }) => void;
    }) {
        const r = opts.range ?? { startMs: 0, endMs: 1000 };
        return new AudioRange({
            port,
            range: { startMs: offset + r.startMs, endMs: offset + r.endMs },
            policy: opts.policy ?? { kind: 'stop' },
            onTick: opts.onTick,
            onBoundary: opts.onBoundary,
        });
    }

    /** Set the audio element's clip-relative currentTime to `clipMs` so
     *  `port.currentTimeMs()` returns `clipMs + offset`. */
    function setClipTimeMs(clipMs: number) {
        audio.currentTime = clipMs / 1000;
    }

    // -----------------------------------------------------------------------
    // 1. Boundary precision
    // -----------------------------------------------------------------------

    describe('boundary precision', () => {
        it('does not fire while currentTime < endMs', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ onBoundary });
            r.start();
            setClipTimeMs(999);
            raf.flushFrames(1);
            expect(onBoundary).not.toHaveBeenCalled();
        });

        it('fires once on the first frame after currentTime >= endMs', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ onBoundary });
            r.start();
            setClipTimeMs(999);
            raf.flushFrames(1);
            setClipTimeMs(1001);
            raf.flushFrames(1);
            expect(onBoundary).toHaveBeenCalledTimes(1);
            expect(onBoundary.mock.calls[0]?.[0]).toMatchObject({ reason: 'stop' });
        });
    });

    // -----------------------------------------------------------------------
    // 2. Stop policy
    // -----------------------------------------------------------------------

    describe('stop policy', () => {
        it('pauses + cuts + clips currentTime to endMs (file-absolute)', () => {
            const r = buildRange({ policy: { kind: 'stop' } });
            r.start();
            setClipTimeMs(1050);
            raf.flushFrames(1);
            expect(audio.pause).toHaveBeenCalled();
            // Port-mode pin: port.seek(range.endMs) writes clip-relative time.
            expect(audio.currentTime).toBeCloseTo(1.0, 5);
            expect(vi.mocked(cutAudio)).toHaveBeenCalled();
            expect(r.isRunning()).toBe(false);
        });
    });

    // -----------------------------------------------------------------------
    // 2b. timeupdate backstop (hidden tab / backgrounded window)
    // -----------------------------------------------------------------------

    describe('timeupdate backstop', () => {
        // A hidden tab gets ZERO rAF callbacks while the media element keeps
        // decoding, so every case below deliberately never flushes a frame.
        it('enforces a stop boundary with rAF starved', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ policy: { kind: 'stop' }, onBoundary });
            r.start();
            setClipTimeMs(1500);
            audio._fireEvent('timeupdate');
            expect(onBoundary).toHaveBeenCalledTimes(1);
            expect(onBoundary.mock.calls[0]?.[0]).toMatchObject({ reason: 'stop' });
            expect(audio.pause).toHaveBeenCalled();
            expect(r.isRunning()).toBe(false);
        });

        it('enforces a loop boundary with rAF starved', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ policy: { kind: 'loop' }, onBoundary });
            r.start();
            setClipTimeMs(1500);
            audio._fireEvent('timeupdate');
            expect(onBoundary).toHaveBeenCalledTimes(1);
            expect(onBoundary.mock.calls[0]?.[0]).toMatchObject({ reason: 'loop' });
            expect(audio.currentTime).toBeCloseTo(0, 5);
            expect(r.isRunning()).toBe(true);
        });

        it('stays quiet below the boundary', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ onBoundary });
            r.start();
            setClipTimeMs(500);
            audio._fireEvent('timeupdate');
            expect(onBoundary).not.toHaveBeenCalled();
        });

        it('does not fire the boundary twice when rAF resumes', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ policy: { kind: 'stop' }, onBoundary });
            r.start();
            setClipTimeMs(1500);
            audio._fireEvent('timeupdate');
            raf.flushFrames(2);
            expect(onBoundary).toHaveBeenCalledTimes(1);
        });

        it('ignores timeupdates once disposed', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ onBoundary });
            r.start();
            r.dispose();
            setClipTimeMs(1500);
            audio._fireEvent('timeupdate');
            expect(onBoundary).not.toHaveBeenCalled();
        });
    });

    // -----------------------------------------------------------------------
    // 3. Loop policy
    // -----------------------------------------------------------------------

    describe('loop policy', () => {
        it('seeks back to startMs and reports loop boundary', () => {
            const onBoundary = vi.fn();
            const r = buildRange({
                range: { startMs: 0, endMs: 1000 },
                policy: { kind: 'loop' },
                onBoundary,
            });
            r.start();
            setClipTimeMs(1001);
            raf.flushFrames(1);

            expect(audio.pause).not.toHaveBeenCalled();
            // Port-mode loop seek: port.seek(range.startMs) → clip-relative.
            expect(audio.currentTime).toBeCloseTo(0, 5);
            expect(onBoundary).toHaveBeenCalledTimes(1);
            expect(onBoundary.mock.calls[0]?.[0]).toMatchObject({ reason: 'loop' });
            expect(r.isRunning()).toBe(true);
        });

        it('does not double-fire on the same crossing (seeked-this-frame guard)', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ policy: { kind: 'loop' }, onBoundary });
            r.start();
            setClipTimeMs(1001);
            raf.flushFrames(1);
            expect(onBoundary).toHaveBeenCalledTimes(1);

            // Decoder lag — currentTime hasn't dropped yet.
            setClipTimeMs(1001);
            raf.flushFrames(1);
            expect(onBoundary).toHaveBeenCalledTimes(1);

            setClipTimeMs(500);
            raf.flushFrames(1);
            setClipTimeMs(1001);
            raf.flushFrames(1);
            expect(onBoundary).toHaveBeenCalledTimes(2);
        });
    });

    // -----------------------------------------------------------------------
    // 4. Advance policy
    // -----------------------------------------------------------------------

    describe('advance policy', () => {
        it('pauses, gaps, then resumes at nextRange.startMs', async () => {
            vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
            const next: AudioRangeSpec = { startMs: offset + 2000, endMs: offset + 3000 };
            const onBoundary = vi.fn();
            const r = buildRange({
                policy: { kind: 'advance', gapMs: 200, nextRange: () => next },
                onBoundary,
            });
            r.start();
            setClipTimeMs(1001);
            raf.flushFrames(1);

            expect(audio.pause).toHaveBeenCalledTimes(1);
            expect(onBoundary).toHaveBeenCalledTimes(1);
            expect(onBoundary.mock.calls[0]?.[0]).toMatchObject({
                reason: 'advance',
                nextRange: next,
            });
            const playBefore = audio.play.mock.calls.length;

            vi.advanceTimersByTime(200);

            if (!reusesSubranges) {
                // VBR reloads to a clip whose byte 0 is nextRange.startMs.
                audio._fireEvent('canplay');
                await Promise.resolve();
            }

            // After gap: CBR seeks inside the already-loaded chapter; VBR
            // swaps to a new clip and starts from clip time 0.
            expect(audio.currentTime).toBeCloseTo(reusesSubranges ? 2.0 : 0, 5);
            expect(audio.play.mock.calls.length).toBeGreaterThan(playBefore);
        });

        it('falls back to stop when nextRange returns null', () => {
            vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
            const r = buildRange({
                policy: { kind: 'advance', gapMs: 100, nextRange: () => null },
            });
            r.start();
            setClipTimeMs(1001);
            raf.flushFrames(1);
            vi.advanceTimersByTime(100);
            expect(audio.paused).toBe(true);
            expect(r.isRunning()).toBe(false);
        });

        it('cancels pending gap timer on stop()', () => {
            vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
            const next: AudioRangeSpec = { startMs: offset + 2000, endMs: offset + 3000 };
            const r = buildRange({
                policy: { kind: 'advance', gapMs: 200, nextRange: () => next },
            });
            r.start();
            setClipTimeMs(1001);
            raf.flushFrames(1);
            r.stop();
            const playBefore = audio.play.mock.calls.length;
            vi.advanceTimersByTime(500);
            expect(audio.play.mock.calls.length).toBe(playBefore);
            expect(r.isRunning()).toBe(false);
        });
    });

    // -----------------------------------------------------------------------
    // 5. setRange dynamic
    // -----------------------------------------------------------------------

    describe('setRange', () => {
        it('applies new bounds on the next frame', () => {
            const onBoundary = vi.fn();
            const r = buildRange({ onBoundary });
            r.start();
            setClipTimeMs(600);
            raf.flushFrames(1);
            expect(onBoundary).not.toHaveBeenCalled();

            r.setRange({ startMs: offset + 0, endMs: offset + 500 });
            raf.flushFrames(1);
            expect(onBoundary).toHaveBeenCalledTimes(1);
        });

        it('lifts a pending kill-switch ramp from a prior pauseAndFlush', () => {
            const r = buildRange({ policy: { kind: 'stop' } });
            r.start();
            setClipTimeMs(1001);
            raf.flushFrames(1);
            expect(vi.mocked(cutAudio)).toHaveBeenCalledTimes(1);

            vi.mocked(uncutAudio).mockClear();
            r.setRange({ startMs: offset + 0, endMs: offset + 2000 });
            expect(vi.mocked(uncutAudio)).toHaveBeenCalledTimes(1);
            expect(vi.mocked(uncutAudio)).toHaveBeenCalledWith(audio);
        });
    });

    // -----------------------------------------------------------------------
    // 6. Pause-resilient loop
    // -----------------------------------------------------------------------

    describe('pause-resilient loop', () => {
        it('keeps rAF alive while paused; resumes onTick on unpause', () => {
            const onTick = vi.fn();
            const r = buildRange({ onTick });
            r.start();

            setClipTimeMs(100);
            raf.flushFrames(1);
            const before = onTick.mock.calls.length;
            expect(before).toBeGreaterThan(0);

            audio.paused = true;
            raf.flushFrames(3);
            expect(onTick.mock.calls.length).toBe(before);
            expect(r.isRunning()).toBe(true);
            expect(raf.queueLength()).toBeGreaterThan(0);

            audio.paused = false;
            setClipTimeMs(200);
            raf.flushFrames(1);
            expect(onTick.mock.calls.length).toBe(before + 1);
        });
    });

    // -----------------------------------------------------------------------
    // 7. attach (no seek, no play)
    // -----------------------------------------------------------------------

    describe('attach', () => {
        it('starts rAF without seeking or calling play', () => {
            audio.currentTime = 4.2;
            audio.paused = false;
            const onTick = vi.fn();
            const r = buildRange({ range: { startMs: 1000, endMs: 5000 }, onTick });
            r.attach();

            expect(audio.play).not.toHaveBeenCalled();
            expect(audio.currentTime).toBeCloseTo(4.2, 5);

            raf.flushFrames(1);
            expect(onTick).toHaveBeenCalledTimes(1);
            // Tick payload is file-absolute.
            expect(onTick.mock.calls[0]?.[0]).toBeCloseTo(offset + 4200, 5);
        });
    });

    // -----------------------------------------------------------------------
    // 8. dispose
    // -----------------------------------------------------------------------

    describe('dispose', () => {
        it('stops rAF + uncuts the audio graph', () => {
            const r = buildRange({
                policy: { kind: 'advance', gapMs: 500, nextRange: () => ({ startMs: 0, endMs: 100 }) },
            });
            r.start();
            r.dispose();
            expect(r.isRunning()).toBe(false);
            expect(vi.mocked(uncutAudio)).toHaveBeenCalled();
            // Gap-timer cancellation on stop/dispose is asserted by the
            // dedicated stop-cancels-gap test; this test never crosses the
            // boundary so no advance timer exists to cancel here.
        });
    });

    // -----------------------------------------------------------------------
    // 9. start() with src already covered (sync path)
    // -----------------------------------------------------------------------

    describe('start - already covered', () => {
        it('plays immediately without canplay wait when the transport can reuse the loaded window', async () => {
            const r = buildRange({ range: { startMs: 100, endMs: 1000 } });
            r.start();
            if (reusesSubranges) {
                // CBR pre-load covered [0..10000] file-absolute, so this takes
                // the no-swap fast path: seek + play sync.
                expect(audio.play).toHaveBeenCalledTimes(1);
                expect(audio.currentTime).toBeCloseTo(0.1, 5);
            } else {
                // VBR must not seek 100ms into the old clip. It waits for a
                // fresh clip starting at the requested file-absolute time.
                expect(audio.play).toHaveBeenCalledTimes(0);
                audio._fireEvent('canplay');
                await Promise.resolve();
                expect(audio.play).toHaveBeenCalledTimes(1);
                expect(audio.currentTime).toBeCloseTo(0, 5);
            }
        });
    });
});
