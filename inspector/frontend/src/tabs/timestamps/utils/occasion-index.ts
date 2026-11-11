/**
 * Picks which occasion of a verse to land on when the verse is recited more
 * than once in a chapter (e.g. stopped after its first word, then repeated
 * joined).
 */

export interface OccasionSpan {
    ref: string;
    startMs: number;
}

/** Index of the occasion of `ref` starting nearest `ms` (its first when `ms`
 *  is null), or -1 when the chapter has none. */
export function occasionIndexOfRef(occasions: readonly OccasionSpan[], ref: string, ms: number | null): number {
    let best = -1;
    occasions.forEach((o, i) => {
        if (o.ref !== ref) return;
        if (best < 0) best = i;
        else if (ms !== null && Math.abs(o.startMs - ms) < Math.abs(occasions[best]!.startMs - ms)) best = i;
    });
    return best;
}
