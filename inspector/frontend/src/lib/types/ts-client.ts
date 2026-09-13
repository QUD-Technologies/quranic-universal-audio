/**
 * Timestamps-tab client types — FE-only.
 *
 * The timing view is assembled client-side from native v13 readings. Shard
 * metadata and timing sidecars reuse generated wire types; native phonemizer
 * documents reuse the renderer package types.
 */

import type { WirePayload } from '@quranic-phonemizer/cells';
import type { TsShardMeta, TsWordShardMeta } from './generated/schemas';
import type { VerseRef } from './view-models';

// ---------------------------------------------------------------------------
// Client verse model (assembled by ts_client)
// ---------------------------------------------------------------------------

/** One audio-relative sound interval used by animation and waveform surfaces. */
export interface PhonemeInterval {
    phone: string;
    start: number; // seconds
    end: number; // seconds
}

/** Single letter with optional per-letter timing. */
export interface Letter {
    char: string;
    start: number | null;
    end: number | null;
    /** True when the native source unit owns or presents no sound. */
    silent?: boolean;
    tokenId?: number;
    sourceUnitIds?: number[];
    characterIds?: number[];
    paintCharacterIds?: number[];
    policy?: AnimationPolicy;
}

export type AnimationPolicy = 'timed' | 'cohighlight_previous' | 'cohighlight_next';

/** Single word with text + timing + letters + phoneme indices into the flat intervals list. */
export interface TsWord {
    location: string; // "surah:ayah:word"
    text: string;
    display_text: string;
    start: number; // seconds (may be negative for by_surah mode after offset)
    end: number;
    phoneme_indices: number[];
    letters: Letter[];
}

/** Pause state of the gap that follows a word-profile word. */
export type WordPauseState = 'start' | 'join' | 'sakt' | 'stop';

/** The gap after a word-profile word — the only sub-word geometry it has. */
export interface WordProfileBoundary {
    id: number;
    start: number; // seconds, offset-adjusted like TsWord
    end: number;
    state: WordPauseState;
    /** Ayah number that ends at this gap, else null. */
    verseEnd: number | null;
    /**
     * Waqf glyph lifted off the preceding word so the gap can render it as its
     * own bridge cell, mirroring the native profile's `stop_sign` column. Null
     * when the word carries none — or when this gap is a verse end, where the
     * mark stays inside the word (the verse marker owns the gap).
     */
    stopSign: string | null;
}

/** One word-profile word as `WordTimedRow` renders it. */
export interface WordProfileWord {
    /** Index within the reading — also the `timing.words[].word_id`. */
    id: number;
    /**
     * Position of this word in `TsVerseData.words`, which is what every
     * `loopTarget.wordIndex` means. Not the same number as `id`: `id` counts
     * from the reading's first word (the chapter's), this counts from the
     * verse's, and they coincide only in a chapter's first verse.
     */
    displayIndex: number;
    location: string;
    text: string;
    start: number; // seconds, offset-adjusted
    end: number;
    boundary: WordProfileBoundary | null;
}

/** One word-profile reading, carrying the report-target reading id. */
export interface WordProfileReading {
    id: string;
    words: WordProfileWord[];
}

/** Full verse data for the timestamps tab. */
export interface TsVerseData {
    reciter: string;
    chapter: number;
    verse_ref: VerseRef;
    audio_url: string;
    /** "by_ayah_audio" (default) or "by_surah_audio" — drives URL-rewrite + offset handling. */
    audio_category: 'by_ayah_audio' | 'by_surah_audio';
    time_start_ms: number;
    time_end_ms: number;
    intervals: PhonemeInterval[];
    words: TsWord[];
    /** Native schema-v2 readings rendered by quran-cells, in audio order. */
    native: TsShardReading[];
    /**
     * Word-profile readings, in audio order — populated INSTEAD of `native`
     * when the shard carries proxy-timed words. Exactly one of the two is
     * non-empty; a consumer that reads cells must check `native.length` (or
     * narrow the shard with `isWordShard`) before assuming geometry exists.
     */
    wordReadings: WordProfileReading[];
}

// ---------------------------------------------------------------------------
// Reciters (Timestamps tab)
// ---------------------------------------------------------------------------

/** Timestamps-tab reciter row. Derived client-side from the manifest, no
 *  dedicated wire model. */
export interface TsReciter {
    slug: string;
    name: string;
    audio_source?: string;
    audio_reciter?: string;
    has_data?: boolean;
}

// ---------------------------------------------------------------------------
// Catalog projection (slim, Timestamps-tab fields only)
// ---------------------------------------------------------------------------

