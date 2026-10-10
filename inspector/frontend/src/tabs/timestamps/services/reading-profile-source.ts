/**
 * Loads what the readings panel shows for a recitation: its reading choices
 * from `GET /api/ts/readings/<slug>` and its madd, ghunnah and pause durations
 * from `GET /api/ts/profile/<slug>`.
 *
 * The server builds the readings summary from the delivery's shards and stores
 * it, so each is one small request. Results are cached per delivery for the
 * session; a failed load is not cached. The recitation profile is optional: a
 * delivery without one, or a failed load, gives no sections.
 */

import type { TsReadingsDoc, TsRecitationProfile } from '../../../lib/types/generated/schemas';
import { buildProfile, type ProfileGroup } from '../utils/reading-profile';
import { profileSections, type ProfileSection } from '../utils/recitation-profile';

const readingsCache = new Map<string, Promise<ProfileGroup[]>>();
const profileCache = new Map<string, Promise<ProfileSection[]>>();

async function fetchJson<T>(path: string, slug: string): Promise<T> {
    const res = await fetch(`/api/ts/${path}/${encodeURIComponent(slug)}`);
    if (!res.ok) throw new Error(`${path} ${slug}: HTTP ${res.status}`);
    return (await res.json()) as T;
}

function cached<T>(cache: Map<string, Promise<T>>, slug: string, load: () => Promise<T>): Promise<T> {
    const existing = cache.get(slug);
    if (existing) return existing;
    const promise = load();
    cache.set(slug, promise);
    promise.catch((err: unknown) => {
        cache.delete(slug);
        console.error(`[reading-profile] ${slug} failed to load`, err);
    });
    return promise;
}

export function loadReadingProfile(slug: string): Promise<ProfileGroup[]> {
    return cached(readingsCache, slug, async () =>
        buildProfile((await fetchJson<TsReadingsDoc>('readings', slug)).rows ?? []));
}

/** The recitation profile sections; empty when the delivery has none or the load failed. */
export function loadRecitationProfile(slug: string): Promise<ProfileSection[]> {
    return cached(profileCache, slug, async () =>
        profileSections(await fetchJson<TsRecitationProfile | null>('profile', slug)))
        .catch(() => []);
}
