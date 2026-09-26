/**
 * Segments tab — Auto Split cursor map (client-side preload cache).
 *
 * Background: the Auto Split button (cross-verse, repetitions, missed-waqf and
 * hidden-pause accordions) resolves its cursor positions from offline-precomputed
 * sidecars (`auto_split_v1.json` plus the `missed_waqf_v1.json` /
 * `hidden_pause_v1.json` entries that carry refs, keyed by `segment_uid`). The
 * server holds the whole
 * map in process memory after the first read, but the FE historically re-POSTed
 * per click — one network round trip every click. On the deployed Space that
 * round trip (RTT + single-worker serialization) is the dominant latency.
 *
 * This store preloads the whole map ONCE (lazily, on auto-split accordion open)
 * via `GET /api/seg/auto-split/<reciter>`, so each click becomes a zero-network
 * O(1) lookup. Misses ("uid not in map") are classified client-side from the
 * segment's own `matched_ref` / `wrap_word_ranges`, so the per-uid POST's O(n)
 * `_find_segment_kind` scan is avoided too.
 *
 * Lifetime: reciter-scoped. The map content is stable across a session — the
 * sidecar is offline-computed, and edits only mint new uids that are legitimate
 * misses (the offline pass never saw them), so no per-edit invalidation is
 * needed. Cleared on reciter switch / stale reload via `clearAutoSplitMap`
 * (wired into `clear-per-reciter-state.ts`).
 */

import { get, writable } from 'svelte/store';

import { fetchJsonOrNull } from '../../../lib/api';

export interface AutoSplitEntry {
    /** Absolute-ms cut positions (N-1 entries for N sections). */
    cursors: number[];
    /** Per-section refs (N entries). */
    refs: string[];
    kind: 'cross_verse' | 'repetition' | 'missed_waqf' | 'hidden_pause';
}

export type AutoSplitMap = Record<string, AutoSplitEntry>;

/** Loaded map for the current reciter, or null when nothing is loaded.
 *  Exposed mainly for tests / debugging — call sites use the helpers below. */
export const autoSplitMap = writable<AutoSplitMap | null>(null);

let _loadedReciter: string | null = null;
let _inflight: Promise<AutoSplitMap | null> | null = null;
let _inflightReciter: string | null = null;

/**
 * Lazily load the whole Auto Split map for `reciter`. Deduped: concurrent
 * callers for the same reciter share one in-flight fetch, and a reciter whose
 * map is already loaded resolves synchronously. Returns the map, or `null` if
 * the fetch failed — callers fall back to the per-uid POST in that case.
 */
export async function ensureAutoSplitMap(reciter: string): Promise<AutoSplitMap | null> {
    if (!reciter) return null;
    if (_loadedReciter === reciter) return get(autoSplitMap);
    if (_inflight && _inflightReciter === reciter) return _inflight;

    _inflightReciter = reciter;
    _inflight = (async (): Promise<AutoSplitMap | null> => {
        const resp = await fetchJsonOrNull<{ by_uid: AutoSplitMap }>(
            `/api/seg/auto-split/${encodeURIComponent(reciter)}`,
        );
        const map = resp?.by_uid ?? null;
        // Guard against a reciter switch that landed mid-flight: only publish
        // if this is still the reciter callers asked for.
        if (map && _inflightReciter === reciter) {
            autoSplitMap.set(map);
            _loadedReciter = reciter;
        }
        if (_inflightReciter === reciter) {
            _inflight = null;
            _inflightReciter = null;
        }
        return map;
    })();
    return _inflight;
}

/** Drop the cached map. Called on reciter switch / stale reload. */
export function clearAutoSplitMap(): void {
    autoSplitMap.set(null);
    _loadedReciter = null;
    _inflight = null;
    _inflightReciter = null;
}
