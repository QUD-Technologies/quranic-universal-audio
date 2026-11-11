/**
 * Audio-surahs fetcher — wraps ``/api/audio/surahs/<category>/<source>/<slug>``.
 *
 * Returns ``{surahNum → {url, durationMs}}`` for a delivery. ``durationMs``
 * comes from the sidecar so the dashboard's progress bar can show full
 * chapter length even with ``<audio preload="none">`` (the element doesn't
 * fetch MP3 headers until the user hits play). Each chapter's manifest
 * ``size_bytes`` is registered with ``play-url`` so the dashboard / Timestamps
 * player plays the CDN directly only while the live file still matches.
 */

import { registerExpectedSizes } from '../playback/play-url';
import type { AudioSurahsResponse } from '../types/generated/schemas';

export interface SurahEntry {
    url: string;
    durationMs: number | null;
}

const _cache: Map<string, Record<string, SurahEntry>> = new Map();

export async function fetchSurahsForDelivery(
    source: string,
    slug: string,
    signal?: AbortSignal,
): Promise<Record<string, SurahEntry>> {
    const key = `by_surah/${source}/${slug}`;
    const cached = _cache.get(key);
    if (cached) return cached;
    const resp = await fetch(`/api/audio/surahs/${key}`, { signal });
    if (!resp.ok) {
        throw new Error(`fetchSurahsForDelivery: HTTP ${resp.status}`);
    }
    const data = (await resp.json()) as AudioSurahsResponse;
    const raw = data.surahs ?? {};
    const out: Record<string, SurahEntry> = {};
    const sizes: Record<string, number | null> = {};
    for (const [k, v] of Object.entries(raw)) {
        out[k] = { url: v.url, durationMs: v.duration_ms };
        sizes[v.url] = v.size_bytes;
    }
    registerExpectedSizes(sizes);
    _cache.set(key, out);
    return out;
}
