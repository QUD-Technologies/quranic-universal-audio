/**
 * warmup.ts — CBR audio warmup util.
 *
 * Verifies the byte-offset formula, the three skip rules (no kbps for chapter,
 * dedupe within 30 s, proximity skip for same-chapter close-time segs), and
 * the fire-and-forget shape (rejected fetches don't surface).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { mp3Head } from '../../../../../lib/playback/__tests__/mp3-fixtures';
import { _resetPlayUrlForTest } from '../../../../../lib/playback/play-url';
import { shadowPrewarm } from '../../../../../lib/playback/shadow-audio';

import type { SegAllResponse } from '../../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../../lib/types/view-models';
import { segAllData } from '../../../stores/chapter';
import { chapterCbrKbps } from '../../../stores/chapter-meta';
import { _resetWarmedRecentlyForTest, warmChapterStart, warmSeg, warmSegChapter } from '../warmup';

vi.mock('../../../../../lib/playback/shadow-audio', () => ({ shadowPrewarm: vi.fn() }));

function makeSeg(overrides: Partial<Segment> = {}): Segment {
    return {
        index: 0,
        entry_idx: 0,
        time_start: 0,
        time_end: 1000,
        matched_ref: '1:1:1-1:1:1',
        matched_text: 'x',
        confidence: 1.0,
        audio_url: 'http://x/ch1.mp3',
        chapter: 1,
        ...overrides,
    } as Segment;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
    _resetWarmedRecentlyForTest();
    chapterCbrKbps.set(new Map([[1, 128], [2, 96]]));
    fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 206 })));
    vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    chapterCbrKbps.set(new Map());
    segAllData.set(null);
});

describe('warmSeg — byte formula', () => {
    it('time_start=0 → byteStart=0 in Range header', () => {
        warmSeg(makeSeg({ time_start: 0, chapter: 1 }), 'reciter1');
        expect(fetchMock).toHaveBeenCalledOnce();
        const init = fetchMock.mock.calls[0]?.[1] as RequestInit & { headers: Record<string, string> };
        expect(init.headers.Range).toBe('bytes=0-65535');
    });

    it('time_start=10000ms, kbps=128 → byteStart=160000', () => {
        warmSeg(makeSeg({ time_start: 10_000, chapter: 1 }), 'reciter1');
        const init = fetchMock.mock.calls[0]?.[1] as RequestInit & { headers: Record<string, string> };
        expect(init.headers.Range).toBe('bytes=160000-225535');
    });

    it('encodes the chapter URL in the proxy URL', () => {
        warmSeg(makeSeg({ audio_url: 'https://cdn.example/file with spaces.mp3', chapter: 1 }), 'rec');
        const url = fetchMock.mock.calls[0]?.[0] as string;
        expect(url).toMatch(/^\/api\/seg\/audio-proxy\/rec\?url=/);
        expect(url).toContain(encodeURIComponent('https://cdn.example/file with spaces.mp3'));
    });

    it('does not pass `priority: low` hint', () => {
        warmSeg(makeSeg(), 'r');
        const init = fetchMock.mock.calls[0]?.[1] as RequestInit & { priority?: string };
        expect(init.priority).toBeUndefined();
    });
});

describe('warmSeg — skip rules', () => {
    it('no-op when chapter is not in the CBR kbps map (covers VBR + missing data)', () => {
        warmSeg(makeSeg({ chapter: 99 }), 'r'); // chapter 99 not in map
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('no-op when seg is null', () => {
        warmSeg(null, 'r');
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('no-op when reciter is empty', () => {
        warmSeg(makeSeg(), '');
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('no-op when audio_url is empty', () => {
        warmSeg(makeSeg({ audio_url: '' }), 'r');
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('no-op when chapter is null', () => {
        warmSeg(makeSeg({ chapter: null as unknown as number }), 'r');
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('proximity skip — same audio_url, gap < 30 s', () => {
        const current = makeSeg({ time_start: 5_000, time_end: 7_000, chapter: 1 });
        const next = makeSeg({ time_start: 8_000, time_end: 10_000, chapter: 1 });
        warmSeg(next, 'r', current);
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('proximity fires — same audio_url, gap >= 30 s', () => {
        const current = makeSeg({ time_start: 5_000, time_end: 7_000, chapter: 1 });
        const next = makeSeg({ time_start: 40_000, time_end: 42_000, chapter: 1 });
        warmSeg(next, 'r', current);
        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('proximity fires — different chapter regardless of time gap', () => {
        const current = makeSeg({ audio_url: 'http://x/ch1.mp3', chapter: 1, time_end: 7_000 });
        const next = makeSeg({ audio_url: 'http://x/ch2.mp3', chapter: 2, time_start: 8_000 });
        warmSeg(next, 'r', current);
        expect(fetchMock).toHaveBeenCalledOnce();
    });
});

describe('warmSeg — dedupe (30s window)', () => {
    it('two consecutive calls for same key fire once', () => {
        warmSeg(makeSeg(), 'r');
        warmSeg(makeSeg(), 'r');
        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('different reciter is a different key', () => {
        warmSeg(makeSeg(), 'r1');
        warmSeg(makeSeg(), 'r2');
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('different audio_url is a different key', () => {
        warmSeg(makeSeg({ audio_url: 'http://x/a.mp3' }), 'r');
        warmSeg(makeSeg({ audio_url: 'http://x/b.mp3' }), 'r');
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('different byte-bucket (>= 64 KB apart) is a different key', () => {
        // chapter 1 @ 128 kbps → bytes_per_sec = 16000. 4.1s → 65600 bytes (different bucket from 0).
        warmSeg(makeSeg({ time_start: 0, chapter: 1 }), 'r');
        warmSeg(makeSeg({ time_start: 4_100, chapter: 1 }), 'r');
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('re-fires after 30 s window elapses', () => {
        vi.useFakeTimers();
        const start = new Date(2030, 0, 1);
        vi.setSystemTime(start);
        warmSeg(makeSeg(), 'r');
        expect(fetchMock).toHaveBeenCalledOnce();
        vi.setSystemTime(new Date(start.getTime() + 31_000));
        warmSeg(makeSeg(), 'r');
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });
});

describe('warmSeg — fire-and-forget', () => {
    it('does not throw when fetch rejects', () => {
        fetchMock.mockReturnValueOnce(Promise.reject(new Error('boom')));
        expect(() => warmSeg(makeSeg(), 'r')).not.toThrow();
    });
});

describe('warmSeg — audio_url fallback to segAllData.audio_by_chapter', () => {
    // Main-list segs (/api/seg/all/<reciter>) deliberately omit `audio_url`
    // to save wire bytes; FE consumers fall back to `audio_by_chapter[chapter]`.
    // The warmup util must apply the same fallback or the hover + play-next-seg
    // triggers silently no-op on every main-list row.
    it('fires when seg.audio_url is empty but audio_by_chapter has a URL', () => {
        segAllData.set({
            segments: [],
            audio_by_chapter: { '1': 'http://x/ch1.mp3' },
        } as unknown as SegAllResponse);
        warmSeg({ chapter: 1, audio_url: '', time_start: 0, time_end: 1000 } as Segment, 'r');
        expect(fetchMock).toHaveBeenCalledOnce();
        const url = fetchMock.mock.calls[0]?.[0] as string;
        expect(url).toContain(encodeURIComponent('http://x/ch1.mp3'));
    });

    it('no-op when both seg.audio_url and audio_by_chapter miss', () => {
        segAllData.set({
            segments: [],
            audio_by_chapter: {},
        } as unknown as SegAllResponse);
        warmSeg({ chapter: 1, audio_url: '', time_start: 0, time_end: 1000 } as Segment, 'r');
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('proximity skip works via fallback URLs on both seg + current', () => {
        segAllData.set({
            segments: [],
            audio_by_chapter: { '1': 'http://x/ch1.mp3' },
        } as unknown as SegAllResponse);
        const current = { chapter: 1, audio_url: '', time_start: 0, time_end: 5_000 } as Segment;
        const closeNext = { chapter: 1, audio_url: '', time_start: 10_000, time_end: 12_000 } as Segment;
        warmSeg(closeNext, 'r', current);
        expect(fetchMock).not.toHaveBeenCalled();
    });
});

describe('warmChapterStart', () => {
    it('fires bytes=0-65535 when chapter has kbps', () => {
        warmChapterStart('r', 'http://x/ch1.mp3', 1);
        expect(fetchMock).toHaveBeenCalledOnce();
        const init = fetchMock.mock.calls[0]?.[1] as RequestInit & { headers: Record<string, string> };
        expect(init.headers.Range).toBe('bytes=0-65535');
    });

    it('no-op when chapter has no kbps', () => {
        warmChapterStart('r', 'http://x/ch99.mp3', 99);
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('no-op when reciter or url missing', () => {
        warmChapterStart('', 'http://x/ch1.mp3', 1);
        warmChapterStart('r', '', 1);
        warmChapterStart('r', 'http://x/ch1.mp3', null);
        expect(fetchMock).not.toHaveBeenCalled();
    });
});

describe('warmSegChapter', () => {
    const CDN = 'https://cdn.example.com/ch1.mp3';
    const flush = () => new Promise((r) => setTimeout(r, 0));

    beforeEach(() => {
        _resetPlayUrlForTest();
        vi.mocked(shadowPrewarm).mockClear();
    });

    it('direct CDN: probes, shadow-warms the chapter, Range-warms the CDN itself', async () => {
        fetchMock.mockImplementation(() =>
            Promise.resolve(new Response(mp3Head({ tag: 'Info' }) as BodyInit, { status: 206 })));
        warmSegChapter(makeSeg({ audio_url: CDN }), 'r');
        await flush(); await flush();
        expect(shadowPrewarm).toHaveBeenCalledWith(CDN);
        const urls = fetchMock.mock.calls.map((c) => c[0] as string);
        expect(urls).toEqual([CDN, CDN]); // probe, then the 64 KB warm — never the proxy
    });

    it('proxied: no whole-chapter shadow warm, only the 64 KB Range via the proxy', async () => {
        fetchMock.mockImplementation(() => Promise.resolve(new Response(null, { status: 403 })));
        warmSegChapter(makeSeg({ audio_url: CDN }), 'r');
        await flush(); await flush();
        expect(shadowPrewarm).not.toHaveBeenCalled();
        const last = fetchMock.mock.calls.at(-1) as [string, RequestInit & { headers: Record<string, string> }];
        expect(last[0]).toMatch(/^\/api\/seg\/audio-proxy\/r\?url=/);
        expect(last[1].headers.Range).toBe('bytes=0-65535');
    });
});
