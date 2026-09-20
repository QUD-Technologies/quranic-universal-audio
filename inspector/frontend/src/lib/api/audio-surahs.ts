/**
 * Audio-surahs fetcher — wraps ``/api/audio/surahs/<category>/<source>/<slug>``.
 *
 * Returns ``{surahNum → {url, durationMs}}`` for a delivery. ``durationMs``
 * comes from the sidecar so the dashboard's progress bar can show full
 * chapter length even with ``<audio preload="none">`` (the element doesn't
 * fetch MP3 headers until the user hits play).
 */

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
    for (const [k, entry] of Object.entries(raw)) {
        const v = entry as { url: string; duration_ms: number | null };
        out[k] = { url: v.url, durationMs: v.duration_ms };
    }
    _cache.set(key, out);
    return out;
}
