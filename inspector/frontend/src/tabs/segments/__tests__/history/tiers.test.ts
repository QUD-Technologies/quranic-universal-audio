import { describe, expect, it } from 'vitest';

import type { GenerationBoundary } from '../../../../lib/types/view-models';
import {
    entryAffectsTimestamps,
    tierOf,
    TS_AFFECTING_OP_TYPES,
    type DisplayEntry,
} from '../../utils/history/items';

const GENS: GenerationBoundary[] = [
    { version: 'g1', produced_at: '2026-01-01T00:00:00Z', published: true, published_at: null },
    { version: 'g2', produced_at: '2026-03-01T00:00:00Z', published: false, published_at: null },
];

function opItem(opType: string, date: string): DisplayEntry {
    return {
        type: 'op-item',
        date,
        // Minimal OpFlatItem shape — only `group` is read by the helpers.
        item: { group: [{ op_type: opType }] } as never,
    };
}

describe('tierOf', () => {
    it('returns 0 for an edit before the first generation', () => {
        expect(tierOf('2025-12-01T00:00:00Z', GENS)).toBe(0);
    });
    it('returns 1 for an edit between gen 1 and gen 2', () => {
        expect(tierOf('2026-02-01T00:00:00Z', GENS)).toBe(1);
    });
    it('returns the current tier (2) for an edit after the latest generation', () => {
        expect(tierOf('2026-04-01T00:00:00Z', GENS)).toBe(2);
    });
});

describe('entryAffectsTimestamps', () => {
    it('is true for a structural/reference op', () => {
        expect(entryAffectsTimestamps(opItem('trim_segment', '2026-04-01T00:00:00Z'))).toBe(true);
        expect(entryAffectsTimestamps(opItem('edit_reference', '2026-04-01T00:00:00Z'))).toBe(true);
    });
    it('is false for an annotation-only op', () => {
        expect(entryAffectsTimestamps(opItem('ignore_issue', '2026-04-01T00:00:00Z'))).toBe(false);
        expect(entryAffectsTimestamps(opItem('confirm_reference', '2026-04-01T00:00:00Z'))).toBe(false);
    });
    it('excludes exactly the annotation/comment ops', () => {
        for (const ann of ['confirm_reference', 'ignore_issue', 'flag_segment']) {
            expect(TS_AFFECTING_OP_TYPES.has(ann)).toBe(false);
        }
    });
    it('treats a waṣl relabel as timestamp-affecting', () => {
        expect(TS_AFFECTING_OP_TYPES.has('set_is_wasl')).toBe(true);
    });
});
