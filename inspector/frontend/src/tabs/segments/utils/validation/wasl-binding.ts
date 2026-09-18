/**
 * Which WASL/WAQF boundary a focused cross-verse piece labels.
 *
 * A cross-verse card renders N pieces with a picker between each adjacent
 * pair, each picker keyed by the uid of the piece ABOVE it. The keyboard
 * (1 = waṣl, 2 = waqf) acts on the boundary **directly below** the focused
 * piece; the last piece has nothing below it, so it takes the boundary
 * **above** it instead.
 *
 * Consequences, which are the whole point of the rule:
 *   - Two pieces (one boundary): both pieces resolve to that single boundary,
 *     so it doesn't matter which one you are on.
 *   - Three or more pieces: every piece still resolves to exactly one
 *     boundary, so the key is never ambiguous — piece i labels the boundary
 *     under it, and the final piece labels the one above it (the same boundary
 *     piece N-2 labels).
 */

/** Commit callbacks published by mounted pickers, keyed by the uid of the
 *  piece above each one. */
export type WaslCommits = Map<string, (_value: boolean) => void>;

/**
 * Resolve the commit for the piece at `i`, or null when neither side of it has
 * a mounted picker (then the shortcut stays unhandled and the keys fall
 * through to their normal meaning).
 */
export function waslCommitForPiece(
    i: number,
    uids: ReadonlyArray<string | null | undefined>,
    commits: WaslCommits,
): ((_value: boolean) => void) | null {
    const below = uids[i] ?? '';
    if (below && commits.has(below)) return commits.get(below) ?? null;
    const above = i > 0 ? uids[i - 1] ?? '' : '';
    if (above && commits.has(above)) return commits.get(above) ?? null;
    return null;
}
