/**
 * Shared shard-fetch data layer (was `tabs/timestamps/services/ts_client.ts`).
 *
 * Relocated under `lib/` so non-timestamps surfaces (the dashboard now-reciting
 * section) can consume it without a cross-tab import — the shared lib must not
 * import from `tabs/*`. The timestamps tab keeps its old import path via a thin
 * re-export barrel at `tabs/timestamps/services/ts_client.ts`. One module =
 * one set of module-level caches, so every surface shares the same fetch.
 *
 * Replaces the per-verse `/api/ts/{reciters,chapters,verses,data,random}`
 * round-trips with one manifest fetch + per-chapter shards. Local-mode
 * Flask and the HF dataset CDN both speak the same shape so the only
 * branch in this module is URL templating against `tsConfig`.
 *
 * Caching:
 *   - Config / manifest / qpc / dk: module-level singletons (load once).
 *   - Shards: small LRU keyed `${reciter}:${chapter}` so back-and-forth
 *     between two reciters keeps both currents resident.
 *
 * Each chapter shard is a temporal `segments[]` array (every recited segment
 * raw, in recitation order). `shardOccasions` splits it into occasions (a verse
 * may recur — loopbacks, re-dos) and `assembleOccasion` builds one occasion's
 * `TsVerseData` with every recited word kept (no dedup). See `occasions.ts`.
 */

import { ApiError, fetchArrayBuffer, fetchJson } from '../api';
import type { TsConfigResponse, TsManifestResponse, TsValidationDoc } from '../types/generated/schemas';
import type {
    TsShardResponse,
    TsVbrResponse,
    TsVerseData,
    TsWord,
} from '../types/ts-client';
import type { ChapterOccasion } from './occasions';
import { decodeTimestampShard } from './compact-shards';
import {
    assembleNative,
    chapterVerseRefs,
    shardOccasions,
} from './native-shards';
import { assembleWord, isWordReading } from './word-shards';

// ---------------------------------------------------------------------------
// Singleton caches
// ---------------------------------------------------------------------------

let _config: Promise<TsConfigResponse> | null = null;
let _manifest: Promise<TsManifestResponse> | null = null;
let _qpc: Promise<Record<string, { text?: string }>> | null = null;
let _qpcVerseIndex: Promise<Map<number, Map<number, number>>> | null = null;
let _dk: Promise<Record<string, { text?: string }>> | null = null;

/** Bounded LRU for chapter shards — covers current + adjacent + two pre-rolls. */
const SHARD_CACHE_SIZE = 4;
const _shards: Map<string, Promise<TsShardResponse>> = new Map();
const _vbrChapters: Map<string, Promise<number[]>> = new Map();

function _shardKey(reciter: string, chapter: number): string {
    return `${reciter}:${chapter}`;
}

function _lruTouch(key: string, value: Promise<TsShardResponse>): void {
    // Map iteration order is insertion order; re-set bumps to "most recent".
    _shards.delete(key);
    _shards.set(key, value);
    while (_shards.size > SHARD_CACHE_SIZE) {
        const oldest = _shards.keys().next().value;
        if (oldest === undefined) break;
        _shards.delete(oldest);
    }
}

// ---------------------------------------------------------------------------
// Gzip + JSON fetch
// ---------------------------------------------------------------------------

/**
 * Fetch a gzipped JSON body (manifest, shard, resource) and decode it.
 *
 * Bodies are pre-gzipped at rest with no `Content-Encoding: gzip` header
 * (matches HF's static file serving), so we decompress explicitly via
 * `DecompressionStream('gzip')`. Falls back to a clear error message if
 * the browser is too old (Safari < 16.4 lacks DecompressionStream).
 */
