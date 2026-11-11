/**
 * Segments tab — dirty state + operation log store.
 *
 * Tracks which chapters have unsaved edits (`segDirtyMap`), the per-chapter
 * operation log (`segOpLog`), and the current in-progress operation
 * (`pendingOp`). Provides helper functions for creating, snapshotting,
 * finalizing ops and marking/querying dirty state.
 *
 * Map keys are always `number` (never string-cast).
 *
 * `snapshotSeg` produces a structural copy of a `Segment` — no
 * classification happens client-side. Categories are derived from the
 * backend at save time (the save endpoint enriches each persisted
 * snapshot with `classified_issues`).
 */

import { derived, writable } from 'svelte/store';

import type { EditOp, Segment } from '../../../lib/types/view-models';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Dirty-map entry — edited indices plus structural-change flag. */
export interface DirtyEntry {
    indices: Set<number>;
    structural: boolean;
}

/** Snapshot of a segment captured at op-start / op-end. Shape mirrors `Segment`
 *  with a few client-added flags (`index_at_save`). The backend save endpoint
 *  enriches each persisted snapshot with a `classified_issues: string[]`
 *  field at write time. Loose-typed so downstream history rendering doesn't
 *  have to cast every read. */
export type SegSnapshot = Record<string, unknown>;

export interface CreateOpOptions {
    contextCategory?: string | null;
    fixKind?: string;
}

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------

/** Tracks which chapters have unsaved edits. Keys are ALWAYS `number`. */
const _dirtyMap = new Map<number, DirtyEntry>();

/** Per-chapter operation log. Keys are ALWAYS `number`. */
const _opLog = new Map<number, EditOp[]>();

/** Current in-progress operation (between createOp and finalizeOp). */
let _pendingOp: EditOp | null = null;

/** Reactive tick that bumps on every dirty-state mutation so Svelte
 *  components can derive reactive views (e.g. save-button enabled,
 *  per-row .dirty class). Subscribing components will re-run reactive
 *  blocks that read the dirty map after any mutation. */
export const dirtyTick = writable<number>(0);
function _bump(): void {
    dirtyTick.update((n) => n + 1);
}

/** Derived store: true while any chapter has unsaved edits. */
export const isDirtyStore = derived(dirtyTick, () => _dirtyMap.size > 0);

// ---------------------------------------------------------------------------
// Operation helpers
// ---------------------------------------------------------------------------

export function createOp(opType: string, { contextCategory = null, fixKind = 'manual' }: CreateOpOptions = {}): EditOp {
    return {
        op_id: crypto.randomUUID(),
        op_type: opType,
        op_context_category: contextCategory,
        fix_kind: fixKind,
        targets_before: [],
        targets_after: [],
    };
}

export function snapshotSeg(seg: Segment): SegSnapshot {
    // Migration #5: matched_text + phonemes_asr are no longer carried on
    // snapshots — matched_text is derivable from matched_ref via
    // dk_text_for_ref; phonemes_asr was retired entirely and the schema
    // pre-validator strips it on read.
    const snap: SegSnapshot = {
        segment_uid: seg.segment_uid || null,
        index_at_save: seg.index,
        audio_url: seg.audio_url || null,
        time_start: seg.time_start,
        time_end: seg.time_end,
        matched_ref: seg.matched_ref || '',
        confidence: seg.confidence ?? 0,
    };
    // Migration #5: has_repeated_words is a boolean tautology of
    // `Boolean(wrap_word_ranges)` — the classifier ignores it (only
    // wrap_word_ranges drives the repetitions category). Dropped.
    if (seg.wrap_word_ranges) snap.wrap_word_ranges = seg.wrap_word_ranges;
    if (seg.entry_ref) snap.entry_ref = seg.entry_ref;
    if (seg.chapter != null) snap.chapter = seg.chapter;
    if (seg.ignored_categories?.length) snap.ignored_categories = [...seg.ignored_categories];
    // is_wasl is a boundary annotation captured by the in-card WASL/WAQF
    // picker. Snapshots in op.targets_after carry the committed value so
    // the EditChainRow split-leaf renderer can render its wasl/waqf tag
    // straight from history without consulting the live seg.
    if (seg.is_wasl) snap.is_wasl = true;
    if (seg.join_verdicts) snap.join_verdicts = seg.join_verdicts.map((j) => ({ ...j }));
    return snap;
}

