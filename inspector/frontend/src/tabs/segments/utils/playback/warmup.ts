/**
 * CBR audio warmup.
 *
 * A 64 KB Range fetch at the seg's byte offset (not a full-chapter blob),
 * with a priority hint and 30 s dedupe; fires on user-intent signals
 * (chapter load, accordion mount, hover, play→next-seg). Goal: hide
 * cold-FUSE / cold-CDN play stalls while fetching only the bytes that play.
 *
 * VBR is deferred — segments tab routes VBR per-seg through the segment-clip
 * endpoint, which is its own pre-rendered, deterministically-cacheable URL.
 *
 * Skip rules (in order):
 *   1. Chapter not CBR with valid kbps  → no-op (covers VBR + missing data).
 *   2. Same `(reciter, audio_url, byteStart >> 16)` warmed within 30 s → skip.
 *   3. When `current` supplied: same chapter AND `gap < 30 s` → skip
 *      (browser forward buffer covers it).
 *
 * Byte formula (CBR, naive — server-side FUSE page-cache warming is what
 * matters, not browser-side Range coalescing, and 64 KB covers the typical
 * ~25 KB ID3v2 prefix offset error):
 *
 *   bytesPerSec = kbps * 125            // = kbps * 1000 / 8
 *   byteStart   = (time_start / 1000) * bytesPerSec
 *
 * Cost: one 64 KB Range fetch per warm. Fire-and-forget; rejected fetches
 * are swallowed.
 */

import { get } from 'svelte/store';

import { playUrl, probeDirectPlayable } from '../../../../lib/playback/play-url';
import { shadowPrewarm } from '../../../../lib/playback/shadow-audio';
import type { Segment } from '../../../../lib/types/view-models';
import { segAllData } from '../../stores/chapter';
import { cbrKbpsForChapter } from '../../stores/chapter-meta';

/** Main-list segs from `/api/seg/all/<reciter>` deliberately omit `audio_url`
 *  to save wire bytes; every FE consumer falls back to the top-level
 *  `audio_by_chapter[chapter]` map. Mirror that here so the hover and
 *  next-seg triggers don't silently no-op on main-list segs. */
function _segAudioUrl(seg: Segment): string {
    if (seg.audio_url) return seg.audio_url;
    const chapter = seg.chapter;
    if (chapter == null) return '';
    return get(segAllData)?.audio_by_chapter?.[String(chapter)] ?? '';
}

const WARMUP_BYTES = 65536;            // 64 KB
const DEDUPE_WINDOW_MS = 30_000;
const PROXIMITY_GAP_MS = 30_000;

/** key -> last-fired-at-ms. Key is `${reciter}|${audio_url}|${byteBucket}`
 *  where byteBucket = byteStart >> 16, i.e. one slot per 64 KB region. */
const warmedRecently = new Map<string, number>();

function _pruneExpired(nowMs: number): void {
    for (const [k, t] of warmedRecently) {
        if (nowMs - t > DEDUPE_WINDOW_MS) warmedRecently.delete(k);
    }
}

function _fire(reciter: string, audioUrl: string, byteStart: number): void {
    // Direct CDN hosts get warmed at the edge instead of the Space's page cache.
    const proxyUrl = playUrl(reciter, audioUrl);
    const startedAt = performance.now();
    // Verbose-mode trace — flip via `localStorage.insp_warmup_log = 'true'` to
    // confirm the warmup is firing in production. The win is server-side OS
    // page cache (the audio element's open-ended Range hits warm pages after
    // this completes), so the visible signal is the response time on this
    // request — if it's slow, the user's eventual play TTFB shows the same
    // cost; if it's fast, the play TTFB is fast for "free".
    const trace = (typeof localStorage !== 'undefined'
        && localStorage.getItem('insp_warmup_log') === 'true');
    if (trace) {
         
        console.log(`[warmup] fire ${audioUrl} bytes=${byteStart}-${byteStart + WARMUP_BYTES - 1}`);
    }
    fetch(proxyUrl, {
        headers: { Range: `bytes=${byteStart}-${byteStart + WARMUP_BYTES - 1}` },
    })
        .then((r) => {
            if (trace) {
                 
                console.log(`[warmup] done ${audioUrl} bytes=${byteStart} → ${r.status} in ${Math.round(performance.now() - startedAt)}ms`);
            }
        })
        .catch(() => {});
}

function _warmAtByte(
    reciter: string,
    audioUrl: string,
    byteStart: number,
): void {
    const now = Date.now();
    _pruneExpired(now);
    const key = `${reciter}|${audioUrl}|${byteStart >> 16}`;
    const last = warmedRecently.get(key);
    if (last !== undefined && now - last < DEDUPE_WINDOW_MS) return;
    warmedRecently.set(key, now);
    _fire(reciter, audioUrl, byteStart);
}

/** Warm the chapter MP3 at the byte offset corresponding to `seg.time_start`.
 *
 *  Triggers: accordion card mount (first sibling), hover seg play button,
 *  play seg N → next sibling.
 *
 *  When `current` is supplied (next-sibling trigger), the proximity skip
 *  short-circuits when `seg` lives in the same chapter audio file and is
 *  less than 30 s past `current.time_end` — the browser's forward buffer
 *  on the active `<audio>` element will cover it.
 */
export function warmSeg(
    seg: Segment | null | undefined,
    reciter: string,
    current?: Segment | null,
): void {
    if (!seg || !reciter) return;
    const chapter = seg.chapter;
    if (chapter == null) return;
    const audioUrl = _segAudioUrl(seg);
    if (!audioUrl) return;
    const currentUrl = current ? _segAudioUrl(current) : '';
    if (current
        && currentUrl
        && currentUrl === audioUrl
        && seg.time_start - current.time_end < PROXIMITY_GAP_MS) return;
    const kbps = cbrKbpsForChapter(chapter);
    if (!kbps) return;
    const bytesPerSec = kbps * 125;
    const byteStart = Math.max(0, Math.floor((seg.time_start / 1000) * bytesPerSec));
    _warmAtByte(reciter, audioUrl, byteStart);
}

/** Warm a CBR seg's chapter ahead of a likely play (accordion lead card,
 *  play-button hover). Probes the CDN first so the eventual click resolves
 *  direct instead of via the proxy, then warms the 64 KB Range at the seg.
 *  Only a DIRECT chapter also gets the hidden-`<audio>` whole-chapter warm:
 *  a proxied one would stream the chapter through the single-worker Space
 *  alongside the play it is meant to speed up. */
export function warmSegChapter(
    seg: Segment | null | undefined,
    reciter: string,
    audioUrl: string = seg ? _segAudioUrl(seg) : '',
): void {
    if (!seg || !reciter || !audioUrl) return;
    void probeDirectPlayable(audioUrl).then((direct) => {
        if (direct) shadowPrewarm(audioUrl);
        warmSeg(seg, reciter);
    });
}

/** Warm bytes 0–65535 of a chapter MP3 — used by the chapter-load trigger
 *  where no seg context is yet available. Most reviewers start playback
 *  near the chapter start, so byte-0 is the right default. No-op for
 *  VBR + missing-kbps chapters (skip via `cbrKbpsForChapter`).
 */
export function warmChapterStart(
    reciter: string,
    audioUrl: string,
    chapter: number | null | undefined,
): void {
    if (!reciter || !audioUrl || chapter == null) return;
    if (!cbrKbpsForChapter(chapter)) return;
    _warmAtByte(reciter, audioUrl, 0);
}

/** Test-only — reset the dedupe map between cases. */
export function _resetWarmedRecentlyForTest(): void {
    warmedRecently.clear();
}
