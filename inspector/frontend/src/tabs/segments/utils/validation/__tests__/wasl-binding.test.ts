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

    it('three pieces: each piece labels the boundary below it, the last one the boundary above', () => {
        const uids = ['a', 'b', 'c'];
        const { commits, calls } = cardWithBoundaries(uids);

        waslCommitForPiece(0, uids, commits)?.(true);  // a|b
        waslCommitForPiece(1, uids, commits)?.(true);  // b|c
        waslCommitForPiece(2, uids, commits)?.(true);  // no boundary below → b|c

        expect(calls).toEqual(['a', 'b', 'b']);
    });

    it('four pieces: every piece maps to exactly one boundary', () => {
        const uids = ['a', 'b', 'c', 'd'];
        const { commits, calls } = cardWithBoundaries(uids);

        for (let i = 0; i < uids.length; i++) waslCommitForPiece(i, uids, commits)?.(true);

        expect(calls).toEqual(['a', 'b', 'c', 'c']);
    });

    it('returns null when no picker is mounted on either side', () => {
        expect(waslCommitForPiece(0, ['a'], new Map())).toBeNull();
    });

    it('returns null for a piece with no uid and no labelled piece above it', () => {
        const commits: WaslCommits = new Map([['a', vi.fn()]]);
        expect(waslCommitForPiece(0, [undefined, 'b'], commits)).toBeNull();
    });

    it('falls back upward only — a piece above with no picker does not leak a wrong boundary', () => {
        // Pickers exist for 'a' only; piece 2 ('c') has neither 'c' nor 'b' keyed.
        const commits: WaslCommits = new Map([['a', vi.fn()]]);
        expect(waslCommitForPiece(2, ['a', 'b', 'c'], commits)).toBeNull();
    });
});
