/**
 * Play-URL resolver — the ONE place that decides whether a chapter MP3 is
 * loaded straight from its CDN or through the same-origin audio-proxy.
 *
 * Why this exists: every chapter used to be wrapped in
 * `/api/seg/audio-proxy/<reciter>?url=…` so the `<audio crossorigin="anonymous">`
 * element (routed through Web Audio's `MediaElementAudioSourceNode`) always
 * saw an `Access-Control-Allow-Origin` header. That routes the whole
 * multi-minute MP3 through the single-worker Space, and from far-away
 * clients (AU / Middle East ↔ US Space) that pipe runs at ~300-500 KB/s —
 * the browser's read-ahead on a 60 MB chapter then starves every other
 * request multiplexed on the same HTTP/2 connection (small JSON calls
 * queue for 5-10 s). Most published CDNs (tarteel / mp3quran / quranicaudio /
 * way2quran / archive.org) already send `ACAO: *` + Range from an edge near
 * the user, so playing them directly is both faster and off the Space.
 *
 * Contract:
 * - `probeDirectPlayable(url)` fetches the head of the file once per URL
 *   (`Range: bytes=0-65535`, CORS mode) and caches the outcome of three
 *   checks:
 *     · per HOST — the CDN answered 206 to a CORS request (Range support is
 *       what makes seeking work; a missing ACAO rejects the fetch, which is
 *       exactly the case where the element would play silence). A failed
 *       host short-circuits every later URL on it without a fetch.
 *     · per URL — the file seeks natively (`mp3-header.ts`): its first
 *       MPEG frame carries an `Info` tag, or no tag and the sniffed frames
 *       run at the header's nominal byte rate. Chrome seeks a `Xing`-tagged
 *       file through its 1/256-quantised TOC and an untagged one at the
 *       nominal rate, landing seconds off either way while still reporting
 *       the requested `currentTime`; the bucket copy the proxy serves is
 *       remuxed with an `Info` tag and seeks exactly.
 *     · per URL — the CDN file is still the file the delivery was aligned
 *       on: its total size equals the manifest `size_bytes` registered via
 *       `registerExpectedSizes`. A source CDN can replace a chapter with a
 *       different recording under the same URL; the bucket copy (and every
 *       segment / timestamp) keeps the original. The total comes from the
 *       206 `Content-Range`, or — on hosts that don't expose that header
 *       cross-origin — from a two-byte read at `size - 1`, which returns
 *       exactly one byte only when the file ends there.
 *   Direct requires all three; a URL with no registered size never probes.
 * - `playUrl(reciter, url)` is the SYNC decision used at `setSource` time:
 *   direct when the URL is known-good, proxy otherwise (unknown URLs stay
 *   on the proxy — never silence, never drift). Callers that can `await`
 *   should probe first (chapter load, shuffle prime, dashboard hover) so
 *   the first play already goes direct.
 * - Same-origin (`/api/...`) and non-http (`qua-sample://`) URLs always pass
 *   through the proxy wrapper unchanged / wrapped, respectively.
 *
 * The proxy string shape is unchanged so `normalizeAudioUrl` / `audioSrcMatches`
 * keep treating proxied and direct forms of one chapter as the same resource.
 */

import { isNativelySeekable, MP3_SNIFF_BYTES } from './mp3-header';

/** host → true when the CDN answered a CORS Range probe with 206. */
const _hostDirect = new Map<string, boolean>();
/** url → true when the host is direct-capable, the file seeks natively and
 *  its size matches the manifest. */
const _urlDirect = new Map<string, boolean>();
/** url → manifest `size_bytes` of the file the delivery was aligned on. */
const _expectedSize = new Map<string, number>();
const _inflight = new Map<string, Promise<boolean>>();

/** Build the same-origin proxy URL for a chapter MP3. Already-proxied
 *  (`/api/...`) URLs and empty strings pass through untouched. */
export function proxyPlayUrl(reciter: string, url: string): string {
    if (!url || url.startsWith('/api/')) return url;
    return `/api/seg/audio-proxy/${reciter}?url=${encodeURIComponent(url)}`;
}

/** The cross-origin http(s) host of `url`, or null when it isn't one
 *  (same-origin path, `qua-sample://`, malformed). */