/** GET /api/static/catalog.json — slim catalog projection. The route serves the
 *  full ReciterCatalog; only the Timestamps-tab fields are modelled. No
 *  generated equivalent (the catalog schema is not codegen'd to the FE). */
export interface TsCatalogReciter {
    reciter_id: string;
    name_en: string;
    name_ar?: string | null;
    country?: string | null;
    notes?: string | null;
}

/** Delivery entry in the v2 catalog (see TsCatalogReciter). */
export interface TsCatalogDelivery {
    slug: string;
    reciter_id: string;
    riwayah: string;
    style: string;
    source: string;
    audio_category: 'by_surah' | 'by_ayah';
}

/** GET /api/static/catalog.json — slim projection (see above). */
export interface TsCatalogResponse {
    schema_version: number;
    reciters: TsCatalogReciter[];
    deliveries: TsCatalogDelivery[];
}

// ---------------------------------------------------------------------------
// Native shard v13
// ---------------------------------------------------------------------------

export interface TsShardPart {
    ref: string;
    t: [number, number];
    word_ids: number[];
}

export interface TsAnimationToken {
    id: number;
    word_id: number;
    source_unit_ids: number[];
    character_ids: number[];
    paint_character_ids: number[];
    text: string;
    sound_ids: number[];
    policy: AnimationPolicy;
    target_token_id: number | null;
    start_ms: number | null;
    end_ms: number | null;
}

export interface TsWordTiming {
    word_id: number;
    start_ms: number;
    end_ms: number;
}

export interface TsSoundTiming {
    sound_id: number;
    start_ms: number;
    end_ms: number;
}

export interface TsBoundaryTiming {
    boundary_id: number;
    start_ms: number;
    end_ms: number;
}

export interface TsColumnTiming {
    column_id: string | number;
    start_ms: number | null;
    end_ms: number | null;
}

export interface TsShardReading {
    id: string;
    parts: TsShardPart[];
    wire: WirePayload;
    animationTokens: TsAnimationToken[];
    timing: {
        words: TsWordTiming[];
        sounds: TsSoundTiming[];
        boundaries: TsBoundaryTiming[];
        columns: TsColumnTiming[];
    };
}

export interface TsNativeShardResponse {
    _meta: TsShardMeta;
    readings: TsShardReading[];
}

/** One proxy-timed word: the delivery edition's ref and its exact text. */
export interface TsWordRow {
    ref: string;
    text: string;
    start_ms: number;
    end_ms: number;
}

/**
 * A reading in a word-profile shard. Deliberately narrower than
 * `TsShardReading`: there is no `wire` (no cells to decode), no
 * `animationTokens`, no sound or column timing. `boundaries` is derived by the
 * same rule the native profile uses, so pause geometry is identical.
 */
export interface TsWordShardReading {
    id: string;
    parts: TsShardPart[];
    words: TsWordRow[];
    /** Boundary semantic state per word, aligned to `timing.boundaries[i + 1]`. */
    states: Array<'start' | 'join' | 'sakt' | 'stop'>;
    /** Ayah that ends at word `i`, else null. */
    verseEnds: Array<number | null>;
    timing: {
        words: TsWordTiming[];
        boundaries: TsBoundaryTiming[];
    };
}

export interface TsWordShardResponse {
    _meta: TsWordShardMeta;
    readings: TsWordShardReading[];
}

export type TsShardResponse = TsNativeShardResponse | TsWordShardResponse;

/**
 * Narrow a shard to the word profile. Gate every letter/phoneme/tajweed
 * surface on this rather than on the manifest riwayah — the manifest is a
 * pre-fetch hint, the shard is the truth about what timing actually exists.
 */
export const isWordShard = (shard: TsShardResponse): shard is TsWordShardResponse =>
    (shard._meta as { profile?: string }).profile === 'word';

export const nativePayload = (reading: TsShardReading): WirePayload => ({
    analysis: reading.wire.analysis,
    cells: reading.wire.cells,
});

// ---------------------------------------------------------------------------
// Surah info (cross-tab) — route emits a bare map, no wire model
// ---------------------------------------------------------------------------

export interface SurahInfo {
    name_en: string;
    name_ar: string;
    num_verses?: number;
}

export type SurahInfoMap = Record<string, SurahInfo>;

/** GET /api/surah-info — the route emits a bare map. */
export type SurahInfoResponse = SurahInfoMap;

/** GET /api/ts/vbr/:reciter — fallback for older HF manifests. */
export interface TsVbrResponse {
    vbr_chapters: number[];
    error?: string;
}
