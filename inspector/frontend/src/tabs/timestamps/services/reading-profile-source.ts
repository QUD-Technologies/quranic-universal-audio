/**
 * Loads a recitation's reading profile from its chapter shards.
 *
 * Reads only the chapters that can hold a shown selector, a few at a time,
 * keeps the occurrences and drops the shard. A first shard older than v15 ends
 * the load early: such a recitation records no reading choices. Results are
 * cached per delivery for the session; a failed load is not cached.
 */

import { CHOICE_CHAPTERS } from '../domain/reading-choices';
import {
    buildProfile,
    type ChoiceHit,
    MIN_SCHEMA_VERSION,
    type ProfileGroup,
    type RawChoiceShard,
    shardChoices,
} from '../utils/reading-profile';
import { loadConfig, loadManifest } from './ts_client';

const CONCURRENCY = 4;

export type ProgressHandler = (done: number, total: number) => void;

const cache = new Map<string, Promise<ProfileGroup[]>>();

async function fetchShard(template: string, slug: string, chapter: number): Promise<RawChoiceShard> {
    const url = template
        .replace('{reciter}', encodeURIComponent(slug))
        .replace('{chapter}', String(chapter));
    const res = await fetch(url);
    if (!res.ok) throw new Error(`shard ${slug}/${chapter}: HTTP ${res.status}`);
    return (await res.json()) as RawChoiceShard;
}

async function chaptersOf(slug: string): Promise<number[]> {
    const manifest = await loadManifest();
    const own = manifest.reciters?.[slug]?.ts_chapters;
    const have = new Set(Array.isArray(own) ? own : CHOICE_CHAPTERS);
    return CHOICE_CHAPTERS.filter((chapter) => have.has(chapter));
}

async function load(slug: string, onProgress?: ProgressHandler): Promise<ProfileGroup[]> {
    const [config, chapters] = await Promise.all([loadConfig(), chaptersOf(slug)]);
    const template = config.shard_url_template;
    if (!chapters.length) return [];

    const first = await fetchShard(template, slug, chapters[0]!);
    if ((first._meta?.schema_version ?? 0) < MIN_SCHEMA_VERSION) return [];
    const hits: ChoiceHit[] = shardChoices(first);
    let done = 1;
    onProgress?.(done, chapters.length);

    const queue = chapters.slice(1);
    const worker = async (): Promise<void> => {
        for (let chapter = queue.shift(); chapter !== undefined; chapter = queue.shift()) {
            hits.push(...shardChoices(await fetchShard(template, slug, chapter)));
            onProgress?.(++done, chapters.length);
        }
    };
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    return buildProfile(hits);
}

export function loadReadingProfile(slug: string, onProgress?: ProgressHandler): Promise<ProfileGroup[]> {
    const existing = cache.get(slug);
    if (existing) return existing;
    const promise = load(slug, onProgress);
    cache.set(slug, promise);
    promise.catch((err: unknown) => {
        cache.delete(slug);
        console.error(`[reading-profile] ${slug} failed to load`, err);
    });
    return promise;
}
