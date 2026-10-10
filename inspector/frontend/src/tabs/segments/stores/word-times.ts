/**
 * Segments tab — stored word times, for the word-by-word highlight while a card plays.
 *
 * Every timed segment's word intervals come from the stored segment times
 * (`GET /api/seg/word-times/<reciter>/<chapter>`, chapter-audio ms), fetched once
 * per chapter the first time one of its cards renders. Each carries the span it was
 * timed on, so a card trimmed since is treated as untimed. A save re-times its
 * chapter on the server in the background, so playing a card without valid times
 * refetches its chapter (at most once per `REFETCH_MS`). Review samples keep their
 * own `word_timings`.
 */

import { get, writable } from 'svelte/store';

import { fetchJsonOrNull } from '../../../lib/api';
import type {
    SegStoredTimes,
    SegStoredWordTime,
    SegWordTimesResponse,
} from '../../../lib/types/generated/schemas';

/** Shortest gap between two refetches of one chapter. */
const REFETCH_MS = 15_000;

/** chapter → segment uid → stored times, for the current reciter. */
export const wordTimes = writable<Record<string, Record<string, SegStoredTimes>>>({});

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

/** The card's stored word intervals, or `[]` when it has none or was trimmed since. */
export function timesForCard(
    all: Record<string, Record<string, SegStoredTimes>>,
    chapter: number | string | null | undefined,
    seg: { segment_uid?: string | null; time_start: number; time_end: number },
): SegStoredWordTime[] {
    if (chapter == null || !seg.segment_uid) return [];
    const held = all[String(chapter)]?.[seg.segment_uid];
    if (!held || held.start_ms !== seg.time_start || held.end_ms !== seg.time_end) return [];
    return held.words;
}

/** `timesForCard` against the current store. */
export function storedWordTimes(
    chapter: number | string | null | undefined,
    seg: { segment_uid?: string | null; time_start: number; time_end: number },
): SegStoredWordTime[] {
    return timesForCard(get(wordTimes), chapter, seg);
}

/** Drop every chapter's word times (reciter switch / stale reload). */
export function clearWordTimes(reciter = ''): void {
    _reciter = reciter;
    _fetchedAt.clear();
    _inflight.clear();
    wordTimes.set({});
}
