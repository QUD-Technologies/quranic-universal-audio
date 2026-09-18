/**
 * Which WASL/WAQF boundary a focused cross-verse piece labels.
 *
 * A cross-verse card renders N pieces with a picker between each adjacent
 * pair, each picker keyed by the uid of the piece above it. The keyboard
 * (1 = waṣl, 2 = waqf) acts on the boundary **directly above** the piece you
 * are on; the FIRST piece has nothing above it, so it doubles for the
 * boundary **below** it.
 *
 * Consequences, which are the whole point of the rule:
 *   - Two pieces (one boundary): both pieces resolve to that single boundary,
 *     so it doesn't matter which one you are on.
 *   - Three or more pieces: every piece still resolves to exactly one
 *     boundary, so the key is never ambiguous — piece 0 labels the boundary
 *     below it (the same one piece 1 labels from above), and every later
 *     piece labels the boundary immediately above itself.
 */

/** Commit callbacks published by mounted pickers, keyed by the uid of the
 *  piece above each one. */
export type WaslCommits = Map<string, (_value: boolean) => void>;

/**
 * Resolve the commit for the piece at `i` — the boundary above it, else (first
 * piece only) the one below it — or null when neither side has a mounted
 * picker, in which case the shortcut stays unhandled and the keys fall through
 * to their normal meaning.
 */
export function waslCommitForPiece(
    i: number,
    uids: ReadonlyArray<string | null | undefined>,
    commits: WaslCommits,
): ((_value: boolean) => void) | null {
    const above = i > 0 ? uids[i - 1] ?? '' : '';
    if (above && commits.has(above)) return commits.get(above) ?? null;
    const below = uids[i] ?? '';
    if (below && commits.has(below)) return commits.get(below) ?? null;
    return null;
}
