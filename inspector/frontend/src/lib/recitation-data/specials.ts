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

/**
 * The Quran word each opener word is read as (qua_sdk `special_native`): the
 * Basmala is 1:1, the Isti'adha a connected reading of 2:67:15 then 16:98:5-8.
 * An opener word's tokens are that word's, so it takes that word's QPC and
 * DigitalKhatt text and shaped teleprompter geometry. Mirrored by
 * `OPENER_SOURCE_WORDS` in `scripts/codegen/build_shaped_animation_fixture.py`.
 */
const OPENER_SOURCE_WORDS: Readonly<Record<string, readonly string[]>> = {
    '0:1': ['2:67:15', '16:98:5', '16:98:6', '16:98:7', '16:98:8'],
    '0:2': ['1:1:1', '1:1:2', '1:1:3', '1:1:4'],
};

/** The verse word an opener word (`0:2:3`) is read as, else null. */
export function openerSourceRef(location: string): string | null {
    if (!isSpecialRef(location)) return null;
    const [surah, ayah, word] = location.split(':');
    return OPENER_SOURCE_WORDS[`${surah}:${ayah}`]?.[Number(word) - 1] ?? null;
}