export function finalizeOp(chapter: number, op: EditOp): void {
    if (!_opLog.has(chapter)) _opLog.set(chapter, []);
    _opLog.get(chapter)!.push(op);
    _pendingOp = null;
}

// ---------------------------------------------------------------------------
// Dirty-state helpers
// ---------------------------------------------------------------------------

/**
 * Mark a chapter as dirty (has unsaved edits).
 *
 * When `structural=true` the `indices` set becomes advisory only — save takes
 * the full-replace path for that chapter, so specific edited indices stop
 * being load-bearing once any structural op has landed in the batch.
 *
 * @param chapter - chapter number (MUST be number, not string)
 * @param index - optional segment index to mark as edited
 * @param structural - whether this is a structural change (split/merge/delete)
 */
export function markDirty(chapter: number, index?: number, structural = false): void {
    if (!_dirtyMap.has(chapter)) {
        _dirtyMap.set(chapter, { indices: new Set(), structural: false });
    }
    const entry = _dirtyMap.get(chapter)!;
    if (index !== undefined) entry.indices.add(index);
    if (structural) entry.structural = true;
    _bump();
}

export function unmarkDirty(chapter: number, index: number): void {
    const entry = _dirtyMap.get(chapter);
    if (!entry) return;
    entry.indices.delete(index);
    if (entry.indices.size === 0 && !entry.structural) {
        _dirtyMap.delete(chapter);
    }
    _bump();
}

export function isDirty(): boolean {
    return _dirtyMap.size > 0;
}

export function isIndexDirty(chapter: number, index: number): boolean {
    const entry = _dirtyMap.get(chapter);
    return entry ? entry.indices.has(index) : false;
}

/** Whether the given segment (chapter, index) currently has unsaved edits.
 *  Pair with `$dirtyTick` in reactive `$:` blocks so the derivation fires
 *  after mutations. */
export function isSegmentDirty(chapter: number, index: number): boolean {
    return isIndexDirty(chapter, index);
}

/** Readonly snapshot of a chapter's op log for display/inspection callers that
 *  must not mutate the underlying array. Internal writers use getChapterOps. */
export function getChapterOpsSnapshot(chapter: number): readonly EditOp[] {
    return _opLog.get(chapter) ?? [];
}

// ---------------------------------------------------------------------------
// Pending op accessors
// ---------------------------------------------------------------------------

export function getPendingOp(): EditOp | null {
    return _pendingOp;
}

export function setPendingOp(op: EditOp | null): void {
    _pendingOp = op;
}

// ---------------------------------------------------------------------------
// Map accessors (read-only views + mutation)
// ---------------------------------------------------------------------------

/** Read-only reference to the dirty map. Callers should not mutate directly
 *  except via the provided helpers (markDirty/unmarkDirty/clearDirtyMap). */
export function getDirtyMap(): Map<number, DirtyEntry> {
    return _dirtyMap;
}

/** Read-only reference to the op log. */
export function getOpLog(): Map<number, EditOp[]> {
    return _opLog;
}

/** Get ops for a specific chapter. */
export function getChapterOps(chapter: number): EditOp[] {
    return _opLog.get(chapter) || [];
}

/**
 * Mutate every seg snapshot for ``segmentUid`` inside ``op`` to carry the
 * given ``fields`` overlay (e.g. ``{ is_wasl: true }``). Walks
 * ``targets_after``, ``snapshots.after``, and ``patch.after`` (when
 * present) so the save payload, history-row snapshot, and inverse-patch
 * undo all stay in sync.
 *
 * Used by the post-CV-split wasl flow: the user's WASL/WAQF pick amends
 * the parent split op's snapshots in place instead of emitting a fresh
 * ``set_is_wasl`` op (which would pollute the edit-history view with one
 * row per pick). ``_bump()`` nudges ``dirtyTick`` so reactive subscribers
 * (history panel, dirty banner) re-derive.
 *
 * No-op if no snapshot in ``op`` matches the UID. Safe to call on any op
 * shape — only matching snapshots are touched.
 */
