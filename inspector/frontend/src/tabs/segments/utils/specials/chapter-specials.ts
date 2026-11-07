/**
 * A chapter's recorded openers/closers for the Segments strip
 * (`components/specials/ChapterSpecials.svelte`, `docs/reference/specials.md`).
 */

import type { SegSpecialsResponse, SpecialEntry } from '../../../../lib/types/generated/schemas';

export { loadSpecials } from './basmala-voice';

/**
 * Sources whose entry is already a row in the segment list: Al-Fatiha's 1:1 verse
 * and a row a reviewer labelled with a special's name. The strip leaves them out.
 */
const LISTED_AS_SEGMENT: ReadonlySet<SpecialEntry['source']> = new Set(['fatiha_verse', 'in_detailed']);

/** The chapter's specials that the segment list does not show, time-ordered. */
export function listedSpecials(doc: SegSpecialsResponse | null, chapter: number): SpecialEntry[] {
    return (doc?.chapters?.[String(chapter)] ?? [])
        .filter((entry) => !LISTED_AS_SEGMENT.has(entry.source))
        .slice()
        .sort((a, b) => a.start_ms - b.start_ms);
}

/** Preview-context row id for one entry's clip. */
export function specialPreviewUid(chapter: number, entry: SpecialEntry): string {
    return `specials:${chapter}:${entry.type}:${entry.start_ms}`;
}
