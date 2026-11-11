/**
 * AudioPort — tests.
 *
 * Owns the abstraction proof: callers see file-absolute ms; transport
 * (CBR chapter URL vs VBR clip URL) and offset arithmetic stay inside the
 * port. The test surface mirrors §10 of the design doc.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { cutAudio, uncutAudio } from '../audio-graph';
import { AudioPort } from '../audio-port';
import { type AudioStub, makeAudioStub } from './raf-harness';

// happy-dom has no Web Audio; mock the kill-switch so we can assert delegation.
vi.mock('../audio-graph', () => ({
    cutAudio: vi.fn(),
    uncutAudio: vi.fn(),
    getAudioGraph: vi.fn(() => null),
    _getCtx: vi.fn(() => null),
}));

let audio: AudioStub;
let port: AudioPort;

beforeEach(() => {
    audio = makeAudioStub({ src: '', readyState: 4 });
    port = new AudioPort();
    port.attachElement(audio as unknown as HTMLAudioElement);
    vi.mocked(cutAudio).mockClear();
    vi.mocked(uncutAudio).mockClear();
});

afterEach(() => {
    port.dispose();
});

// ---------------------------------------------------------------------------
// 1. CBR fast path
// ---------------------------------------------------------------------------

describe('AudioPort — CBR fast path', () => {
    it('first loadCovering swaps to chapter url, subsequent in-window calls no-op', async () => {
        // CBR src defaults to audioUrl when cbrSrc omitted. by_surah reciters
        // pass a pre-wrapped audio-proxy url here; non-by_surah pass canonical.
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: false });
        const r1 = port.loadCovering(0, 1000);
        expect(r1.swapped).toBe(true);
        expect(audio.src).toBe('http://cdn/x.mp3');
        audio._fireEvent('canplay');
        await r1.ready;

        const r2 = port.loadCovering(500, 800);
        expect(r2.swapped).toBe(false);
        expect(r2.ready).toBeDefined();
        // Sync resolution — no canplay required.
        await r2.ready;
    });

    it('uses cbrSrc override when caller pre-wraps via audio-proxy', async () => {
        port.setSource({
            audioUrl: 'http://cdn/x.mp3',
            cbrSrc: '/api/seg/audio-proxy/r1?url=http%3A%2F%2Fcdn%2Fx.mp3',
            reciter: 'r1',
            vbr: false,
        });
        const r = port.loadCovering(0, 1000);
        expect(audio.src).toBe('/api/seg/audio-proxy/r1?url=http%3A%2F%2Fcdn%2Fx.mp3');
        audio._fireEvent('canplay');
        await r.ready;
    });

    it('endMs = Infinity in the loaded window so any future range covers', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: false });
        const r = port.loadCovering(0, 100);
        audio._fireEvent('canplay');
        await r.ready;

        expect(port.window?.endMs).toBe(Number.POSITIVE_INFINITY);
        expect(port.covers(0, 1_000_000)).toBe(true);
    });
});

// ---------------------------------------------------------------------------
// 2. VBR routing
// ---------------------------------------------------------------------------

describe('AudioPort — VBR routing', () => {
    it('builds /api/seg/segment-clip URL for the requested window', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r = port.loadCovering(1000, 2000);
        expect(r.swapped).toBe(true);
        expect(audio.src).toBe(
            '/api/seg/segment-clip/r1?url=http%3A%2F%2Fcdn%2Fx.mp3&start_ms=1000&end_ms=2000',
        );
        expect(r.window).toMatchObject({
            startMs: 1000, endMs: 2000, offsetMs: 1000, isClip: true,
        });
        audio._fireEvent('canplay');
        await r.ready;
    });

    it('subsequent VBR request with the same start and shorter end is a no-op', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r1 = port.loadCovering(1000, 2000);
        audio._fireEvent('canplay');
        await r1.ready;

        const r2 = port.loadCovering(1000, 1800);
        expect(r2.swapped).toBe(false);
    });

    it('VBR request inside an earlier clip reloads so playback can start at clip time 0', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r1 = port.loadCovering(1000, 2000);
        audio._fireEvent('canplay');
        await r1.ready;

        const r2 = port.loadCovering(1500, 1800);
        expect(r2.swapped).toBe(true);
        expect(audio.src).toContain('start_ms=1500');
    });

    it('window-outside-clip triggers a fresh swap', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r1 = port.loadCovering(1000, 2000);
        audio._fireEvent('canplay');
        await r1.ready;

        const r2 = port.loadCovering(2500, 3000);
        expect(r2.swapped).toBe(true);
        expect(audio.src).toContain('start_ms=2500');
    });

    it('falls through to CBR routing when reciter is missing despite vbr:true', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: null, vbr: true });
        const r = port.loadCovering(1000, 2000);
        expect(audio.src).toBe('http://cdn/x.mp3');
        expect(r.window?.isClip).toBe(false);
        audio._fireEvent('canplay');
        await r.ready;
    });
});

// ---------------------------------------------------------------------------
// 3. Padding
// ---------------------------------------------------------------------------

describe('AudioPort — padding', () => {
    it('keeps VBR clip start at playback start and applies pad as post-roll', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r = port.loadCovering(1000, 2000, 200);
        expect(audio.src).toContain('start_ms=1000');
        expect(audio.src).toContain('end_ms=2200');
        expect(r.window).toMatchObject({ startMs: 1000, endMs: 2200, offsetMs: 1000 });
        audio._fireEvent('canplay');
        await r.ready;
    });

    it('subsequent zero-pad VBR call with same start inside the post-roll window no-ops', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r1 = port.loadCovering(1000, 2000, 200);
        audio._fireEvent('canplay');
        await r1.ready;

        const r2 = port.loadCovering(1000, 2100, 0);
        expect(r2.swapped).toBe(false);
    });

    it('does not move VBR clip start earlier when pad would overshoot file start', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r = port.loadCovering(100, 1000, 2000);
        expect(audio.src).toContain('start_ms=100');
        expect(r.window?.startMs).toBe(100);
        expect(r.window?.offsetMs).toBe(100);
        audio._fireEvent('canplay');
        await r.ready;
    });

    it('uses constructor defaultPadMs when caller omits pad', async () => {
        const padded = new AudioPort({ defaultPadMs: 500 });
        padded.attachElement(audio as unknown as HTMLAudioElement);
        padded.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r = padded.loadCovering(1000, 2000);
        expect(audio.src).toContain('start_ms=1000');
        expect(audio.src).toContain('end_ms=2500');
        audio._fireEvent('canplay');
        await r.ready;
        padded.dispose();
    });
});

// ---------------------------------------------------------------------------
// 4. Coordinate translation
// ---------------------------------------------------------------------------

describe('AudioPort — coordinates', () => {
    it('returns 0 offset before any load', () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        expect(port.coordinates().offsetMs).toBe(0);
        expect(port.toClipMs(1500)).toBe(1500);
        expect(port.toFileMs(500)).toBe(500);
    });

    it('VBR clip — currentTimeMs is file-absolute', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const r = port.loadCovering(1000, 2000);
        audio._fireEvent('canplay');
        await r.ready;

        audio.currentTime = 0.7; // 700 ms into the clip
        expect(port.currentTimeMs()).toBeCloseTo(1700, 5);
        expect(port.toClipMs(1500)).toBe(500);
        expect(port.toFileMs(500)).toBe(1500);
    });

    it('CBR — currentTimeMs equals audioEl.currentTime * 1000', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: false });
        const r = port.loadCovering(0, 1000);
        audio._fireEvent('canplay');
        await r.ready;

        audio.currentTime = 5.0;
        expect(port.currentTimeMs()).toBe(5000);
    });
});

// ---------------------------------------------------------------------------
// 5. Overlapping load resolution
// ---------------------------------------------------------------------------

describe('AudioPort — overlapping loads', () => {
    it('newer loadCovering aborts the prior canplay listener with AbortError', async () => {
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: true });
        const first = port.loadCovering(1000, 2000);
        // Don't fire canplay — second call comes in first.
        const firstCanplayCount = audio._listeners.get('canplay')?.size ?? 0;
        expect(firstCanplayCount).toBe(1);

        const second = port.loadCovering(2500, 3500);
        // First's listener detached, second's attached.
        expect(audio._listeners.get('canplay')?.size).toBe(1);

        // First's ready rejects with AbortError.
        await expect(first.ready).rejects.toMatchObject({ name: 'AbortError' });

        // Second resolves on its canplay.
        audio._fireEvent('canplay');
        await second.ready;
        expect(port.window?.startMs).toBe(2500);
    });
});

// ---------------------------------------------------------------------------
// 6. setSource invalidation
// ---------------------------------------------------------------------------

describe('AudioPort — setSource invalidation', () => {
    it('different source triggers a fresh swap on next loadCovering', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        const r1 = port.loadCovering(0, 1000);
        audio._fireEvent('canplay');
        await r1.ready;

        port.setSource({ audioUrl: 'http://cdn/b.mp3', reciter: 'r1', vbr: false });
        expect(port.window).toBeNull();

        const r2 = port.loadCovering(0, 1000);
        expect(r2.swapped).toBe(true);
        expect(audio.src).toContain('b.mp3');
        audio._fireEvent('canplay');
        await r2.ready;
    });

    it('setSource(null) clears the audio src and rejects pending load', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        const r = port.loadCovering(0, 1000);

        port.setSource(null);
        await expect(r.ready).rejects.toMatchObject({ name: 'AbortError' });
        expect(audio.src).toBe('');
        expect(audio.load).toHaveBeenCalled();
    });

    it('same source (re-set) is a no-op', async () => {
        const src = { audioUrl: 'http://cdn/a.mp3', reciter: 'r1' as const, vbr: false };
        port.setSource(src);
        const r = port.loadCovering(0, 1000);
        audio._fireEvent('canplay');
        await r.ready;
        const windowBefore = port.window;
        const loadCallsBefore = audio.load.mock.calls.length;
        port.setSource({ ...src });   // identical fields, fresh object
        // No re-load was issued and the loaded window survived intact —
        // proves the no-op path was actually taken (byte-equality alone
        // does not, since port.source would equal src either way).
        expect(audio.load.mock.calls.length).toBe(loadCallsBefore);
        expect(port.window).toEqual(windowBefore);
        expect(port.source).toEqual(src);
    });
});

// ---------------------------------------------------------------------------
// 7. Element swap
// ---------------------------------------------------------------------------

describe('AudioPort — attachElement', () => {
    it('CORS-enables the element for kill-switch ports before any src is set', () => {
        // Regression: direct-CDN srcs (play-url.ts) through a
        // MediaElementAudioSourceNode are SILENT unless the element is
        // crossorigin="anonymous".
        const el = makeAudioStub({ src: '', readyState: 4 }) as unknown as HTMLAudioElement & { crossOrigin: string | null };
        el.crossOrigin = null;
        new AudioPort().attachElement(el);
        expect(el.crossOrigin).toBe('anonymous');
    });

    it('leaves crossOrigin untouched for non-kill-switch (dashboard) ports', () => {
        const el = makeAudioStub({ src: '', readyState: 4 }) as unknown as HTMLAudioElement & { crossOrigin: string | null };
        el.crossOrigin = null;
        new AudioPort({ disableKillSwitch: true }).attachElement(el);
        expect(el.crossOrigin).toBeNull();
    });

    it('detaches DOM listeners and aborts pending load on element swap', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        const r = port.loadCovering(0, 1000);
        expect(audio._listeners.get('canplay')?.size).toBe(1);

        const elB = makeAudioStub({ src: '', readyState: 4 });
        port.attachElement(elB as unknown as HTMLAudioElement);

        // First element's canplay listener detached.
        expect(audio._listeners.get('canplay')?.size).toBe(0);
        // First load aborted.
        await expect(r.ready).rejects.toMatchObject({ name: 'AbortError' });
        // Window cleared (the new element doesn't share the prior load).
        expect(port.window).toBeNull();
    });

    it('attachElement(null) detaches listeners and clears state', () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        port.loadCovering(0, 1000);
        expect(audio._listeners.get('canplay')?.size).toBe(1);

        port.attachElement(null);
        expect(audio._listeners.get('canplay')?.size).toBe(0);
        expect(port.element).toBeNull();
        expect(port.window).toBeNull();
    });

    it('applies the remembered playback rate to a newly attached element', () => {
        port.setPlaybackRate(1.5);
        const elB = makeAudioStub({ src: '', readyState: 4 });

        port.attachElement(elB as unknown as HTMLAudioElement);

        expect(elB.playbackRate).toBe(1.5);
        expect(port.playbackRate).toBe(1.5);
    });
});

// ---------------------------------------------------------------------------
// 7b. adoptElement — element-reuse fast path
// ---------------------------------------------------------------------------

describe('AudioPort — adoptElement', () => {
    it('binds the new element and synthesises a CBR window without re-loading', async () => {
        // Set up an already-loaded element (simulating a prewarmed shadow).
        const warm = makeAudioStub({ src: 'http://cdn/x.mp3', readyState: 4 });
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: false });

        port.adoptElement(warm as unknown as HTMLAudioElement, 'http://cdn/x.mp3');

        // Window synthesised — no canplay needed.
        expect(port.window).toMatchObject({
            startMs: 0,
            endMs: Number.POSITIVE_INFINITY,
            offsetMs: 0,
            isClip: false,
            src: 'http://cdn/x.mp3',
        });
        // No load() called on the adopted element — we're trusting it was
        // already past canplay when it was handed in.
        expect(warm.load).not.toHaveBeenCalled();
        // Subsequent loadCovering short-circuits via fast path 1.
        const r = port.loadCovering(0, 1000);
        expect(r.swapped).toBe(false);
        await r.ready;
    });

    it('detaches DOM listeners from the prior element when adopting', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        port.loadCovering(0, 1000);
        expect(audio._listeners.get('canplay')?.size).toBe(1);

        const warm = makeAudioStub({ src: 'http://cdn/b.mp3', readyState: 4 });
        port.adoptElement(warm as unknown as HTMLAudioElement, 'http://cdn/b.mp3');

        // Old element's listeners detached.
        expect(audio._listeners.get('canplay')?.size).toBe(0);
        expect(port.element).toBe(warm);
    });

    it('seekAndPlay after adopt operates on the new element', async () => {
        const warm = makeAudioStub({ src: 'http://cdn/x.mp3', readyState: 4 });
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: false });
        port.adoptElement(warm as unknown as HTMLAudioElement, 'http://cdn/x.mp3');

        port.seekAndPlay(5000);
        expect(warm.currentTime).toBeCloseTo(5.0, 5);
        expect(warm.play).toHaveBeenCalledTimes(1);
        // Original element untouched.
        expect(audio.play).not.toHaveBeenCalled();
    });

    it('preserves the selected playback rate when adopting a warm element', () => {
        port.setPlaybackRate(1.75);
        const warm = makeAudioStub({ src: 'http://cdn/x.mp3', readyState: 4 });
        port.setSource({ audioUrl: 'http://cdn/x.mp3', reciter: 'r1', vbr: false });

        port.adoptElement(warm as unknown as HTMLAudioElement, 'http://cdn/x.mp3');
        port.seekAndPlay(0);

        expect(warm.playbackRate).toBe(1.75);
        expect(warm.play).toHaveBeenCalledTimes(1);
    });
});

// ---------------------------------------------------------------------------
// 7c. Playback rate persistence
// ---------------------------------------------------------------------------

describe('AudioPort — playback rate persistence', () => {
    it('reapplies the selected playback rate before source swaps and play()', async () => {
        port.setPlaybackRate(1.5);
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        const r = port.loadCovering(0, 1000);
        expect(audio.playbackRate).toBe(1.5);
        audio._fireEvent('canplay');
        await r.ready;

        audio.playbackRate = 1;
        port.play();

        expect(audio.playbackRate).toBe(1.5);
        expect(audio.play).toHaveBeenCalledTimes(1);
    });
});

// ---------------------------------------------------------------------------
// 8. dispose
// ---------------------------------------------------------------------------

describe('AudioPort — dispose', () => {
    it('clears subscribers and detaches the element', () => {
        const cb = vi.fn();
        port.onPlay(cb);
        port.dispose();

        // Firing play on the (now-detached) element does NOT fan out.
        audio._fireEvent('play');
        expect(cb).not.toHaveBeenCalled();
        expect(port.element).toBeNull();
    });
});

// ---------------------------------------------------------------------------
// 9. cut/uncut delegation
// ---------------------------------------------------------------------------

describe('AudioPort — pauseAndFlush / uncut', () => {
    it('pauseAndFlush calls cutAudio then pauses the element', () => {
        audio.paused = false;
        port.pauseAndFlush();
        expect(vi.mocked(cutAudio)).toHaveBeenCalledTimes(1);
        expect(vi.mocked(cutAudio)).toHaveBeenCalledWith(audio);
        expect(audio.pause).toHaveBeenCalledTimes(1);
    });

    it('uncut calls uncutAudio on the bound element', () => {
        port.uncut();
        expect(vi.mocked(uncutAudio)).toHaveBeenCalledTimes(1);
        expect(vi.mocked(uncutAudio)).toHaveBeenCalledWith(audio);
    });

    it('seekAndPlay applies uncut + safePlay', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        const r = port.loadCovering(0, 1000);
        audio._fireEvent('canplay');
        await r.ready;

        port.seekAndPlay(500);
        expect(audio.currentTime).toBeCloseTo(0.5, 5);
        expect(vi.mocked(uncutAudio)).toHaveBeenCalled();
        expect(audio.play).toHaveBeenCalledTimes(1);
    });

    it('seekAndPlay translates file-absolute into clip-relative on VBR', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: true });
        const r = port.loadCovering(5000, 6000);
        audio._fireEvent('canplay');
        await r.ready;

        port.seekAndPlay(5500);  // file-absolute
        expect(audio.currentTime).toBeCloseTo(0.5, 5);   // clip-relative
    });
});

// ---------------------------------------------------------------------------
// 10. Event re-emission (file-absolute time on timeupdate)
// ---------------------------------------------------------------------------

describe('AudioPort — events', () => {
    it('marks seeks synchronously without treating playback ticks as discontinuities', () => {
        const initial = port.timelineVersion;
        audio.currentTime = 0.5;
        audio._fireEvent('timeupdate');
        expect(port.timelineVersion).toBe(initial);
        port.seek(400);
        expect(port.timelineVersion).toBeGreaterThan(initial);
        const afterSeek = port.timelineVersion;
        audio._fireEvent('seeking');
        expect(port.timelineVersion).toBeGreaterThan(afterSeek);
    });

    it('marks transport replacement but preserves the version when coverage is reused', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: true });
        const first = port.loadCovering(5000, 6000);
        audio._fireEvent('canplay');
        await first.ready;
        const version = port.timelineVersion;
        port.loadCovering(5000, 6000);
        expect(port.timelineVersion).toBe(version);
        port.loadCovering(5200, 6000);
        expect(port.timelineVersion).toBeGreaterThan(version);

        port.attachElement(null);
        const detached = port.timelineVersion;
        audio._fireEvent('seeking');
        expect(port.timelineVersion).toBe(detached);
    });

    it('onPlay/onPause/onEnded fire when the element fires the DOM event', () => {
        const onPlay = vi.fn(), onPause = vi.fn(), onEnded = vi.fn();
        port.onPlay(onPlay); port.onPause(onPause); port.onEnded(onEnded);

        audio._fireEvent('play');
        audio._fireEvent('pause');
        audio._fireEvent('ended');

        expect(onPlay).toHaveBeenCalledTimes(1);
        expect(onPause).toHaveBeenCalledTimes(1);
        expect(onEnded).toHaveBeenCalledTimes(1);
    });

    it('onTimeUpdate receives file-absolute ms (with offset applied on VBR)', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: true });
        const r = port.loadCovering(5000, 6000);
        audio._fireEvent('canplay');
        await r.ready;

        const onTU = vi.fn();
        port.onTimeUpdate(onTU);
        audio.currentTime = 0.3;   // 300 ms into the clip
        audio._fireEvent('timeupdate');

        expect(onTU).toHaveBeenCalledTimes(1);
        expect(onTU.mock.calls[0]?.[0]).toBeCloseTo(5300, 5);
    });

    it('onLoad fires after canplay resolves', async () => {
        const onLoad = vi.fn();
        port.onLoad(onLoad);
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        const r = port.loadCovering(0, 1000);
        expect(onLoad).not.toHaveBeenCalled();
        audio._fireEvent('canplay');
        await r.ready;
        expect(onLoad).toHaveBeenCalledTimes(1);
        expect(onLoad.mock.calls[0]?.[0]).toMatchObject({ isClip: false });
    });

    it('unsubscribe stops further callbacks', () => {
        const cb = vi.fn();
        const off = port.onPlay(cb);
        audio._fireEvent('play');
        expect(cb).toHaveBeenCalledTimes(1);
        off();
        audio._fireEvent('play');
        expect(cb).toHaveBeenCalledTimes(1);
    });
});

// ---------------------------------------------------------------------------
// 11. covers / loadCovering invariants
// ---------------------------------------------------------------------------

describe('AudioPort — covers', () => {
    it('returns false before any load', () => {
        expect(port.covers(0, 100)).toBe(false);
    });

    it('returns true for any range under CBR (endMs Infinity)', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        const r = port.loadCovering(0, 1000);
        audio._fireEvent('canplay');
        await r.ready;
        expect(port.covers(0, Number.MAX_SAFE_INTEGER / 2)).toBe(true);
    });

    it('respects the loaded clip span on VBR', async () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: true });
        const r = port.loadCovering(1000, 2000, 100);
        audio._fireEvent('canplay');
        await r.ready;
        expect(port.covers(1000, 2100)).toBe(true);
        expect(port.covers(900, 2100)).toBe(false);
        expect(port.covers(1000, 2200)).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// 12. Bad inputs
// ---------------------------------------------------------------------------

describe('AudioPort — guards', () => {
    it('loadCovering throws when no element is attached', () => {
        const p = new AudioPort();
        p.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        expect(() => p.loadCovering(0, 100)).toThrow(/no element/);
    });

    it('loadCovering throws when no source is bound', () => {
        expect(() => port.loadCovering(0, 100)).toThrow(/no source/);
    });

    it('loadCovering throws on inverted range', () => {
        port.setSource({ audioUrl: 'http://cdn/a.mp3', reciter: 'r1', vbr: false });
        expect(() => port.loadCovering(500, 100)).toThrow(/bad range/);
    });
});