function _crossOriginHost(url: string): string | null {
    if (!url || url.startsWith('/')) return null;
    try {
        const u = new URL(url, globalThis.location?.origin ?? 'http://localhost');
        if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
        if (globalThis.location && u.origin === globalThis.location.origin) return null;
        return u.host;
    } catch {
        return null;
    }
}

/** True when `url` has already passed both the host (CORS + Range) and the
 *  file (native-seek) probes. */
export function isDirectPlayable(url: string): boolean {
    return _urlDirect.get(url) === true;
}

/** True when `url` is a cross-origin CDN URL with no verdict yet — i.e. a
 *  sync `playUrl` right now would fall back to the proxy only because nobody
 *  has probed it. Same-origin / non-http URLs never need a probe. */
export function needsDirectProbe(url: string): boolean {
    return _crossOriginHost(url) !== null && !_urlDirect.has(url);
}

/** Register manifest sizes for chapter URLs. A changed size drops the URL's
 *  cached verdict so the next probe re-checks it. Null / non-positive sizes
 *  are ignored — those URLs stay on the proxy. */
export function registerExpectedSizes(sizes: Record<string, number | null | undefined>): void {
    for (const [url, size] of Object.entries(sizes)) {
        if (typeof size !== 'number' || size <= 0 || _expectedSize.get(url) === size) continue;
        _expectedSize.set(url, size);
        _urlDirect.delete(url);
    }
}

function _rangeGet(url: string, range: string): Promise<Response> {
    return fetch(url, { mode: 'cors', credentials: 'omit', cache: 'no-store', headers: { Range: range } });
}

/** Total length from a `Content-Range: bytes a-b/total` header, or null. */
function _contentRangeTotal(header: string | null): number | null {
    const m = header ? /\/(\d+)\s*$/.exec(header) : null;
    return m ? Number(m[1]) : null;
}

async function _sizeMatches(url: string, head: Response, expected: number): Promise<boolean> {
    const total = _contentRangeTotal(head.headers.get('Content-Range'));
    if (total !== null) return total === expected;
    const tail = await _rangeGet(url, `bytes=${expected - 1}-${expected}`);
    if (tail.status !== 206) {
        void tail.body?.cancel().catch(() => {});
        return false;
    }
    return (await tail.arrayBuffer()).byteLength === 1;
}

async function _sniff(url: string, host: string, expected: number): Promise<boolean> {
    const res = await _rangeGet(url, `bytes=0-${MP3_SNIFF_BYTES - 1}`);
    if (res.status !== 206) {
        void res.body?.cancel().catch(() => {});
        _hostDirect.set(host, false);
        return false;
    }
    _hostDirect.set(host, true);
    const head = new Uint8Array(await res.arrayBuffer());
    return isNativelySeekable(head) && _sizeMatches(url, res, expected);
}

/** Probe `url` once (cached, coalesced). Resolves true when the chapter can
 *  be played directly. Never throws — a network / CORS failure is a `false`
 *  verdict and the caller falls back to the proxy. */
export function probeDirectPlayable(url: string): Promise<boolean> {
    const host = _crossOriginHost(url);
    if (!host) return Promise.resolve(false);
    const known = _urlDirect.get(url);
    if (known !== undefined) return Promise.resolve(known);
    const expected = _expectedSize.get(url);
    if (expected === undefined || _hostDirect.get(host) === false) {
        _urlDirect.set(url, false);
        return Promise.resolve(false);
    }
    const pending = _inflight.get(url);
    if (pending) return pending;

    const probe = _sniff(url, host, expected)
        .catch(() => false)
        .then((ok) => {
            _urlDirect.set(url, ok);
            _inflight.delete(url);
            return ok;
        });
    _inflight.set(url, probe);
    return probe;
}

/** Sync resolution: direct CDN URL when it is known-good, else the proxy
 *  wrapper. Safe to call before any probe — unknown = proxy. */
export function playUrl(reciter: string, url: string): string {
    return isDirectPlayable(url) ? url : proxyPlayUrl(reciter, url);
}

/** Probe-then-resolve for callers that can await (chapter load, prewarm). */
export async function resolvePlayUrl(reciter: string, url: string): Promise<string> {
    await probeDirectPlayable(url);
    return playUrl(reciter, url);
}

/** Test hook: forget every probe verdict. */
export function _resetPlayUrlForTest(): void {
    _hostDirect.clear();
    _urlDirect.clear();
    _expectedSize.clear();
    _inflight.clear();
}
