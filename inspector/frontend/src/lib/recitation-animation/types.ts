/**
 * Shared types for the recitation-animation feature.
 *
 * Surface-agnostic: the engine + components consume these, never a tab store.
 * `AnimUnit` carries chapter-absolute timing (seconds) so the same units feed
 * the dashboard full-chapter player and, later, the timestamps tab.
 */

import type { SpecialKind } from '../types/ts-client';

/** One per-letter timing inside a word (seconds, chapter-absolute). */
export interface AnimLetter {
    char: string;
    start: number | null;
    end: number | null;
    tokenId?: number;
    paintCharacterIds?: number[];
    /** True only when the producer token owns or presents no performed sound. */
    silent?: boolean;
}

/** A single occurrence's time span (seconds). A word has more than one when the
 *  reciter repeats it. */
export interface TimeSpan {
    start: number;
    end: number;
    /** Set on the occurrence that is the LAST word of a take that waṣl-bridges
     *  into the next verse; value = the target ayahKey ("14:2"). Per-occurrence,
     *  so a verse recited as both a stop take and a bridge take carries it on
     *  only the bridging occurrence. A boundary that bridges in any take renders
     *  permanently merged. Absent otherwise. */
    waslTo?: string;
}

/** One animatable word with chapter-absolute timing (seconds). Each canonical
 *  word appears once; repeats are collapsed into multiple `intervals`. */
export interface AnimUnit {
    /** "surah:ayah:word". */
    location: string;
    /** "surah:ayah" — paging clears when this changes. */
    ayahKey: string;
    surah: number;
    ayah: number;
    /** Word index within its ayah (from the shard row). */
    word: number;
    /** Display text (Arabic), already DK/QPC-resolved. */
    text: string;
    /** First occurrence's start (seconds). */
    start: number;
    /** Last occurrence's end (seconds). */
    end: number;
    /** Every occurrence's span, ascending — ≥1 entry; >1 when repeated. */
    intervals: TimeSpan[];
    /** Canonical (first-occurrence) letter timings — geometry + structure anchor.
     *  Mirrors `occurrenceLetters[0]`. */
    letters: AnimLetter[];
    /** Per-occurrence letter timings, index-aligned to `intervals`. A repeated
     *  word carries each take's OWN letter spans so the per-letter reveal tracks
     *  the current take instead of linearly stretching take 1. Optional: units
     *  built outside the chapter assembler (or hand-built test fixtures) omit it
     *  and the reveal falls back to the canonical remap. */
    occurrenceLetters?: AnimLetter[][];
    /** Set on a recited opener's words (Isti'adha/Basmala). Their `ayahKey` is
     *  the opener's own `0:1`/`0:2` part ref, so `surah` is 0 and `ayah` is not
     *  a verse number — surfaces label and guard on this flag, not on `ayah`. */
    special?: SpecialKind;
}

/** Per-ayah boundary for the picker + timeline markers. */
export interface AyahBoundary {
    ayahKey: string;
    surah: number;
    ayah: number;
    /** Chapter-absolute ms of the ayah's first word start. */
    startMs: number;
    /** Chapter-absolute ms of the ayah's last word end. */
    endMs: number;
}

/** Assembled chapter recitation — the input to every animation surface. */
export interface ChapterRecitation {
    reciter: string;
    chapter: number;
    units: AnimUnit[];
    ayahs: AyahBoundary[];
    /** Chapter-absolute ms of the last word end. Fallback duration when the
     *  audio element hasn't reported its real length yet. */
    contentEndMs: number;
}
