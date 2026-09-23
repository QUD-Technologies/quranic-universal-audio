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
