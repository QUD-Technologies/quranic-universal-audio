/**
 * Segment-end chime — synthesis + throttle.
 *
 * The chime is cosmetic, so the contract under test is mostly "never throws
 * and never spams": it must no-op cleanly when Web Audio is missing (happy-dom
 * ships no AudioContext) and must collapse a double-fired boundary into one
 * beep.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { _resetChimeThrottle, playSegmentEndChime } from '../chime';

interface FakeNode { connect: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }

function installFakeAudioContext(state: AudioContextState = 'running') {
    const oscillators: FakeNode[] = [];
    const resume = vi.fn(() => Promise.resolve());
    const ctx = {
        state,
        currentTime: 0,
        resume,
        destination: {} as AudioNode,
        createOscillator: vi.fn(() => {
            const osc = {
                type: 'sine',
                frequency: { setValueAtTime: vi.fn() },
                connect: vi.fn(),
                disconnect: vi.fn(),
                start: vi.fn(),
                stop: vi.fn(),
                onended: null,
            };
            oscillators.push(osc as unknown as FakeNode);
            return osc;
        }),
        createGain: vi.fn(() => ({
            gain: {
                setValueAtTime: vi.fn(),
                linearRampToValueAtTime: vi.fn(),
            },
            connect: vi.fn(),
            disconnect: vi.fn(),
        })),
    };
    const Ctor = vi.fn(() => ctx);
    (globalThis as { AudioContext?: unknown }).AudioContext = Ctor as unknown as typeof AudioContext;
    return { ctx, oscillators, resume };
}

describe('playSegmentEndChime', () => {
    beforeEach(() => {
        _resetChimeThrottle();
        vi.resetModules();
    });

    afterEach(() => {
        delete (globalThis as { AudioContext?: unknown }).AudioContext;
        vi.restoreAllMocks();
    });

    it('no-ops without Web Audio instead of throwing', () => {
        delete (globalThis as { AudioContext?: unknown }).AudioContext;
        expect(() => playSegmentEndChime()).not.toThrow();
    });

    it('throttles a double-fired boundary to one chime', async () => {
        // Fresh module so the memoized `_getCtx` picks up the fake ctor.
        const graph = await import('../../../../../lib/playback/audio-graph');
        const chime = await import('../chime');
        const { ctx } = installFakeAudioContext();
        vi.spyOn(graph, '_getCtx').mockReturnValue(ctx as unknown as AudioContext);
        chime._resetChimeThrottle();

        chime.playSegmentEndChime();
        const afterFirst = ctx.createOscillator.mock.calls.length;
        chime.playSegmentEndChime();
        expect(ctx.createOscillator.mock.calls.length).toBe(afterFirst);
        expect(afterFirst).toBe(2); // one oscillator per tone in the pair
    });

    it('resumes a suspended context', async () => {
        const graph = await import('../../../../../lib/playback/audio-graph');
        const chime = await import('../chime');
        const { ctx, resume } = installFakeAudioContext('suspended');
        vi.spyOn(graph, '_getCtx').mockReturnValue(ctx as unknown as AudioContext);
        chime._resetChimeThrottle();

        chime.playSegmentEndChime();
        expect(resume).toHaveBeenCalled();
    });

    it('swallows a context that throws mid-call', async () => {
        const graph = await import('../../../../../lib/playback/audio-graph');
        const chime = await import('../chime');
        const { ctx } = installFakeAudioContext();
        ctx.createOscillator = vi.fn(() => { throw new Error('context closed'); });
        vi.spyOn(graph, '_getCtx').mockReturnValue(ctx as unknown as AudioContext);
        chime._resetChimeThrottle();

        expect(() => chime.playSegmentEndChime()).not.toThrow();
    });
});