export function amendSegInOp(
    op: EditOp,
    segmentUid: string,
    fields: Record<string, unknown>,
): void {
    const apply = (arr: Array<Record<string, unknown>> | undefined): void => {
        if (!Array.isArray(arr)) return;
        for (const snap of arr) {
            if (snap && (snap as { segment_uid?: string }).segment_uid === segmentUid) {
                Object.assign(snap, fields);
            }
        }
    };
    apply(op.targets_after);
    const snaps = (op as EditOp & { snapshots?: { after?: Array<Record<string, unknown>> } })
        .snapshots;
    if (snaps && Array.isArray(snaps.after)) apply(snaps.after);
    if (op.patch && Array.isArray(op.patch.after)) {
        apply(op.patch.after as Array<Record<string, unknown>>);
    }
    _bump();
}

/** Delete dirty entry for a chapter. */
export function deleteDirtyEntry(chapter: number): void {
    _dirtyMap.delete(chapter);
    _bump();
}

/** Delete op log for a chapter. */
export function deleteOpLogEntry(chapter: number): void {
    _opLog.delete(chapter);
}

/** Replace the chapter's op log with *ops* (or delete if empty).
 *
 *  Used by the pending-discard path after reverse-applying a card's ops:
 *  it filters them out and writes the remainder back. Bumps `dirtyTick`
 *  so subscribers see the change. */
export function setChapterOps(chapter: number, ops: EditOp[]): void {
    if (ops.length === 0) {
        _opLog.delete(chapter);
    } else {
        _opLog.set(chapter, ops);
    }
    _bump();
}

/** Recompute `_dirtyMap[chapter]` from the kept *ops*. Drops the entry
 *  entirely when the list is empty. The `structural` flag is set if any
 *  op is structural (`split_segment`, `merge_segments`, `delete_segment`,
 *  `trim_segment`, `auto_fix_missing_word` — see save full_replace
 *  semantics). `indices` is rebuilt from `op.targetSegmentIndex` when
 *  available; otherwise advisory-empty (structural=true forces
 *  full_replace anyway).
 *
 *  Used after a partial discard to bring the dirty entry back in line
 *  with the kept ops. */
export function recomputeDirtyEntryFromOps(chapter: number, ops: EditOp[]): void {
    if (ops.length === 0) {
        _dirtyMap.delete(chapter);
        _bump();
        return;
    }
    const indices = new Set<number>();
    let structural = false;
    for (const op of ops) {
        const t = op.op_type;
        if (
            t === 'split_segment' ||
            t === 'merge_segments' ||
            t === 'delete_segment' ||
            t === 'trim_segment' ||
            t === 'auto_fix_missing_word'
        ) {
            structural = true;
        }
        const tsi = (op as EditOp & { targetSegmentIndex?: { index: number } })
            .targetSegmentIndex;
        if (tsi && typeof tsi.index === 'number') indices.add(tsi.index);
    }
    _dirtyMap.set(chapter, { indices, structural });
    _bump();
}

/** Clear all dirty state (after successful save). */
export function clearDirtyMap(): void {
    _dirtyMap.clear();
    _bump();
}

/** Clear all op log entries (after successful save). */
export function clearOpLog(): void {
    _opLog.clear();
}

/** Clear only the specified operations from a chapter's op log and recompute dirty state.
 *  Used by autosave to safely drop successfully saved edits without clobbering new
 *  edits that were queued concurrently. */
export function clearSavedOps(chapter: number, savedOps: EditOp[]): void {
    const currentOps = _opLog.get(chapter);
    if (!currentOps) return;

    const savedOpIds = new Set(savedOps.map(o => o.op_id));
    const remainingOps = currentOps.filter(o => !savedOpIds.has(o.op_id));

    if (remainingOps.length === 0) {
        _opLog.delete(chapter);
        _dirtyMap.delete(chapter);
    } else {
        _opLog.set(chapter, remainingOps);
        // Do not call recomputeDirtyEntryFromOps directly because it also bumps dirtyTick, 
        // we'll just inline the logic here or let it bump twice.
        const indices = new Set<number>();
        let structural = false;
        for (const op of remainingOps) {
            const t = op.op_type;
            if (
                t === 'split_segment' ||
                t === 'merge_segments' ||
                t === 'delete_segment' ||
                t === 'trim_segment' ||
                t === 'auto_fix_missing_word'
            ) {
                structural = true;
            }
            const tsi = (op as EditOp & { targetSegmentIndex?: { index: number } }).targetSegmentIndex;
            if (tsi && typeof tsi.index === 'number') indices.add(tsi.index);
        }
        _dirtyMap.set(chapter, { indices, structural });
    }
    _bump();
}
