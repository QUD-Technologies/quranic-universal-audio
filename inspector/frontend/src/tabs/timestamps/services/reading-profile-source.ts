/**
 * Loads a recitation's reading profile from `GET /api/ts/readings/<slug>`.
 *
 * The server builds the summary from the delivery's shards and stores it, so
 * this is one small request. Results are cached per delivery for the session;
 * a failed load is not cached.
 */

import type { TsReadingsDoc } from '../../../lib/types/generated/schemas';
import { buildProfile, type ProfileGroup } from '../utils/reading-profile';

const cache = new Map<string, Promise<ProfileGroup[]>>();

async function load(slug: string): Promise<ProfileGroup[]> {
    const res = await fetch(`/api/ts/readings/${encodeURIComponent(slug)}`);
    if (!res.ok) throw new Error(`readings ${slug}: HTTP ${res.status}`);
    const doc = (await res.json()) as TsReadingsDoc;
    return buildProfile(doc.rows ?? []);
}

export function loadReadingProfile(slug: string): Promise<ProfileGroup[]> {
    const existing = cache.get(slug);
    if (existing) return existing;
    const promise = load(slug);
    cache.set(slug, promise);
    promise.catch((err: unknown) => {
        cache.delete(slug);
        console.error(`[reading-profile] ${slug} failed to load`, err);
    });
    return promise;
}
