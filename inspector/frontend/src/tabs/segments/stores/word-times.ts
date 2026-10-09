/**
 * Segments tab — stored word times, for the word-by-word highlight while a card plays.
 *
 * Every timed segment's word intervals come from the stored segment times
 * (`GET /api/seg/word-times/<reciter>/<chapter>`, chapter-audio ms), fetched once
 * per chapter the first time one of its cards renders. A segment whose stored times
 * no longer match it is absent; a save re-times its chapter on the server in the
 * background, so playing a segment that has no times refetches its chapter (at
 * most once per `REFETCH_MS`). Review samples keep their own `word_timings`.
 */

import { get, writable } from 'svelte/store';

import { fetchJsonOrNull } from '../../../lib/api';
import type {
    SegStoredWordTime,
    SegWordTimesResponse,
} from '../../../lib/types/generated/schemas';

/** Shortest gap between two refetches of one chapter. */
const REFETCH_MS = 15_000;

/** chapter → segment uid → word intervals, for the current reciter. */
export const wordTimes = writable<Record<string, Record<string, SegStoredWordTime[]>>>({});

let _reciter = '';
const _fetchedAt = new Map<string, number>();
const _inflight = new Map<string, Promise<void>>();

async function _load(reciter: string, chapter: string): Promise<void> {
    _fetchedAt.set(chapter, Date.now());
    const resp = await fetchJsonOrNull<SegWordTimesResponse>(
        `/api/seg/word-times/${encodeURIComponent(reciter)}/${encodeURIComponent(chapter)}`,
    );
    if (resp && _reciter === reciter) {
        wordTimes.update((all) => ({ ...all, [chapter]: resp.segments }));
    }
}

/** Load `chapter`'s word times unless loaded (or, with `refresh`, loaded recently). */
export function ensureWordTimes(
    reciter: string,
    chapter: number | string | null | undefined,
    refresh = false,
): void {
    if (!reciter || chapter == null || chapter === '') return;
    const key = String(chapter);
    if (reciter !== _reciter) clearWordTimes(reciter);
    if (_inflight.has(key)) return;
    const last = _fetchedAt.get(key);
    if (last !== undefined && (!refresh || Date.now() - last < REFETCH_MS)) return;
    const job = _load(reciter, key)
        .catch(() => undefined)
        .finally(() => _inflight.delete(key));
    _inflight.set(key, job);
}

/** The stored word intervals of the segment `uid` in `chapter`, or `[]`. */
export function storedWordTimes(
    chapter: number | string | null | undefined,
    uid: string | null | undefined,
): SegStoredWordTime[] {
    if (chapter == null || !uid) return [];
    return get(wordTimes)[String(chapter)]?.[uid] ?? [];
}

/** Drop every chapter's word times (reciter switch / stale reload). */
export function clearWordTimes(reciter = ''): void {
    _reciter = reciter;
    _fetchedAt.clear();
    _inflight.clear();
    wordTimes.set({});
}
