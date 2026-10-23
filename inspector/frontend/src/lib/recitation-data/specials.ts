/**
 * Recited openers (Isti'adha, Basmala) in a timestamps shard.
 *
 * The aligner times a chapter's openers into their own readings, ahead of the
 * verses: Isti'adha on part `0:1` (words `0:1:1`..`0:1:5`), Basmala on `0:2`
 * (`0:2:1`..`0:2:4`). The reading's `special` field names it; a chapter-0 ref
 * is never a verse (mirrors the backend `timestamps_native.is_verse_ref`), so
 * surfaces that only hold a ref string can still keep it out of verse counts.
 */

export type { SpecialKind } from '../types/ts-client';

/** Chapter number of a special part ref — never a surah. */
const SPECIAL_CHAPTER_PREFIX = '0:';

/** True for a special's part ref or word location (`0:2`, `0:2:3`). */
export const isSpecialRef = (ref: string): boolean => ref.startsWith(SPECIAL_CHAPTER_PREFIX);

/** True for a real verse ref — the negation, named for verse-count call sites. */
export const isVerseRef = (ref: string): boolean => !isSpecialRef(ref);
