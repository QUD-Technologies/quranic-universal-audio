/**
 * Repeat plan — which verse plays next while a Mushaf repeat runs.
 *
 * A request is a verse range (possibly across surahs) plus two counts: each
 * verse × `each`, then the whole range × `rounds`. `Infinity` repeats forever.
 * Pure: the view's driver owns audio, this only walks the sequence.
 */

export interface VerseRef {
    surah: number;
    ayah: number;
}

export interface RepeatRequest {
    /** Verses in mushaf order, already filtered to what the reciter recited. */
    verses: VerseRef[];
    each: number;
    rounds: number;
}

export interface RepeatCursor {
    /** Index into `verses`. */
    verse: number;
    /** 1-based repetition of the current verse. */
    rep: number;
    /** 1-based round over the whole range. */
    round: number;
}

export const REPEAT_COUNTS = [1, 2, 3, 4, 5, 7, 10, Infinity] as const;

/** Silence between plays, ms. 0 = none: replays and jumps run straight on. */
export const REPEAT_PAUSES_MS = [0, 500, 1000, 2000, 3000, 5000, 10000] as const;

export function firstCursor(req: RepeatRequest): RepeatCursor | null {
    return req.verses.length ? { verse: 0, rep: 1, round: 1 } : null;
}

/** The step after `c`, or null when the plan is finished. */
export function nextCursor(req: RepeatRequest, c: RepeatCursor): RepeatCursor | null {
    if (c.rep < req.each) return { ...c, rep: c.rep + 1 };
    if (c.verse + 1 < req.verses.length) return { verse: c.verse + 1, rep: 1, round: c.round };
    if (c.round < req.rounds) return { verse: 0, rep: 1, round: c.round + 1 };
    return null;
}

export function refKey(v: VerseRef): string {
    return `${v.surah}:${v.ayah}`;
}

export function parseRef(raw: string): VerseRef | null {
    const m = /^\s*(\d{1,3})\s*[:.]\s*(\d{1,3})\s*$/.exec(raw);
    if (!m) return null;
    const surah = Number(m[1]);
    const ayah = Number(m[2]);
    return surah >= 1 && surah <= 114 && ayah >= 1 ? { surah, ayah } : null;
}

export function compareRefs(a: VerseRef, b: VerseRef): number {
    return a.surah - b.surah || a.ayah - b.ayah;
}

/**
 * Every verse from `from` to `to` (inclusive, mushaf order), keeping only the
 * surahs the reciter has and the verses `verseCounts` knows. `to` null = just
 * `from`. Swapped ends are normalised.
 */
export function expandRange(
    from: VerseRef,
    to: VerseRef | null,
    verseCounts: (surah: number) => number,
    hasSurah: (surah: number) => boolean,
): VerseRef[] {
    let a = from;
    let b = to ?? from;
    if (compareRefs(a, b) > 0) [a, b] = [b, a];
    const out: VerseRef[] = [];
    for (let s = a.surah; s <= b.surah; s++) {
        if (!hasSurah(s)) continue;
        const count = verseCounts(s);
        const first = s === a.surah ? a.ayah : 1;
        const last = s === b.surah ? Math.min(b.ayah, count) : count;
        for (let v = first; v <= last; v++) out.push({ surah: s, ayah: v });
    }
    return out;
}
