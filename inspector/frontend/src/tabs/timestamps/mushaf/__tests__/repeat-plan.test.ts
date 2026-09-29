import { describe, expect, it } from 'vitest';

import {
    expandRange,
    firstCursor,
    nextCursor,
    parseRef,
    refKey,
    type RepeatCursor,
    type RepeatRequest,
} from '../repeat-plan';

function walk(req: RepeatRequest, limit = 50): string[] {
    const out: string[] = [];
    let c: RepeatCursor | null = firstCursor(req);
    while (c && out.length < limit) {
        out.push(refKey(req.verses[c.verse]!));
        c = nextCursor(req, c);
    }
    return out;
}

const v = (surah: number, ayah: number) => ({ surah, ayah });

describe('repeat plan', () => {
    it('repeats each verse, then the whole range', () => {
        const req = { verses: [v(1, 1), v(1, 2)], each: 2, rounds: 2 };
        expect(walk(req)).toEqual(['1:1', '1:1', '1:2', '1:2', '1:1', '1:1', '1:2', '1:2']);
    });

    it('plays a single verse once by default', () => {
        expect(walk({ verses: [v(2, 255)], each: 1, rounds: 1 })).toEqual(['2:255']);
    });

    it('never ends with an infinite count', () => {
        expect(walk({ verses: [v(1, 1)], each: Infinity, rounds: 1 }, 20)).toHaveLength(20);
    });

    it('has no first step for an empty range', () => {
        expect(firstCursor({ verses: [], each: 1, rounds: 1 })).toBeNull();
    });
});

describe('expandRange', () => {
    const counts = (s: number) => ({ 112: 4, 113: 5, 114: 6 })[s] ?? 0;

    it('spans surahs and skips ones the reciter lacks', () => {
        const out = expandRange(v(112, 3), v(114, 2), counts, (s) => s !== 113);
        expect(out.map(refKey)).toEqual(['112:3', '112:4', '114:1', '114:2']);
    });

    it('treats a missing end as the start verse, and normalises swapped ends', () => {
        expect(expandRange(v(113, 2), null, counts, () => true).map(refKey)).toEqual(['113:2']);
        expect(expandRange(v(113, 3), v(113, 1), counts, () => true).map(refKey)).toEqual(['113:1', '113:2', '113:3']);
    });

    it('clips an end past the surah length', () => {
        expect(expandRange(v(112, 3), v(112, 9), counts, () => true).map(refKey)).toEqual(['112:3', '112:4']);
    });
});

describe('parseRef', () => {
    it('accepts surah:verse and rejects nonsense', () => {
        expect(parseRef(' 2:255 ')).toEqual(v(2, 255));
        expect(parseRef('2.255')).toEqual(v(2, 255));
        expect(parseRef('115:1')).toBeNull();
        expect(parseRef('2')).toBeNull();
        expect(parseRef('2:0')).toBeNull();
    });
});
