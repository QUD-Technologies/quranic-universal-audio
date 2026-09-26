/**
 * Issue Registry — TypeScript twin of
 * ``inspector/services/validation/registry.py``.
 *
 * Each row is the single source of truth for one validation category's
 * UI / persistence / suppression metadata. The Python and TS sides MUST
 * agree row-for-row; ``__tests__/registry/parity.test.ts`` enforces this.
 *
 * camelCase mirrors of the Python fields:
 *   kind / cardType / severity / accordionOrder / canIgnore /
 *   autoSuppress / persistsIgnore / scope / displayTitle / description.
 */

import type { SortOption } from './sorting';

export type CardType = 'generic' | 'missingWords' | 'missingVerses' | 'error';
export type Severity = 'error' | 'warning' | 'info';
export type Scope = 'per_segment' | 'per_verse' | 'per_chapter';

export interface IssueDefinition {
    kind: string;
    cardType: CardType;
    severity: Severity;
    accordionOrder: number;
    canIgnore: boolean;
    autoSuppress: boolean;
    persistsIgnore: boolean;
    scope: Scope;
    displayTitle: string;
    description: string;
    /**
     * Owner-only category: invisible to everyone but an owner. Dropped from the
     * validation accordion, from the mark-ready blocking gate, and from the
     * required editing-guide set — for non-owners the category may as well not
     * exist. Mirrors the Python registry's `owner_only`. Defaults to false.
     */
    ownerOnly?: boolean;
    /**
     * Sort options the accordion offers (first = active default). FE-only
     * presentation concern — deliberately absent from the Python registry and
     * its parity snapshot. Omit to offer no sorting (Missing Verses). See
     * `./sorting.ts`.
     */
    sorts?: readonly SortOption[];
    /**
     * Offer the Unset · Wasl · Waqf boundary chips + filter in the accordion
     * header (cross-verse and missed-waqf). FE-only presentation concern, absent from
     * the Python registry. See `utils/validation/boundary-state.ts`.
     */
    boundaryFilter?: boolean;
}

export const IssueRegistry: Readonly<Record<string, IssueDefinition>> = Object.freeze({
    failed: {
        kind: 'failed',
        cardType: 'generic',
        severity: 'error',
        accordionOrder: 1,
        canIgnore: false,
        autoSuppress: true,
        persistsIgnore: false,
        scope: 'per_segment',
        displayTitle: 'Failed Alignments',
        description: '',
        sorts: [{ kind: 'quran_order', default: true }],
    },
    missing_verses: {
        kind: 'missing_verses',
        cardType: 'missingVerses',
        severity: 'error',
        accordionOrder: 2,
        canIgnore: false,
        autoSuppress: true,
        persistsIgnore: false,
        scope: 'per_verse',
        displayTitle: 'Missing Verses',
        description: '',
    },
    missing_words: {
        kind: 'missing_words',
        cardType: 'missingWords',
        severity: 'error',
        accordionOrder: 3,
        canIgnore: false,
        autoSuppress: false,
        persistsIgnore: false,
        scope: 'per_verse',
        displayTitle: 'Missing Words',
        description: '',
        sorts: [{ kind: 'quran_order', default: true }, { kind: 'word_count' }],
    },
    structural_errors: {
        kind: 'structural_errors',
        cardType: 'error',
        severity: 'error',
        accordionOrder: 4,
        canIgnore: false,
        autoSuppress: true,
        persistsIgnore: false,
        scope: 'per_chapter',
        displayTitle: 'Structural Errors',
        description: '',
        sorts: [{ kind: 'quran_order', default: true }],
    },
    low_confidence: {
        kind: 'low_confidence',
        cardType: 'generic',
        severity: 'warning',
        accordionOrder: 5,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'Low Confidence',
        description: '',
        sorts: [{ kind: 'confidence', default: true }, { kind: 'quran_order' }],
    },
    low_confidence_v2: {
        kind: 'low_confidence_v2',
        cardType: 'generic',
        severity: 'warning',
        accordionOrder: 6,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'Low Confidence v2',
        description: 'MFA tight-beam probe disagreed with the DP alignment for these segments. Treat as a second-opinion warning.',
        sorts: [{ kind: 'quran_order', default: true }],
    },
    repetitions: {
        kind: 'repetitions',
        cardType: 'generic',
        severity: 'warning',
        accordionOrder: 9,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'Detected Repetitions',
        description: '',
        sorts: [{ kind: 'quran_order', default: true }, { kind: 'rep_split_count' }],
    },
    audio_bleeding: {
        kind: 'audio_bleeding',
        cardType: 'generic',
        severity: 'warning',
        accordionOrder: 7,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'Audio Bleeding',
        description: '',
        sorts: [{ kind: 'quran_order', default: true }],
    },
    boundary_adj: {
        kind: 'boundary_adj',
        cardType: 'generic',
        severity: 'warning',
        accordionOrder: 8,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'May Require Boundary Adjustment',
        description: '',
        ownerOnly: true,
        sorts: [{ kind: 'quran_order', default: true }],
    },
    cross_verse: {
        kind: 'cross_verse',
        cardType: 'generic',
        severity: 'warning',
        accordionOrder: 10,
        canIgnore: false,
        autoSuppress: false,
        persistsIgnore: false,
        scope: 'per_segment',
        displayTitle: 'Cross-verse',
        description: 'Label each verse boundary WASL or WAQF — the split is pre-applied from the aligner and saves on the last label. Adjust a piece if the suggested cut is off.',
        sorts: [{ kind: 'quran_order', default: true }, { kind: 'verse_count' }],
        boundaryFilter: true,
    },
    qalqala: {
        kind: 'qalqala',
        cardType: 'generic',
        severity: 'info',
        accordionOrder: 12,
        canIgnore: false,
        autoSuppress: false,
        persistsIgnore: false,
        scope: 'per_segment',
        displayTitle: 'Qalqala',
        description: '',
        ownerOnly: true,
        sorts: [{ kind: 'quran_order', default: true }],
    },
    muqattaat: {
        kind: 'muqattaat',
        cardType: 'generic',
        severity: 'info',
        accordionOrder: 13,
        canIgnore: false,
        autoSuppress: false,
        persistsIgnore: false,
        scope: 'per_segment',
        displayTitle: 'Muqattaʼat',
        description: '',
        sorts: [{ kind: 'quran_order', default: true }],
    },
    basmala_amin: {
        kind: 'basmala_amin',
        cardType: 'generic',
        severity: 'info',
        accordionOrder: 14,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'Basmala + Amin',
        description: '',
        sorts: [{ kind: 'quran_order', default: true }],
    },
    hidden_pause: {
        kind: 'hidden_pause',
        cardType: 'generic',
        severity: 'info',
        accordionOrder: 15,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'Hidden Pause (review)',
        description: 'Re-segmentation found a pause inside this segment. Auto Split places the proposed cut; ignore if there is no pause.',
        sorts: [{ kind: 'score', default: true }, { kind: 'quran_order' }],
    },
    missed_waqf: {
        kind: 'missed_waqf',
        cardType: 'generic',
        severity: 'info',
        accordionOrder: 11,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'Missed Waqf (review)',
        description: 'An offline detector heard the reciter stop inside this segment. Label each proposed cut: WAQF cuts there, WASL keeps it joined. The split saves on the last label; all WASL ignores the item.',
        sorts: [{ kind: 'score', default: true }, { kind: 'quran_order' }],
        boundaryFilter: true,
    },
    false_split: {
        kind: 'false_split',
        cardType: 'generic',
        severity: 'info',
        accordionOrder: 16,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'False Split (review)',
        description: "Re-segmentation heard continuous speech across this segment's end. Merge with the next segment if the cut is inside speech; ignore if the pause is real.",
        sorts: [{ kind: 'score', default: true }, { kind: 'quran_order' }],
    },
    unmarked_wasl: {
        kind: 'unmarked_wasl',
        cardType: 'generic',
        severity: 'info',
        accordionOrder: 17,
        canIgnore: true,
        autoSuppress: true,
        persistsIgnore: true,
        scope: 'per_segment',
        displayTitle: 'Unmarked Wasl (review)',
        description: 'Every re-segmentation arm read straight through this verse-to-verse join with no stop, but the boundary is not marked wasl. Mark it wasl (or merge if it is one utterance); ignore if there is a real stop.',
        sorts: [{ kind: 'score', default: true }, { kind: 'quran_order' }],
    },
});

