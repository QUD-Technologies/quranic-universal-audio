export type WordBounds = { start_ms: number; end_ms: number };

export const MIN_WORD_MS = 20;

/** One boundary separates each pair of words; only the outer edges are free. */
export function contiguousWordDraft(words: readonly WordBounds[]): WordBounds[] {
    const draft = words.map(word => ({ start_ms: word.start_ms, end_ms: word.end_ms }));
    for (let i = 1; i < draft.length; i++) draft[i - 1]!.end_ms = draft[i]!.start_ms;
    return draft;
}

function clamp(value: number, minimum: number, maximum: number): number {
    return Math.max(minimum, Math.min(maximum, value));
}

/** Boundary 0 is the first start; boundary N is the last end. */
export function moveWordBoundary(
    words: readonly WordBounds[], boundary: number, positionMs: number, segmentStartMs: number, segmentEndMs: number,
): WordBounds[] {
    const next = words.map(word => ({ ...word }));
    if (!next.length || boundary < 0 || boundary > next.length) return next;
    if (boundary === 0) {
        next[0]!.start_ms = clamp(positionMs, segmentStartMs, next[0]!.end_ms - MIN_WORD_MS);
    } else if (boundary === next.length) {
        const last = next[next.length - 1]!;
        last.end_ms = clamp(positionMs, last.start_ms + MIN_WORD_MS, segmentEndMs);
    } else {
        const left = next[boundary - 1]!;
        const right = next[boundary]!;
        const position = clamp(positionMs, left.start_ms + MIN_WORD_MS, right.end_ms - MIN_WORD_MS);
        left.end_ms = position;
        right.start_ms = position;
    }
    return next;
}

/** Moving a word carries its neighboring shared edges, without opening gaps. */
export function moveWordBlock(
    words: readonly WordBounds[], index: number, deltaMs: number, segmentStartMs: number, segmentEndMs: number,
): WordBounds[] {
    const next = words.map(word => ({ ...word }));
    if (index < 0 || index >= next.length) return next;
    const word = next[index]!;
    const previous = index > 0 ? next[index - 1]! : null;
    const following = index + 1 < next.length ? next[index + 1]! : null;
    const minimum = (previous ? previous.start_ms + MIN_WORD_MS : segmentStartMs) - word.start_ms;
    const maximum = (following ? following.end_ms - MIN_WORD_MS : segmentEndMs) - word.end_ms;
    const offset = clamp(deltaMs, minimum, maximum);
    word.start_ms += offset;
    word.end_ms += offset;
    if (previous) previous.end_ms = word.start_ms;
    if (following) following.start_ms = word.end_ms;
    return next;
}
