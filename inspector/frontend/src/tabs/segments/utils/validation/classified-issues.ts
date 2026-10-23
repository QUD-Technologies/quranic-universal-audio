/**
 * Helpers around the backend-supplied `classified_issues` field on
 * validation responses and history snapshots.
 *
 * The frontend stops classifying live segments locally — every category
 * decision originates from the backend's unified classifier. Live segments
 * inherit categories via the validation response (one
 * `classified_issues: string[]` per per-issue snapshot under each
 * category-keyed array); persisted history snapshots carry the same field
 * (populated when the save record is written).
 *
 * For unsaved snapshots (op-in-progress, before save), the field is
 * absent — the user has just edited and the next save+validate cycle is
 * the source of truth. `classifiedIssuesOf` returns an empty list in
 * that case; UI surfaces (e.g. history-delta badges) treat empty as
 * "no signal" and rely on the post-save delta instead.
 */

import type { EditOp } from '../../../../lib/types/view-models';

// Categories that exist in the accordion for review/awareness but should
// NOT surface as history-delta pills (`+cat` / `−cat`) on edit cards. They
// represent display-only flags or context, not gains/losses from an edit.
// `basmala_amin`: legacy category, still hidden in old history.
const HISTORY_NEUTRAL_CATEGORIES = new Set(['basmala_amin', 'muqattaat']);

/** Loose snapshot shape — anything carrying the optional field qualifies. */
export interface ClassifiableSnap {
    classified_issues?: string[];
    matched_ref?: string;
    segment_uid?: string | null;
}

/** Read the `classified_issues` array off a snapshot (or empty list). */
export function classifiedIssuesOf(snap: ClassifiableSnap | null | undefined): string[] {
    if (!snap || !Array.isArray(snap.classified_issues)) return [];
    return snap.classified_issues.filter((cat) => !HISTORY_NEUTRAL_CATEGORIES.has(cat));
}

/** Check whether a segment-like record opts out of a category.
 *
 *  Reads `ignored_categories` first; the legacy `_all` marker means
 *  "ignore everything"; falls back to the pre-categories `ignored=true`
 *  boolean for snapshots that predate the array shape.
 */
export function isIgnoredFor(
    seg: { ignored_categories?: string[] | null; ignored?: boolean } | null | undefined,
    category: string,
): boolean {
    if (!seg) return false;
    const ic = seg.ignored_categories;
    if (ic && ic.length) return ic.includes('_all') || ic.includes(category);
    return !!seg.ignored;
}

export interface OpIssueDelta {
    resolved: string[];
    introduced: string[];
    involved: string[];
}

/**
 * Compute the issue delta over a group of related ops.
 *
 * `resolved`   — categories present on the before-snapshots that don't
 *                appear on the after-snapshots (issues the edit fixed).
 * `introduced` — categories present on the after-snapshots that didn't
 *                appear on the before-snapshots (regressions).
 * `involved`   — categories present on either before or after snapshots.
 *
 * Reads `classified_issues` directly off each snapshot — populated by the
 * backend at save time. Snapshots without the field contribute nothing
 * (treated as "no signal"); the next save+validate cycle resurfaces any
 * categories that are still active.
 *
 * The after-snapshot dedup uses `segment_uid` so a chain of ops on the
 * same segment counts each surviving uid once (the last `targets_after`
 * for that uid wins). When no after-snapshot carries a uid, the group's
 * final op's `targets_after` is the fallback set.
 */
// Group arrays are stable per OpFlatItem (built once in `flatItems` derivation).
// The WeakMap caches the delta for the lifetime of the array reference; when
// `flatItems` republishes (history change), old groups are GC'd automatically.
// Same group is read twice per filter pass (HistoryBatch render + itemMatchesCatFilter),
// so the memo also amortizes that without any manual eviction.
const _deltaMemo = new WeakMap<EditOp[], OpIssueDelta>();

export function deriveOpIssueDelta(group: EditOp[] | null | undefined): OpIssueDelta {
    if (!group || group.length === 0) return { resolved: [], introduced: [], involved: [] };
    const cached = _deltaMemo.get(group);
    if (cached) return cached;

    const primary = group[0];
    if (!primary) return { resolved: [], introduced: [], involved: [] };

    const beforeIssues = new Set<string>();
    for (const snap of (primary.targets_before || [])) {
        for (const cat of classifiedIssuesOf(snap as ClassifiableSnap)) beforeIssues.add(cat);
    }

    const finalSnaps = new Map<string, ClassifiableSnap>();
    let hasAnyAfterUid = false;
    for (const op of group) {
        for (const snap of (op.targets_after || [])) {
            const s = snap as ClassifiableSnap;
            if (s.segment_uid) { finalSnaps.set(s.segment_uid, s); hasAnyAfterUid = true; }
        }
    }

    const afterSnaps: ClassifiableSnap[] = hasAnyAfterUid
        ? [...finalSnaps.values()]
        : ((group[group.length - 1]?.targets_after || []) as ClassifiableSnap[]);

    const afterIssues = new Set<string>();
    for (const snap of afterSnaps) {
        for (const cat of classifiedIssuesOf(snap)) afterIssues.add(cat);
    }

    const involved = new Set<string>([...beforeIssues, ...afterIssues]);

    const result: OpIssueDelta = {
        resolved:   [...beforeIssues].filter((i) => !afterIssues.has(i)),
        introduced: [...afterIssues].filter((i) => !beforeIssues.has(i)),
        involved:   [...involved],
    };
    _deltaMemo.set(group, result);
    return result;
}

/** Marker re-exported for tests that assert the post-Phase-2 helper exists. */
export const usesStoredClassifiedIssues = true;
