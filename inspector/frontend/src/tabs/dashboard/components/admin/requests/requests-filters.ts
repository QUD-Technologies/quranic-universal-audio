/**
 * Requests-tab filters: pill facets over the rows of the selected status tab,
 * faceted the same way as the Dashboard rail (`recomputeFacets` — each facet's
 * counts narrow only by the OTHER facets, so a pill never zeroes its siblings),
 * plus a free-text search over name / slug / channel.
 */
import { recomputeFacets, type FacetSpec } from '../../../../../lib/utils/facets';
import { vocabLabel } from '../../../../../lib/i18n/vocab';
import type { AdminRequestRow } from '../../../../../lib/types/generated/schemas';

export type RequestFacetKey = 'riwayah' | 'reciter' | 'source' | 'channel';
export type RequestFilters = Partial<Record<RequestFacetKey, Set<string>>>;

/** Where the reciter stands: a new reciter, or an existing one with / without a release. */
export function reciterTag(row: AdminRequestRow): string {
    if (row.kind === 'new_reciter') return 'new';
    return (row.reciter_published ?? 0) > 0 ? 'published' : 'unpublished';
}

const RECITER_LABELS: Record<string, string> = {
    new: 'New reciter',
    published: 'Has a release',
    unpublished: 'No release yet',
};
const SOURCE_LABELS: Record<string, string> = { playlist: 'Playlist', cdn: 'CDN' };

export interface RequestFacet {
    key: RequestFacetKey;
    label: string;
    tagsOf: (row: AdminRequestRow) => string[];
    tagLabel: (row: AdminRequestRow | undefined, tag: string) => string;
}

export const REQUEST_FACETS: RequestFacet[] = [
    {
        key: 'riwayah',
        label: 'Riwayah',
        tagsOf: (r) => (r.riwayah ? [r.riwayah] : []),
        tagLabel: (_r, t) => vocabLabel('riwayah', t),
    },
    {
        key: 'reciter',
        label: 'Reciter',
        tagsOf: (r) => [reciterTag(r)],
        tagLabel: (_r, t) => RECITER_LABELS[t] ?? t,
    },
    {
        key: 'source',
        label: 'Source',
        tagsOf: (r) => (r.source_kind ? [r.source_kind] : []),
        tagLabel: (_r, t) => SOURCE_LABELS[t] ?? t,
    },
    {
        key: 'channel',
        label: 'Channel',
        tagsOf: (r) => (r.channel ? [r.channel] : []),
        tagLabel: (r, t) => r?.channel_name ?? t,
    },
];

export interface FacetPill {
    tag: string;
    label: string;
    count: number;
    active: boolean;
}

export interface FilteredRequests {
    rows: AdminRequestRow[];
    /** Per facet: the pills to show — every tag with a match, plus any active one. */
    pills: Record<RequestFacetKey, FacetPill[]>;
}

function matchesSearch(row: AdminRequestRow, needle: string): boolean {
    if (!needle) return true;
    const hay = [row.name_en, row.name_ar, row.slug, row.reciter_id, row.channel_name]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
    return hay.includes(needle);
}

export function filterRequests(
    rows: AdminRequestRow[],
    filters: RequestFilters,
    search: string,
): FilteredRequests {
    const needle = search.trim().toLowerCase();
    const searched = rows.filter((r) => matchesSearch(r, needle));
    const specs: FacetSpec<AdminRequestRow>[] = REQUEST_FACETS.map((f) => ({
        key: f.key,
        tagsOf: f.tagsOf,
    }));
    const active = filters as Record<string, Set<string>>;
    const { rowVisibility, perFacetCounts } = recomputeFacets(searched, active, specs);

    const pills = {} as Record<RequestFacetKey, FacetPill[]>;
    for (const f of REQUEST_FACETS) {
        const counts = perFacetCounts[f.key] ?? {};
        const on = filters[f.key] ?? new Set<string>();
        const tags = new Set([...Object.keys(counts), ...on]);
        pills[f.key] = [...tags]
            .map((tag) => ({
                tag,
                label: f.tagLabel(searched.find((r) => f.tagsOf(r).includes(tag)), tag),
                count: counts[tag] ?? 0,
                active: on.has(tag),
            }))
            .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    }
    return { rows: searched.filter((_, i) => rowVisibility[i]), pills };
}

/** Toggle one pill, returning a new filter map (empty sets dropped). */
export function toggleFilter(
    filters: RequestFilters,
    key: RequestFacetKey,
    tag: string,
): RequestFilters {
    const next = new Set(filters[key] ?? []);
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    const out: RequestFilters = { ...filters };
    if (next.size > 0) out[key] = next;
    else delete out[key];
    return out;
}
