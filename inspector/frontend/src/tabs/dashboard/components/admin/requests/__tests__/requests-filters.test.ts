import { describe, expect, it } from 'vitest';

import type { AdminRequestRow } from '../../../../../../lib/types/generated/schemas';
import { filterRequests, toggleFilter } from '../requests-filters';

function row(over: Partial<AdminRequestRow>): AdminRequestRow {
    return {
        id: over.id ?? 'r',
        kind: 'existing_combo_edit',
        status: 'pending',
        submitted_at: '2026-01-01T00:00:00Z',
        ...over,
    } as AdminRequestRow;
}

const ROWS = [
    row({ id: 'a', riwayah: 'hafs_an_asim', source_kind: 'cdn', reciter_published: 1 }),
    row({ id: 'b', riwayah: 'hafs_an_asim', source_kind: 'playlist', kind: 'new_reciter' }),
    row({ id: 'c', riwayah: 'warsh_an_nafi', source_kind: 'cdn', reciter_published: 0 }),
];

describe('filterRequests', () => {
    it('narrows rows and counts each facet only by the other facets', () => {
        const { rows, pills } = filterRequests(ROWS, toggleFilter({}, 'source', 'cdn'), '');
        expect(rows.map((r) => r.id)).toEqual(['a', 'c']);
        // The source facet still counts the playlist row (its own filter is ignored).
        expect(pills.source.find((p) => p.tag === 'playlist')?.count).toBe(1);
        // Riwayah counts reflect the active source filter.
        expect(pills.riwayah.find((p) => p.tag === 'hafs_an_asim')?.count).toBe(1);
        expect(pills.reciter.map((p) => p.tag).sort()).toEqual(['published', 'unpublished']);
    });

    it('pins a running align row first even when the filters exclude it', () => {
        const running = row({
            id: 'z',
            source_kind: 'playlist',
            align: { status: 'running' } as AdminRequestRow['align'],
        });
        const { rows } = filterRequests([...ROWS, running], toggleFilter({}, 'source', 'cdn'), 'nomatch-a');
        expect(rows.map((r) => r.id)).toEqual(['z']);
        const all = filterRequests([...ROWS, running], {}, '');
        expect(all.rows.map((r) => r.id)).toEqual(['z', 'a', 'b', 'c']);
    });

    it('drops a facet key once its last pill is toggled off', () => {
        const on = toggleFilter({}, 'riwayah', 'warsh_an_nafi');
        expect(toggleFilter(on, 'riwayah', 'warsh_an_nafi')).toEqual({});
    });
});
