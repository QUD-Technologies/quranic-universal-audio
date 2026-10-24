/**
 * Quran reference data (`dk_words` + `verse_word_counts` + the verse-marker
 * prefix) lives behind one static, content-hashed endpoint per edition instead
 * of being shipped on every reciter switch. Within an edition the bundle is
 * identical for every user, reciter, chapter, and session, so we fetch it once
 * per browser and share it across tabs via a Svelte store.
 *
 * Boot flow:
 *   1. `loadQuranRefs(riwayah)` is called from `SegmentsTab` / reciter-actions.
 *   2. Hit `/api/static/quran-refs/version?riwayah=…` (no-cache) for the hash.
 *   3. Try `sessionStorage` first — same edition + hash means we have the bytes.
 *   4. Otherwise fetch `/api/static/quran-refs.json?riwayah=…&v=<hash>`.
 *   5. Populate the `quranRefs` store + cache by (edition, hash).
 *
 * The store holds ONE edition at a time — the tab shows one delivery — and is
 * cleared the moment a different riwayah is requested. Serving the outgoing
 * edition's coordinates while the incoming one loads would render a segment
 * against the wrong verse geometry, which is exactly the class of error a
 * reviewer would then save.
 */
import { type Readable,writable } from 'svelte/store';

import { fetchJson } from '../api';
import { DEFAULT_RIWAYAH, type InspectorRiwayah } from '../riwayat';

export interface QuranRefs {
    /** SDK slug of the edition these coordinates belong to. */
    riwayah: string;
    dk_words: Record<string, string>;
    verse_word_counts: Record<string, number>;
    /**
     * Glyph to put before an Arabic-Indic verse number. U+06DD for Hafs, whose
     * Digital Khatt font needs the ornament sent alongside the digits; empty
     * for the packaged QPC fonts, which decorate the digits themselves.
     */
    verse_marker_prefix: string;
    /** Arabic text of each opener special (`Basmala`, `Isti'adha`) in this edition, keyed by
     *  its `matched_ref` name — what a special row shows. */
    special_texts: Record<string, string>;
}

const _store = writable<QuranRefs | null>(null);
/** Single store consumed everywhere — null until the bundle is fetched. */
export const quranRefs: Readable<QuranRefs | null> = _store;

let _inflight: Promise<void> | null = null;
let _loaded: InspectorRiwayah | null = null;

const SS_PREFIX = 'quran-refs:';

const cacheKey = (riwayah: InspectorRiwayah, hash: string) => `${SS_PREFIX}${riwayah}:${hash}`;

function _readSessionCache(key: string): QuranRefs | null {
    try {
        const raw = sessionStorage.getItem(key);
        if (!raw) return null;
        return JSON.parse(raw) as QuranRefs;
    } catch {
        return null;
    }
}

function _writeSessionCache(key: string, refs: QuranRefs): void {
    try {
        // Drop entries under prior hashes AND prior editions so a long-running
        // session doesn't accumulate ~3 MB bundles across deploys and switches.
        for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
            const k = sessionStorage.key(i);
            if (k && k.startsWith(SS_PREFIX) && k !== key) {
                sessionStorage.removeItem(k);
            }
        }
        sessionStorage.setItem(key, JSON.stringify(refs));
    } catch {
        // QuotaExceeded or storage disabled — non-fatal, in-memory store still works.
    }
}

/**
 * Load (or revalidate) the Quran-refs bundle for one edition. Idempotent —
 * repeated calls for the edition already loading share one in-flight promise;
 * a call for a DIFFERENT edition supersedes it and clears the store first.
 */
export function loadQuranRefs(riwayah: InspectorRiwayah = DEFAULT_RIWAYAH): Promise<void> {
    if (_inflight && _loaded === riwayah) return _inflight;
    if (_loaded !== riwayah) _store.set(null);
    _loaded = riwayah;
    const query = `riwayah=${encodeURIComponent(riwayah)}`;
    _inflight = (async () => {
        try {
            const { version } = await fetchJson<{ version: string }>(
                `/api/static/quran-refs/version?${query}`,
            );
            const key = cacheKey(riwayah, version);
            const cached = _readSessionCache(key);
            if (cached) {
                if (_loaded === riwayah) _store.set(cached);
                return;
            }
            const refs = await fetchJson<QuranRefs>(
                `/api/static/quran-refs.json?${query}&v=${encodeURIComponent(version)}`,
            );
            if (!refs || typeof refs !== 'object' || !refs.dk_words) {
                console.error('quran-refs: malformed payload', refs);
                return;
            }
            _writeSessionCache(key, refs);
            // A newer switch may have landed while this was in flight; the last
            // requested edition wins, and this response is simply cached.
            if (_loaded === riwayah) _store.set(refs);
        } catch (e) {
            console.error('quran-refs load failed', e);
            // Leave store at `null`; consumers already tolerate that state
            // (same as pre-segAllData hydration). Next reciter switch retries.
            _inflight = null;
        }
    })();
    return _inflight;
}

/**
 * Drop the loaded bundle. Called when the delivery names an edition this build
 * cannot serve: leaving the previous delivery's words in the store renders one
 * edition's coordinates under another's, which is the whole failure this
 * feature exists to prevent.
 */
export function clearQuranRefs(): void {
    _loaded = null;
    _inflight = null;
    _store.set(null);
}

/** Test-only: drop the store + in-flight state so each case starts clean. */
export function _resetQuranRefs(): void {
    _store.set(null);
    _inflight = null;
    _loaded = null;
}
