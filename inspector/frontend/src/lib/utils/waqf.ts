/**
 * Quranic waqf (stop) marks — the single source of stop-sign truth shared by the
 * Timestamps analysis view and the recitation teleprompter.
 *
 * Waqf marks are the small-high pause symbols (ۖ ۗ ۘ ۚ ۛ) that ride the Quranic
 * text as combining marks (Unicode category Mn) at the tail of a word's last
 * letter group. They are NOT pronounced, so they never appear in the MFA-aligned
 * `letters` array — only in the word's `text`. We surface one when the
 * reciter actually pauses on it.
 *
 * Excluded by design: U+06D9 (ۙ, "لا") means "do NOT stop here", and U+06DC
 * (ۜ, saktah) is a brief breathless hold, not a stop — a pause at either renders
 * the neutral pause token instead of a stop sign.
 */

/** Surfaced waqf marks. Excludes U+06D9 (لا) and U+06DC (saktah) by design. */
export const STOP_MARKS = new Set([0x06d6, 0x06d7, 0x06d8, 0x06da, 0x06db]);

/** True when `text` carries a surfaced stop sign. */
export function hasWaqf(text: string): boolean {
    for (const ch of text) {
        const cp = ch.codePointAt(0);
        if (cp !== undefined && STOP_MARKS.has(cp)) return true;
    }
    return false;
}

/**
 * Split surfaced stop marks out of `text`.
 *
 * Returns the cleaned string (marks removed) plus the captured mark glyph — the
 * last one if several ride the same word (a word ends on at most one stop in
 * practice). `mark` is null when none is present, in which case `clean === text`.
 */
export function splitWaqf(text: string): { clean: string; mark: string | null } {
    let mark: string | null = null;
    let clean = '';
    for (const ch of text) {
        const cp = ch.codePointAt(0);
        if (cp !== undefined && STOP_MARKS.has(cp)) {
            mark = ch;
            continue;
        }
        clean += ch;
    }
    return { clean, mark };
}
