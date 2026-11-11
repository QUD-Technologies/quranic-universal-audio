import { describe, expect, it } from 'vitest';

import { alignedDateLabel, sortByAligned } from '../picker-sort';

const row = (id: string, aligned_at: string | null) => ({ id, delivery: { aligned_at } });
const ids = (rs: { id: string }[]) => rs.map((r) => r.id);

describe('sortByAligned', () => {
    const rows = [
        row('a', '2026-03-01T00:00:00Z'),
        row('none', null),
        row('b', '2026-11-10T00:00:00Z'),
        row('c', '2025-12-31T00:00:00Z'),
    ];

    it('keeps catalog order for default', () => {
        expect(ids(sortByAligned(rows, 'default'))).toEqual(['a', 'none', 'b', 'c']);
    });

    it('newest first, undated last', () => {
        expect(ids(sortByAligned(rows, 'aligned_desc'))).toEqual(['b', 'a', 'c', 'none']);
    });

    it('oldest first, undated still last', () => {
        expect(ids(sortByAligned(rows, 'aligned_asc'))).toEqual(['c', 'a', 'b', 'none']);
    });

    it('does not mutate the input', () => {
        sortByAligned(rows, 'aligned_desc');
        expect(ids(rows)).toEqual(['a', 'none', 'b', 'c']);
    });
});

describe('alignedDateLabel', () => {
    it('is empty without a date', () => {
        expect(alignedDateLabel(null, 'en')).toBe('');
        expect(alignedDateLabel('nope', 'en')).toBe('');
    });

    it('formats a short date', () => {
        expect(alignedDateLabel('2026-11-10T12:00:00Z', 'en')).toMatch(/2026/);
    });
});
