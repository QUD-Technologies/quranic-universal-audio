import { describe, expect, it } from 'vitest';

import { alignedDateLabel, sortPickerRows } from '../picker-sort';

const row = (id: string, aligned_at: string | null) => ({ id, delivery: { aligned_at, slug: id } });
const ids = (rs: { id: string }[]) => rs.map((r) => r.id);

describe('sortPickerRows', () => {
    const rows = [
        row('a', '2026-03-01T00:00:00Z'),
        row('none', null),
        row('b', '2026-11-10T00:00:00Z'),
        row('c', '2025-12-31T00:00:00Z'),
    ];

    it('keeps catalog order for default', () => {
        expect(ids(sortPickerRows(rows, 'default'))).toEqual(['a', 'none', 'b', 'c']);
    });

    it('newest first, undated last', () => {
        expect(ids(sortPickerRows(rows, 'aligned_desc'))).toEqual(['b', 'a', 'c', 'none']);
    });

    it('oldest first, undated still last', () => {
        expect(ids(sortPickerRows(rows, 'aligned_asc'))).toEqual(['c', 'a', 'b', 'none']);
    });

    it('does not mutate the input', () => {
        sortPickerRows(rows, 'aligned_desc');
        expect(ids(rows)).toEqual(['a', 'none', 'b', 'c']);
    });
});

describe('sortPickerRows review sorts', () => {
    const rows = [row('a', null), row('b', null), row('c', null), row('d', null)];
    const signals: Record<string, { low_confidence?: number; low_confidence_v2?: number; flagged?: number }> = {
        b: { low_confidence: 2, low_confidence_v2: 3, flagged: 1 },
        c: { low_confidence: 1, flagged: 4 },
    };
    const signalOf = (slug: string) => signals[slug];

    it('most low confidence first (both kinds), the rest in catalog order', () => {
        expect(ids(sortPickerRows(rows, 'low_conf_desc', signalOf))).toEqual(['b', 'c', 'a', 'd']);
    });

    it('most flags first, the rest in catalog order', () => {
        expect(ids(sortPickerRows(rows, 'flags_desc', signalOf))).toEqual(['c', 'b', 'a', 'd']);
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
