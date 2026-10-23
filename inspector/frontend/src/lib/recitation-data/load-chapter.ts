/**
 * One-call chapter loader for the recitation-animation surfaces.
 *
 * Wraps the shard fetch + per-occasion assembly + chapter-absolute rebuild into
 * a single `loadChapterRecitation(reciter, chapter)` that returns the flat
 * `AnimUnit[]` + per-ayah boundaries the line animation and filmstrip consume —
 * or `null` when the reciter/chapter has no timestamps shard (caller hides the
 * section). Keeps the dashboard consumer free of any `tabs/*` import.
 *
 * Every occasion's words feed the build (no dedup), so each word's `intervals`
 * cover every recited occurrence (loopbacks / re-dos) — the recitation locator
 * spans the full chapter audio and the highlight travels back into a re-recited
 * verse instead of freezing on a single take.
 *
 * Scope guard: `buildChapterRecitation` recovers chapter-absolute word times by
 * adding back each occasion's offset, which is only correct for `by_surah`
 * reciters (one shared chapter file). `by_ayah` reciters have per-verse files
 * whose concatenation offsets aren't known here, so we return `null` for them.
 */

import {
    type AssembledVerse,
    buildChapterRecitation,
} from '../recitation-animation/chapter-words';
import type { AnimUnit, AyahBoundary } from '../recitation-animation/types';
import { type ChapterCoverage, computeChapterCoverage } from './coverage';

export type { ChapterCoverage };
import {
    assembleOccasion,
    loadChapterShard,
    loadDk,
    loadManifest,
    loadQpc,
    loadQpcVerseIndex,
    reciterAudioFromManifest,
    shardOccasions,
} from './ts-source';
import { DEFAULT_SDK_RIWAYAH } from '../riwayat';
import { isWordShard } from '../types/ts-client';

export interface ChapterRecitationData {
    units: AnimUnit[];
    ayahs: AyahBoundary[];
    /** Last word end (ms). Informational; surfaces prefer the real audio
     *  duration from the transport when they have it. */
    contentEndMs: number;
    /** Mushaf coverage gaps (incomplete + fully-missing verses) for the chapter,
     *  derived client-side from the recited units vs the qpc verse index.
     *
     *  Omitted for a word-profile (non-Hafs) delivery: the verse index is the
     *  Hafs one, and Warsh/Qalun renumber 50 of the 114 surahs, so diffing
     *  against it would report confident gaps that do not exist. Consumers
     *  already treat it as optional and simply drop the coverage badges. */
    coverage?: ChapterCoverage;
    /** SDK riwayah slug the shard was stamped for — drives the edition font and
     *  verse-marker glyph on the surfaces that render these units. */
    riwayah: string;
}

/**
 * Load + assemble chapter-absolute recitation data for a published reciter.
 * Returns `null` when the reciter isn't in the TS manifest, is `by_ayah`, the
 * shard has no verses, or the fetch is aborted.
 */
export async function loadChapterRecitation(
    reciter: string,
    chapter: number,
    signal?: AbortSignal,
): Promise<ChapterRecitationData | null> {
    if (!reciter || !chapter) return null;

    const manifest = await loadManifest();
    if (signal?.aborted) return null;

    const reciterAudio = reciterAudioFromManifest(manifest, reciter);
    if (!reciterAudio) return null; // reciter not advertised by the TS manifest
    if (reciterAudio.audio_category !== 'by_surah') return null; // see scope guard

    const shard = await loadChapterShard(reciter, chapter);
    if (signal?.aborted) return null;

    // The Hafs script files back the native profile's display text only. A
    // word-profile shard carries its own edition's exact text per word, so
    // fetching them would be two wasted megabytes and a tempting wrong answer.
    const wordShard = isWordShard(shard) ? shard : null;
    const [qpc, dk, qpcVerseIndex] = wordShard
        ? [{}, {}, null]
        : await Promise.all([loadQpc(), loadDk(), loadQpcVerseIndex()]);
    if (signal?.aborted) return null;

    // The animation consumes only word/letter timings — `audio_url` is unused
    // here (playback rides the shared player on canonical URLs), so pass "".
    // Every occasion (incl. re-takes) feeds the build, so no dedup drops audio.
    const occasions: AssembledVerse[] = [];
    for (const occ of shardOccasions(shard)) {
        occasions.push({
            verseRef: occ.ref,
            data: assembleOccasion(reciter, occ, qpc, dk, reciterAudio, ''),
            waslOutTo: occ.waslOutTo,
            special: occ.special,
        });
    }
    if (!occasions.length) return null;

    const built = buildChapterRecitation(reciter, chapter, occasions);
    return {
        units: built.units,
        ayahs: built.ayahs,
        contentEndMs: built.contentEndMs,
        coverage: qpcVerseIndex
            ? computeChapterCoverage(built.units, chapter, qpcVerseIndex.get(chapter))
            : undefined,
        riwayah: wordShard?._meta.riwayah ?? DEFAULT_SDK_RIWAYAH,
    };
}
