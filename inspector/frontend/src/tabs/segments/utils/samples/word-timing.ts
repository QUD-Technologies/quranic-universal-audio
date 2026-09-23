import type { SegWordTiming } from '../../../../lib/types/generated/schemas';

/** Return the word interval sounding at `timeMs`, or -1 between intervals. */
export function wordIndexAt(timeMs: number, timings: SegWordTiming[] | null | undefined): number {
    if (!timings?.length) return -1;
    return timings.findIndex((word, index) =>
        timeMs >= word.start_ms
        && (timeMs < word.end_ms || (index === timings.length - 1 && timeMs === word.end_ms)),
    );
}

/** Guard against displaying stale timings after a reference edit. */
export function timingsMatchRef(ref: string, timings: SegWordTiming[] | null | undefined): boolean {
    if (!ref || !timings?.length || !ref.includes(':')) return false;
    const [start, end = start] = ref.split('-');
    return timings[0]?.location === start && timings[timings.length - 1]?.location === end;
}

/** Use the same edition text and verse ornaments as the ordinary segment row.
 * Historical word-timing text may use a different script; if its coordinates
 * cannot reproduce the displayed reference exactly, omit word highlights. */
export function displayWordsForTimings(
    timings: SegWordTiming[],
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
