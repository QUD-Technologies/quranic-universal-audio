import { describe, expect, it, vi } from 'vitest';

import { waslCommitForPiece, type WaslCommits } from '../wasl-binding';

/** n pieces → n-1 pickers, each keyed by the piece above it. */
function cardWithBoundaries(uids: string[]): { commits: WaslCommits; calls: string[] } {
    const calls: string[] = [];
    const commits: WaslCommits = new Map();
    for (let i = 0; i < uids.length - 1; i++) {
        const key = uids[i]!;
        commits.set(key, () => calls.push(key));
    }
    return { commits, calls };
}

describe('waslCommitForPiece', () => {
    it('two pieces: both resolve to the single boundary between them', () => {
        const uids = ['a', 'b'];
        const { commits, calls } = cardWithBoundaries(uids);

        waslCommitForPiece(0, uids, commits)?.(true);
        waslCommitForPiece(1, uids, commits)?.(false);

        expect(calls).toEqual(['a', 'a']); // the one boundary, from either piece
    });

    it('three pieces: each piece labels the boundary ABOVE it, the first one the boundary below', () => {
        const uids = ['a', 'b', 'c'];
        const { commits, calls } = cardWithBoundaries(uids);

        waslCommitForPiece(0, uids, commits)?.(true);  // none above → a|b
        waslCommitForPiece(1, uids, commits)?.(true);  // above → a|b
        waslCommitForPiece(2, uids, commits)?.(true);  // above → b|c

        expect(calls).toEqual(['a', 'a', 'b']);
    });

    it('four pieces: every piece maps to exactly one boundary', () => {
        const uids = ['a', 'b', 'c', 'd'];
        const { commits, calls } = cardWithBoundaries(uids);

        for (let i = 0; i < uids.length; i++) waslCommitForPiece(i, uids, commits)?.(true);

        expect(calls).toEqual(['a', 'a', 'b', 'c']);
    });

    it('returns null when no picker is mounted on either side', () => {
        expect(waslCommitForPiece(0, ['a'], new Map())).toBeNull();
    });

    it('returns null for a first piece with no uid and nothing above it', () => {
        const commits: WaslCommits = new Map([['a', vi.fn()]]);
        expect(waslCommitForPiece(0, [undefined, 'b'], commits)).toBeNull();
    });

    it('does not leak a wrong boundary when neither side has a picker', () => {
        // Pickers exist for 'a' only; piece 2 ('c') has neither 'b' (above)
        // nor 'c' (below) keyed.
        const commits: WaslCommits = new Map([['a', vi.fn()]]);
        expect(waslCommitForPiece(2, ['a', 'b', 'c'], commits)).toBeNull();
    });

    it('the trailing unmarked_wasl picker still reaches a single-member card', () => {
        // One member, one picker keyed by it (the join to the next verse).
        const commits: WaslCommits = new Map();
        const calls: string[] = [];
        commits.set('only', () => calls.push('only'));
        waslCommitForPiece(0, ['only'], commits)?.(true);
        expect(calls).toEqual(['only']);
    });
});
