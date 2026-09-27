/**
 * 1 / 2 on a staged card label the boundary of the piece the user is ON. The
 * pieces share their parent's (chapter, index), so that piece is the one the
 * nav cursor names — not wherever the playhead walked on to.
 */

import { describe, expect, it } from 'vitest';

import { stagedPieceIsCurrent } from '../../utils/validation/wasl-binding';

const pieces = [
    { uid: 'root', startMs: 1000 },
    { uid: 'child-1', startMs: 2000 },
    { uid: 'child-2', startMs: 3000 },
];

describe('stagedPieceIsCurrent', () => {
    it('follows the nav cursor to exactly one piece', () => {
        const cursor = { uid: 'child-1', startMs: 2000 };
        const current = pieces.map((p) => stagedPieceIsCurrent(cursor, p, false, false));
        expect(current).toEqual([false, true, false]);
    });

    it('ignores the playhead once a cursor names a piece', () => {
        const cursor = { uid: 'root', startMs: 1000 };
        expect(stagedPieceIsCurrent(cursor, pieces[2]!, true, true)).toBe(false);
        expect(stagedPieceIsCurrent(cursor, pieces[0]!, false, false)).toBe(true);
    });

    it('falls back to the playhead before any cursor exists', () => {
        expect(stagedPieceIsCurrent(null, pieces[1]!, true, false)).toBe(true);
        expect(stagedPieceIsCurrent(null, pieces[1]!, false, true)).toBe(true);
        expect(stagedPieceIsCurrent(null, pieces[1]!, false, false)).toBe(false);
    });
});
