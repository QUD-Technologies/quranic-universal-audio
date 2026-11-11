import type { SegWordTiming } from '../../../../lib/types/generated/schemas';

/** A word interval: review-sample timings and stored segment times share this shape. */
export type WordInterval = Pick<SegWordTiming, 'location' | 'start_ms' | 'end_ms'>;

/** Return the word interval sounding at `timeMs`, or -1 between intervals. */
export function wordIndexAt(timeMs: number, timings: WordInterval[] | null | undefined): number {
    if (!timings?.length) return -1;
    return timings.findIndex((word, index) =>
        timeMs >= word.start_ms
        && (timeMs < word.end_ms || (index === timings.length - 1 && timeMs === word.end_ms)),
    );
}

/**
 * Highlight the last timed word reached, holding it through silence and clock
 * regressions. The caller resets `heldIndex` to -1 on a seek, clip change or
 * timing replacement; the size of a clock correction cannot identify a seek.
 * Zero-duration placeholders preserve text positions but never sound.
 */
export function steadyWordIndexAt(
    timeMs: number,
    timings: WordInterval[] | null | undefined,
    heldIndex: number,
): number {
    if (!timings?.length) return -1;
    let index = -1;
    for (let i = timings.length - 1; i >= 0; i--) {
        const word = timings[i]!;
        if (word.end_ms > word.start_ms && timeMs >= word.start_ms) {
            index = i;
            break;
        }
    }
    const held = heldIndex >= 0 ? timings?.[heldIndex] : undefined;
    if (held && held.end_ms > held.start_ms) return Math.max(index, heldIndex);
    return index;
}

/** Guard against displaying stale timings after a reference edit. */
export function timingsMatchRef(ref: string, timings: WordInterval[] | null | undefined): boolean {
    if (!ref || !timings?.length || !ref.includes(':')) return false;
    const [start, end = start] = ref.split('-');
    return timings[0]?.location === start && timings[timings.length - 1]?.location === end;
}

/** Use the same edition text and verse ornaments as the ordinary segment row.
 * Historical word-timing text may use a different script; if its coordinates
 * cannot reproduce the displayed reference exactly, omit word highlights. */
export function displayWordsForTimings(
    timings: WordInterval[],
    bodyText: string,
    dkWords: Record<string, string> | undefined,
    verseWordCounts: Record<string, number> | undefined,
    verseMarkerPrefix: string,
): string[] {
    if (!dkWords || !verseWordCounts || !timings.length) return [];
    const digits = '٠١٢٣٤٥٦٧٨٩';
    const words = timings.map(({ location }) => {
        const text = dkWords[location];
        if (!text) return '';
        const [surah, ayah, index] = location.split(':');
        if (!surah || !ayah || !index) return '';
        const isVerseEnd = Number(index) === verseWordCounts[`${surah}:${ayah}`];
        const marker = isVerseEnd
            ? `${verseMarkerPrefix}${ayah.replace(/\d/g, (d) => digits[Number(d)] ?? d)}`
            : '';
        return marker ? `${text} ${marker}` : text;
    });
    return words.every(Boolean) && words.join(' ') === bodyText ? words : [];
}
