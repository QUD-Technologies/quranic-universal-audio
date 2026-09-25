/**
 * play-url.ts — direct-CDN vs audio-proxy resolution.
 *
 * Two cached verdicts from one head fetch per URL: the HOST must answer a
 * CORS Range request with 206, and the FILE's first frame must not carry a
 * `Xing` tag (TOC seek drifts). An unknown URL is proxied (never silence,
 * never drift). Same-origin and non-http URLs never probe.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MP3_SNIFF_BYTES } from '../mp3-header';
import {
    _resetPlayUrlForTest,
    isDirectPlayable,
    needsDirectProbe,
    playUrl,
    probeDirectPlayable,
    proxyPlayUrl,
    resolvePlayUrl,
} from '../play-url';
import { mp3Head } from './mp3-fixtures';

const CDN = 'https://audio-cdn.example.com/quran/husary/002.mp3';
const CDN_SIBLING = 'https://audio-cdn.example.com/quran/husary/003.mp3';
const PROXIED = `/api/seg/audio-proxy/husary?url=${encodeURIComponent(CDN)}`;
const PROXIED_SIBLING = `/api/seg/audio-proxy/husary?url=${encodeURIComponent(CDN_SIBLING)}`;

let fetchMock: ReturnType<typeof vi.fn>;

function respond(status: number, body: Uint8Array | null = mp3Head({ tag: 'Info' })): void {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(body as BodyInit | null, { status })));
}

/** Per-URL bodies — lets one host serve an Info chapter and a Xing chapter. */
function respondPerUrl(bodies: Record<string, Uint8Array>): void {
    fetchMock.mockImplementation((url: string) =>
        Promise.resolve(new Response((bodies[url] ?? null) as BodyInit | null, { status: 206 })));
}

beforeEach(() => {
    _resetPlayUrlForTest();
    fetchMock = vi.fn();
    respond(206);
    vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('proxyPlayUrl', () => {
    it('wraps a cross-origin URL and passes /api/ and empty through', () => {
        expect(proxyPlayUrl('husary', CDN)).toBe(PROXIED);
        expect(proxyPlayUrl('husary', '/api/seg/clip/x')).toBe('/api/seg/clip/x');
        expect(proxyPlayUrl('husary', '')).toBe('');
    });
});

describe('playUrl before any probe', () => {
    it('returns the proxy wrapper for an unprobed URL', () => {
        expect(isDirectPlayable(CDN)).toBe(false);
        expect(playUrl('husary', CDN)).toBe(PROXIED);
        expect(fetchMock).not.toHaveBeenCalled();
    });
});

describe('probeDirectPlayable', () => {
    it('sends one CORS head fetch and flips the URL to direct on 206 + Info', async () => {
        await expect(probeDirectPlayable(CDN)).resolves.toBe(true);
        expect(fetchMock).toHaveBeenCalledOnce();
        const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { headers: Record<string, string> }];
        expect(url).toBe(CDN);
        expect(init.mode).toBe('cors');
        expect(init.headers.Range).toBe(`bytes=0-${MP3_SNIFF_BYTES - 1}`);
        expect(playUrl('husary', CDN)).toBe(CDN);
    });

    it('plays an untagged (no Xing / Info) file direct', async () => {
        respond(206, mp3Head({ tag: null }));
        await expect(probeDirectPlayable(CDN)).resolves.toBe(true);
    });

    it('keeps a Xing-tagged file on the proxy even though the host is CORS-ok', async () => {
        respond(206, mp3Head({ tag: 'Xing', id3: 9759 }));
        await expect(probeDirectPlayable(CDN)).resolves.toBe(false);
        expect(playUrl('husary', CDN)).toBe(PROXIED);
    });

    it('keeps the proxy when no frame header is found in the head window', async () => {
        respond(206, new Uint8Array(32));
        await expect(probeDirectPlayable(CDN)).resolves.toBe(false);
    });

    it('caches per URL — a sibling chapter re-sniffs its own head once', async () => {
        respondPerUrl({ [CDN]: mp3Head({ tag: 'Info' }), [CDN_SIBLING]: mp3Head({ tag: 'Xing' }) });
        await probeDirectPlayable(CDN);
        await probeDirectPlayable(CDN_SIBLING);
        await probeDirectPlayable(CDN_SIBLING);
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(playUrl('husary', CDN)).toBe(CDN);
        expect(playUrl('husary', CDN_SIBLING)).toBe(PROXIED_SIBLING);
    });

    it('coalesces concurrent probes of one URL into a single fetch', async () => {
        await Promise.all([probeDirectPlayable(CDN), probeDirectPlayable(CDN)]);
        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('keeps the proxy when the CDN answers 200 (no Range support)', async () => {
        respond(200);
        await expect(probeDirectPlayable(CDN)).resolves.toBe(false);
        expect(playUrl('husary', CDN)).toBe(PROXIED);
    });

    it('short-circuits every later URL on a host that failed the Range probe', async () => {
        respond(200);
        await probeDirectPlayable(CDN);
        await expect(probeDirectPlayable(CDN_SIBLING)).resolves.toBe(false);
        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('keeps the proxy when the CORS fetch rejects (no ACAO)', async () => {
        fetchMock.mockImplementation(() => Promise.reject(new TypeError('Failed to fetch')));
        await expect(probeDirectPlayable(CDN)).resolves.toBe(false);
        expect(playUrl('husary', CDN)).toBe(PROXIED);
        // Negative verdicts are cached too — no re-probe storm on every play.
        await probeDirectPlayable(CDN);
        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('never probes same-origin or non-http URLs', async () => {
        await expect(probeDirectPlayable('/api/seg/clip/x')).resolves.toBe(false);
        await expect(probeDirectPlayable('qua-sample://abc/1')).resolves.toBe(false);
        await expect(probeDirectPlayable('')).resolves.toBe(false);
        expect(fetchMock).not.toHaveBeenCalled();
    });
});

describe('resolvePlayUrl', () => {
    it('probes then returns the direct URL on 206 + Info', async () => {
        await expect(resolvePlayUrl('husary', CDN)).resolves.toBe(CDN);
    });

    it('probes then returns the proxy on failure', async () => {
        respond(403);
        await expect(resolvePlayUrl('husary', CDN)).resolves.toBe(PROXIED);
    });
});

describe('needsDirectProbe', () => {
    it('is true for an unprobed CDN URL and false once either verdict is cached', async () => {
        expect(needsDirectProbe(CDN)).toBe(true);
        await probeDirectPlayable(CDN);
        expect(needsDirectProbe(CDN)).toBe(false);

        respond(403);
        expect(needsDirectProbe(CDN_SIBLING)).toBe(true);
        await probeDirectPlayable(CDN_SIBLING);
        expect(needsDirectProbe(CDN_SIBLING)).toBe(false);
    });

    it('is false for URLs that never probe', () => {
        expect(needsDirectProbe('/api/seg/clip/x')).toBe(false);
        expect(needsDirectProbe('qua-sample://abc/1')).toBe(false);
        expect(needsDirectProbe('')).toBe(false);
    });
});