const _entries = Object.entries(IssueRegistry) as [string, IssueDefinition][];

export const ALL_CATEGORIES: readonly string[] = _entries.map(([k]) => k);
export const PER_SEGMENT_CATEGORIES: readonly string[] = _entries
    .filter(([, v]) => v.scope === 'per_segment').map(([k]) => k);
export const PER_VERSE_CATEGORIES: readonly string[] = _entries
    .filter(([, v]) => v.scope === 'per_verse').map(([k]) => k);
export const PER_CHAPTER_CATEGORIES: readonly string[] = _entries
    .filter(([, v]) => v.scope === 'per_chapter').map(([k]) => k);
export const CAN_IGNORE_CATEGORIES: readonly string[] = _entries
    .filter(([, v]) => v.canIgnore).map(([k]) => k);
export const AUTO_SUPPRESS_CATEGORIES: readonly string[] = _entries
    .filter(([, v]) => v.autoSuppress).map(([k]) => k);
export const PERSISTS_IGNORE_CATEGORIES: readonly string[] = _entries
    .filter(([, v]) => v.persistsIgnore).map(([k]) => k);
export const OWNER_ONLY_CATEGORIES: readonly string[] = _entries
    .filter(([, v]) => v.ownerOnly).map(([k]) => k);

/** True iff `category` is owner-only and so must be hidden from this viewer. */
export function isCategoryHidden(category: string, isOwner: boolean): boolean {
    return !isOwner && (IssueRegistry[category]?.ownerOnly ?? false);
}

/**
 * Drop categories whose registry entry has ``persistsIgnore=false``.
 * The legacy ``"_all"`` marker passes through unchanged.
 */
export function filterPersistentIgnores(categories: readonly string[] | undefined | null): string[] {
    if (!categories) return [];
    const out: string[] = [];
    for (const cat of categories) {
        if (cat === '_all') {
            out.push(cat);
            continue;
        }
        const defn = IssueRegistry[cat];
        if (!defn || defn.persistsIgnore) out.push(cat);
    }
    return out;
}

/** Display titles indexed by category — derived for UI labels. */
export const ERROR_CAT_LABELS: Readonly<Record<string, string>> = Object.freeze(
    Object.fromEntries(_entries.map(([k, v]) => [k, v.displayTitle])),
);
