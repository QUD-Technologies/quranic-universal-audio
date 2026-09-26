/**
 * Validation-accordion sort abstraction.
 *
 * Declarative twin of the per-accordion `sorts` field in `./registry.ts`:
 * the registry says *which* sorts an accordion offers; this module says *how*
 * each sort kind reads an ordering key off an item and how a list is ordered.
 *
 * A sort is a (kind, direction) pair. When an accordion offers two sorts, the
 * inactive one acts as an ascending secondary tiebreaker for items that tie on
 * the active key. Item shapes differ per category and carry no discriminator
 * (`SegValAnyItem`), so key extractors read fields defensively.
 *
 * User choice + direction are persisted globally (not per-reciter) by
 * `../stores/validation-sort.ts`.
 */

import * as m from '../../../lib/paraglide/messages';
import type { SegValAnyItem } from '../../../lib/types/generated/schemas';

export type SortKind = 'quran_order' | 'confidence' | 'word_count' | 'verse_count' | 'rep_split_count' | 'score';
export type SortDir = 'asc' | 'desc';

/** A sort an accordion offers. Registry rows hold a readonly list of these. */
export interface SortOption {
    kind: SortKind;
    /** Marks the active-by-default sort for the accordion (first option if unset). */
    default?: boolean;
}

/** Presentation + default direction per sort kind. */
export const SORT_META: Readonly<Record<SortKind, { label: () => string; defaultDir: SortDir }>> = Object.freeze({
    quran_order:     { label: m.segments_validation_sort_quran_order_label, defaultDir: 'asc' },
    confidence:      { label: m.segments_validation_sort_confidence_label,  defaultDir: 'asc' },
    word_count:      { label: m.segments_validation_sort_words_label,       defaultDir: 'asc' },
    verse_count:     { label: m.segments_validation_sort_verses_label,      defaultDir: 'asc' },
    rep_split_count: { label: m.segments_validation_sort_splits_label,      defaultDir: 'asc' },
    score:           { label: m.segments_validation_sort_score_label,       defaultDir: 'desc' },
});

/** Extra inputs some extractors need (resolved at projection time). */
export interface SortCtx {
    /** Auto-split sidecar by segment_uid — source of the suggested split count. */
    autoSplitMap?: Record<string, { refs: string[] }> | null;
}

/** Loose view over the heterogeneous item union for field probing. */
type ItemFields = {
    chapter?: number;
    seg_index?: number;
    segment_uid?: string | null;
    verse_key?: string;
    ref?: string;
    confidence?: number;
    missing_words?: number[];
    /** Boundary-review sidecar payload (hidden_pause / missed_waqf / false_split / unmarked_wasl items). */
    boundary?: { score?: number };
};

function quranKey(it: ItemFields): number[] {
    // Per-segment items: recitation index == mushaf order, and works for
    // `failed` (no ref) — it's the neighbour position the user asked for.
    if (typeof it.seg_index === 'number' && typeof it.chapter === 'number') {
        return [it.chapter, it.seg_index];
    }
    // Verse-scoped items (missing_words / missing_verses).
    if (typeof it.verse_key === 'string') {
        const [s, a] = it.verse_key.split(':');
        return [Number(s) || 0, Number(a) || 0];
    }
    // Fallback: the matched reference start "s:a:w-...".
    if (typeof it.ref === 'string') {
        const [s, a, w] = (it.ref.split('-')[0] ?? '').split(':');
        return [Number(s) || 0, Number(a) || 0, Number(w) || 0];
    }
    if (typeof it.chapter === 'number') return [it.chapter, 0];
    return [0];
}

/** #verses spanned by a cross-verse ref "s:a:w-s:a2:w2". */
function verseSpan(it: ItemFields): number {
    if (typeof it.ref !== 'string') return 1;
    const [start, end] = it.ref.split('-');
    if (!start || !end) return 1;
    const a = Number(start.split(':')[1]);
    const a2 = Number(end.split(':')[1]);
    if (Number.isNaN(a) || Number.isNaN(a2)) return 1;
    return Math.max(1, a2 - a + 1);
}

/** Ordering key for one item under one sort kind (numeric tuple, lexicographic). */
export function keyFor(kind: SortKind, item: SegValAnyItem, ctx?: SortCtx): number[] {
    const it = item as ItemFields;
    switch (kind) {
        case 'quran_order':
            return quranKey(it);
        case 'confidence':
            return [typeof it.confidence === 'number' ? it.confidence : 0];
        case 'word_count':
            return [it.missing_words?.length ?? 0];
        case 'verse_count':
            return [verseSpan(it)];
        case 'rep_split_count': {
            const uid = it.segment_uid;
            const entry = uid ? ctx?.autoSplitMap?.[uid] : undefined;
            return [entry?.refs.length ?? 1];
        }
        case 'score':
            return [it.boundary?.score ?? 0];
    }
}

function cmpTuple(a: number[], b: number[]): number {
    const n = Math.max(a.length, b.length);
    for (let i = 0; i < n; i++) {
        const d = (a[i] ?? 0) - (b[i] ?? 0);
        if (d !== 0) return d;
    }
    return 0;
}

/**
 * Order `items` by the active (kind, dir). Returns a new array; the original is
 * untouched. Ties on the active key are broken by the *other* registered option
 * (always ascending), so a count sort still reads top-to-bottom in Quran order
 * within each count bucket, and vice-versa.
 */
export function sortItems(
    items: readonly SegValAnyItem[],
    kind: SortKind,
    dir: SortDir,
    options: readonly SortOption[] | undefined,
    ctx?: SortCtx,
): SegValAnyItem[] {
    const secondary = options?.find((o) => o.kind !== kind)?.kind;
    const sign = dir === 'desc' ? -1 : 1;
    return items.slice().sort((a, b) => {
        const primary = sign * cmpTuple(keyFor(kind, a, ctx), keyFor(kind, b, ctx));
        if (primary !== 0) return primary;
        if (secondary) return cmpTuple(keyFor(secondary, a, ctx), keyFor(secondary, b, ctx));
        return 0;
    });
}

/** The active-by-default option for a category's sort list, or null if none. */
export function defaultOption(options: readonly SortOption[] | undefined): SortOption | null {
    if (!options || options.length === 0) return null;
    return options.find((o) => o.default) ?? options[0] ?? null;
}