async function _fetchGzipJson<T>(url: string): Promise<T> {
    if (typeof DecompressionStream === 'undefined') {
        throw new Error(
            'This browser is too old for the Timestamps tab — please update to '
            + 'a recent Chrome, Firefox, Edge, or Safari (16.4+).',
        );
    }
    const buf = await fetchArrayBuffer(url);
    const decompressed = new Response(
        new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip')),
    );
    return (await decompressed.json()) as T;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Fetch `/api/ts/config` once. Resolves on first call, returns the cache afterwards. */
export function loadConfig(): Promise<TsConfigResponse> {
    if (!_config) {
        _config = fetchJson<TsConfigResponse>('/api/ts/config');
    }
    return _config;
}

/**
 * Fetch the gzipped manifest once. Resolves with the parsed body — schema
 * matches `TsManifestResponse`. Cached forever client-side.
 */
export async function loadManifest(): Promise<TsManifestResponse> {
    if (!_manifest) {
        _manifest = (async () => {
            const cfg = await loadConfig();
            return _fetchGzipJson<TsManifestResponse>(cfg.manifest_url);
        })();
    }
    return _manifest;
}

/**
 * Fetch a per-chapter Brotli shard. HTTP content decoding is handled by the
 * browser; the compact JSON is expanded once before entering the LRU. The URL is templated against
 * `tsConfig.shard_url_template` (`/api/ts/shard/...`). Concurrent calls dedupe
 * via the in-flight Promise.
 */
export async function loadChapterShard(
    reciter: string,
    chapter: number,
): Promise<TsShardResponse> {
    const key = _shardKey(reciter, chapter);
    const existing = _shards.get(key);
    if (existing) {
        // Touch so it stays resident across LRU evictions.
        _lruTouch(key, existing);
        return existing;
    }
    const promise = (async () => {
        const cfg = await loadConfig();
        const url = cfg.shard_url_template
            .replace('{reciter}', encodeURIComponent(reciter))
            .replace('{chapter}', String(chapter));
        return decodeTimestampShard(await fetchJson<unknown>(url));
    })();
    _lruTouch(key, promise);
    // If the fetch fails, drop the entry so the next call retries.
    promise.catch(() => _shards.delete(key));
    return promise;
}

/**
 * Fetch & cache `qpc_hafs` (Uthmani text) keyed by `surah:ayah:word`.
 * URL comes from the manifest's `resources.qpc_hafs`, joined with
 * `dataset_base_url`.
 */
export async function loadQpc(): Promise<Record<string, { text?: string }>> {
    if (!_qpc) {
        _qpc = (async () => {
            const m = await loadManifest();
            const url = _resourceUrl(m, 'qpc_hafs');
            return _fetchGzipJson<Record<string, { text?: string }>>(url);
        })();
    }
    return _qpc;
}

/**
 * Reference word counts per verse, derived once from the full `qpc_hafs`
 * singleton: `chapter → (ayah → wordCount)`. The mushaf reference for "does
 * this verse have every word" — the chapter's verse count is the max ayah key.
 * Memoized (one O(qpc) scan), so the filmstrip's coverage diff is free after the
 * qpc the animation already loads. Word count = max word index seen (robust to a
 * gap in qpc keys).
 */
export async function loadQpcVerseIndex(): Promise<Map<number, Map<number, number>>> {
    if (!_qpcVerseIndex) {
        _qpcVerseIndex = (async () => {
            const qpc = await loadQpc();
            const index = new Map<number, Map<number, number>>();
            for (const loc of Object.keys(qpc)) {
                const parts = loc.split(':');
                if (parts.length !== 3) continue;
                const ch = parseInt(parts[0]!, 10);
                const ayah = parseInt(parts[1]!, 10);
                const word = parseInt(parts[2]!, 10);
                if (!ch || !ayah || !word) continue;
                let byAyah = index.get(ch);
                if (!byAyah) {
                    byAyah = new Map();
                    index.set(ch, byAyah);
                }
                const prev = byAyah.get(ayah) ?? 0;
                if (word > prev) byAyah.set(ayah, word);
            }
            return index;
        })();
    }
    return _qpcVerseIndex;
}

/**
 * Fetch & cache `digital_khatt_v2` (display text) keyed by `surah:ayah:word`.
 * Same resolution path as `loadQpc`.
 */
export async function loadDk(): Promise<Record<string, { text?: string }>> {
    if (!_dk) {
        _dk = (async () => {
            const m = await loadManifest();
            const url = _resourceUrl(m, 'digital_khatt');
            return _fetchGzipJson<Record<string, { text?: string }>>(url);
        })();
    }
    return _dk;
}

function _resourceUrl(manifest: TsManifestResponse, key: string): string {
    const filename = manifest.resources?.[key];
    if (!filename) {
        throw new Error(`Manifest missing required resource: ${key}`);
    }
    const base = (manifest.dataset_base_url || '').replace(/\/$/, '');
    if (!base) return filename; // local mode — filename is already an absolute path.
    return `${base}/${filename.replace(/^\//, '')}`;
}

/** Read VBR chapter metadata from a manifest, or null when the manifest predates it. */
export function vbrChaptersFromManifest(
    manifest: TsManifestResponse,
    reciter: string,
): number[] | null {
    const raw = manifest.reciters?.[reciter]?.vbr_chapters;
    return Array.isArray(raw)
        ? raw.filter((n): n is number => Number.isInteger(n)).sort((a, b) => a - b)
        : null;
}

/** Resolve VBR chapters, preferring manifest metadata and falling back to Flask. */
export async function resolveVbrChaptersForReciter(
    reciter: string,
    manifest: TsManifestResponse,
): Promise<number[]> {
    const fromManifest = vbrChaptersFromManifest(manifest, reciter);
    if (fromManifest !== null) return fromManifest;
    const data = await fetchJson<TsVbrResponse>(`/api/ts/vbr/${encodeURIComponent(reciter)}`);
    return Array.isArray(data.vbr_chapters)
        ? data.vbr_chapters.filter((n): n is number => Number.isInteger(n)).sort((a, b) => a - b)
        : [];
}

/** Fetch/cache the per-reciter VBR chapter map for timestamp playback + peaks. */
export async function loadVbrChapters(reciter: string): Promise<number[]> {
    if (!reciter) return [];
    const existing = _vbrChapters.get(reciter);
    if (existing) return existing;
    const promise = (async () => {
        try {
            const manifest = await loadManifest();
            return await resolveVbrChaptersForReciter(reciter, manifest);
        } catch (e) {
            console.warn('Failed to load TS VBR metadata:', reciter, e);
            return [];
        }
    })();
    _vbrChapters.set(reciter, promise);
    return promise;
}

/**
 * Authoritative per-reciter audio metadata, sourced from the manifest's reciter
 * block — the live source the backend rebuilds against the current audio-manifest
 * sidecar. The shard's slim `_meta` carries no audio routing fields.
 */
export interface TsReciterAudio {
    /** Short form (`'by_surah'` / `'by_ayah'`) — the contract the FE drives
     *  audio routing + offset logic from. Per-chapter audio URLs are NOT in the
     *  manifest; resolve them from the canonical `/api/audio/surahs` endpoint. */
    audio_category: 'by_surah' | 'by_ayah';
}

/** Look up a reciter's authoritative audio metadata from a loaded manifest.
 *  Returns null when the manifest doesn't advertise the reciter (caller should
 *  treat as "audio unavailable"). */
export function reciterAudioFromManifest(
    manifest: TsManifestResponse,
    reciter: string,
): TsReciterAudio | null {
    const block = manifest.reciters?.[reciter];
    if (!block) return null;
    return { audio_category: block.audio_category };
}

/**
 * Resolve the URL the `<audio>` element should actually load for a TS verse.
 *
 * by_surah audio is routed through the audio-proxy so the bucket-mounted file
 * (or CDN stream-through) is served via sendfile + Range/304; by_ayah / already
 * proxied URLs pass through. ONE source of truth so the prewarm path and the
 * real-play path produce byte-identical URLs (otherwise the shadow-audio prewarm
 * fills a different cache key than playback reads → silent miss).
 */
export function tsPlayUrl(reciter: string, audioUrl: string, audioCategory: string): string {
    return (audioCategory === 'by_surah_audio' && audioUrl && !audioUrl.startsWith('/api/'))
        ? `/api/seg/audio-proxy/${reciter}?url=${encodeURIComponent(audioUrl)}`
        : audioUrl;
}

/**
 * Verse-level ts-validation flags for the Timestamps-tab accordion.
 *
 * Backed by ``reciters/<slug>/ts_validation.json`` (multi-beam generate-job
 * output). 403 → caller lacks ``timestamps.view_validation``; 404 → caller
 * holds it but the reciter isn't viewable (unreleased and the caller doesn't
 * hold ``timestamps.view_unreleased``). Both return ``null`` so the panel
 * stays hidden. A viewable reciter with no flags returns an empty ``verses``
 * map. Only call this when the user holds the view-validation capability
 * (avoids a wasted bucket read per reciter-load for public users on the
 * single worker).
 */
export async function loadTsValidation(reciter: string): Promise<TsValidationDoc | null> {
    if (!reciter) return null;
    try {
        const resp = await fetch(`/api/ts/validation/${encodeURIComponent(reciter)}`);
        if (!resp.ok) return null; // 404 (not viewable) or transient — hide panel
        return (await resp.json()) as TsValidationDoc;
    } catch {
        return null;
    }
}

// ---------------------------------------------------------------------------
// Reading assembly — native cells or proxy-timed words, per the shard profile
// ---------------------------------------------------------------------------

export { chapterVerseRefs, shardOccasions };

/**
 * True when this occasion carries proxy-timed words rather than phonemizer
 * cells. Read off the readings rather than the shard so the two assemble
 * helpers keep their existing signatures — callers hold occasions, not shards.
 */
const isWordOccasion = (members: ChapterOccasion[]): boolean =>
    members.some((member) => member.readings.some((one) => isWordReading(one.reading)));

export function assembleOccasion(
    reciter: string,
    occasion: ChapterOccasion,
    qpc: Record<string, { text?: string }>,
    dk: Record<string, { text?: string }>,
    reciterAudio: TsReciterAudio,
    chapterAudioUrl: string,
): TsVerseData {
    const members = [occasion];
    const shape = {
        reciter,
        members,
        verseRef: occasion.ref,
        audioCategory: reciterAudio.audio_category,
        audioUrl: chapterAudioUrl,
    };
    return isWordOccasion(members)
        ? assembleWord(shape)
        : assembleNative({ ...shape, qpc, dk });
}

export function assembleWaslGroup(
    reciter: string,
    members: ChapterOccasion[],
    verseRef: string,
    qpc: Record<string, { text?: string }>,
    dk: Record<string, { text?: string }>,
    reciterAudio: TsReciterAudio,
    chapterAudioUrl: string,
): TsVerseData {
    const shape = {
        reciter,
        members,
        verseRef,
        audioCategory: reciterAudio.audio_category,
        audioUrl: chapterAudioUrl,
    };
    return isWordOccasion(members)
        ? assembleWord(shape)
        : assembleNative({ ...shape, qpc, dk });
}

// ---------------------------------------------------------------------------
// Random target picking
// ---------------------------------------------------------------------------

export interface TsRandomTarget {
    reciter: string;
    chapter: number;
    verseRef: string;
}

/**
 * Pick a random `(reciter, chapter, verseRef)` from the manifest plus a
 * peek at one shard. The "any-reciter" form picks a reciter at random;
 * the "same-reciter" form pins to `reciter`.
 *
 * Returns null when the manifest has no eligible reciters (e.g. empty
 * local data tree). Caller is responsible for displaying a user-facing
 * empty state.
 *
 * Note: pre-rolling for lookahead requires fetching the resulting
 * shard so the verseRef list is known. The random target therefore
 * resolves only after the chosen shard is loaded.
 */
const RANDOM_TARGET_MAX_TRIES = 5;

export async function getRandomTarget(opts: { reciter?: string } = {}): Promise<TsRandomTarget | null> {
    const m = await loadManifest();
    const reciters = m.reciters ?? {};
    const slugs = Object.keys(reciters);
    if (slugs.length === 0) return null;

    const reciter = opts.reciter ?? slugs[Math.floor(Math.random() * slugs.length)]!;
    const block = reciters[reciter];
    const blockChapters = block?.ts_chapters ?? [];
    if (!block || blockChapters.length === 0) return null;

    // Retry on shard 404 / empty-shard so a single stale manifest entry (e.g.
    // a chapter listed in `ts_chapters` whose shard file vanished from the
    // bucket between manifest build and this fetch) doesn't break the random
    // button. Each retry picks a different unseen chapter.
    const tried = new Set<number>();
    for (let i = 0; i < RANDOM_TARGET_MAX_TRIES; i++) {
        const remaining = blockChapters.filter((c) => !tried.has(c));
        if (remaining.length === 0) return null;
        const chapter = remaining[Math.floor(Math.random() * remaining.length)]!;
        tried.add(chapter);
        try {
            const shard = await loadChapterShard(reciter, chapter);
            const refs = chapterVerseRefs(shard);
            if (refs.length === 0) continue;
            const verseRef = refs[Math.floor(Math.random() * refs.length)]!;
            return { reciter, chapter, verseRef };
        } catch (e) {
            if (e instanceof ApiError && e.status === 404) continue;
            throw e;
        }
    }
    return null;
}

// ---------------------------------------------------------------------------
// Test seam — clears all module-level caches so unit tests stay isolated.
// ---------------------------------------------------------------------------

export function _resetForTests(): void {
    _config = null;
    _manifest = null;
    _qpc = null;
    _qpcVerseIndex = null;
    _dk = null;
    _shards.clear();
    _vbrChapters.clear();
}
