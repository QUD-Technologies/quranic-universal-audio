/**
 * SegmentCommand discriminated union + CommandResult shapes.
 *
 * Commands are the input grammar of `applyCommand` (the segments-domain
 * reducer). Each command names the target segment by `segmentUid` (or
 * `fromUid`/`toUid` for merge), carries the operation parameters, and
 * optionally pins the in-flight UI mount via `_mountId`. The reducer
 * translates a command into a `CommandResult` containing the mutated
 * segment slice (`nextState`), the EditOp record (`operation`), the
 * affected chapter ids, an optional validation delta, and an optional
 * patch (Phase 5 fills it).
 *
 * `_mountId` is a UI binding — it identifies which `SegmentRow` instance
 * owns the live edit. The reducer ignores it; the dispatcher edge uses
 * it for setEdit/clearEdit routing. It is carried on the command for the
 * dispatcher's convenience and never leaks into the operation log.
 */

import type { FlagAuthor, JoinVerdict } from '../../../lib/types/generated/schemas';
import type { EditOp, Segment } from '../../../lib/types/view-models';
import type { SegSnapshot } from '../stores/dirty';

// ---------------------------------------------------------------------------
// Operation tag
// ---------------------------------------------------------------------------

export type Operation =
    | 'trim'
    | 'split'
    | 'merge'
    | 'editReference'
    | 'delete'
    | 'ignoreIssue'
    | 'autoFixMissingWord'
    | 'setIsWasl'
    | 'flagSegment';

// ---------------------------------------------------------------------------
// Command shapes
// ---------------------------------------------------------------------------

export interface CommandBase {
    type: Operation;
    /** Category that triggered the command. Drives auto-suppress when set. */
    sourceCategory?: string | null;
    /** Operation-context category persisted on the EditOp. Defaults to sourceCategory. */
    contextCategory?: string | null;
    /** 'manual' | 'auto_fix' | 'audit' | 'ignore'. Defaults to 'manual'. */
    fixKind?: string;
    /** UI-mount binding consumed by the dispatcher edge. Reducer ignores it. */
    _mountId?: symbol | string | null;
}

export interface TrimCommand extends CommandBase {
    type: 'trim';
    segmentUid: string;
    /** Partial time-bound update; both fields optional, but at least one required. */
    delta: { time_start?: number; time_end?: number };
}

export interface SplitCommand extends CommandBase {
    type: 'split';
    segmentUid: string;
    /** Cursor times. ``number`` = today's single-cursor split (one cut, two
     *  pieces). ``number[]`` = repetitions auto-split (N cuts in ascending
     *  order, N+1 pieces). Single-value form is preserved so existing
     *  callers don't have to wrap. */
    splitMs: number | number[];
    /** Pre-allocated UIDs for the produced pieces *after* the first.
     *  Length = (cursors.length) for the array form. The first piece reuses
     *  the parent's UID. Falls back to UID generation when absent. */
    newUids?: string[];
    /** Per-section ref + text. Length = pieces.length when set; pieces with
     *  ``undefined`` entries inherit the parent's matched_ref/text. */
    refs?: (string | undefined)[];
    texts?: (string | undefined)[];
    /** Per-new-inter-child-boundary wasl flags. Length = cursors.length
     *  (one per new boundary). ``wasls[i]`` becomes ``is_wasl`` on child[i]
     *  (the left side of that boundary). When absent, all children default
     *  is_wasl=false except the LAST child which inherits the parent's
     *  is_wasl (which represented the parent-to-next-seg right edge). */
    wasls?: (boolean | undefined)[];
    joinVerdicts?: JoinVerdict[];
    // ---- Legacy single-cursor convenience fields (still accepted) --------
    firstRef?: string;
    secondRef?: string;
    firstText?: string;
    secondText?: string;
    secondHalfUid?: string;
}

export interface MergeCommand extends CommandBase {
    type: 'merge';
    joinVerdicts?: JoinVerdict[];
    /** Adjacent pair. The reducer determines the kept side by chapter order
     *  (the earlier seg's UID is preserved on the merged result). Either UID
     *  may be passed as `fromUid` or `toUid` — order isn't load-bearing. */
    fromUid: string;
    toUid: string;
    /** Resolved merged values from the dispatcher edge. */
    mergedRef?: string;
    mergedText?: string;
    /** Convenience direction tag — 'prev' if fromUid precedes toUid, else 'next'. */
    direction?: 'prev' | 'next';
}

export interface EditReferenceCommand extends CommandBase {
    type: 'editReference';
    segmentUid: string;
    matched_ref: string;
    matched_text?: string;
    /** Wire-level op_type to record. Defaults to 'edit_reference'; the
     *  dispatcher passes 'confirm_reference' when the user confirmed an
     *  unchanged ref to clear an audit/low-confidence flag. */
    opType?: 'edit_reference' | 'confirm_reference';
}

export interface DeleteCommand extends CommandBase {
    type: 'delete';
    segmentUid: string;
}

export interface IgnoreIssueCommand extends CommandBase {
    type: 'ignoreIssue';
    segmentUid: string;
    category: string;
    joinVerdicts?: JoinVerdict[];
}

export interface AutoFixMissingWordCommand extends CommandBase {
    type: 'autoFixMissingWord';
    segmentUid: string;
    /** New ref the auto-fix extends the segment to. */
    matched_ref: string;
    matched_text?: string;
}

/** Toggle the boundary wasl annotation. Owned by the LEFT seg of the
 *  boundary — ``is_wasl=true`` means "wasl-connected to the next adjacent
 *  segment". Per-segment scope; the reducer does not validate adjacency
 *  (the caller is expected to be a click on an inter-row affordance that
 *  only renders when there IS a right neighbour). */
export interface SetIsWaslCommand extends CommandBase {
    type: 'setIsWasl';
    segmentUid: string;
    is_wasl: boolean;
}

/** Flag a segment with a comment thread. The actor + timestamps are stamped
 *  server-side (never trusted from the client). ``intent`` distinguishes:
 *  - ``set``      — create a flag (comment required).
 *  - ``edit``     — replace the root comment (flagger only); an EMPTY comment
 *                   removes the whole flag (the unflag mechanism).
 *  - ``followup`` — append a reply (any editor; comment required).
 *  ``author`` is the optimistic FE-shape author used for immediate display
 *  (the next /all refetch carries the authoritative redacted shape). */
export interface FlagSegmentCommand extends CommandBase {
    type: 'flagSegment';
    segmentUid: string;
    comment: string;
    intent: 'set' | 'edit' | 'followup';
    /** Optimistic author for the new root/follow-up comment (current user). */
    author?: FlagAuthor;
}

export type SegmentCommand =
    | TrimCommand
    | SplitCommand
    | MergeCommand
    | EditReferenceCommand
    | DeleteCommand
    | IgnoreIssueCommand
    | AutoFixMissingWordCommand
    | SetIsWaslCommand
    | FlagSegmentCommand;

// ---------------------------------------------------------------------------
// Result shapes
// ---------------------------------------------------------------------------

/**
 * Patch description for inverse application. Phase 3 emits empty arrays as
 * a stub; Phase 5 fills `before`/`after`/`removedIds`/`insertedIds`.
 */
export interface SegmentPatch {
    before: SegSnapshot[];
    after: SegSnapshot[];
    removedIds: string[];
    insertedIds: string[];
    affectedChapterIds: number[];
}

/** Op-record shape returned alongside the reducer result. Adds the `kind`,
 *  `type`, `snapshots`, and `targetSegmentIndex` fields used by the command
 *  layer on top of the wire-level `EditOp`. The wire-level `op_type` and
 *  `targets_before`/`targets_after` fields are populated in parallel so the
 *  dispatcher can hand the same record to `finalizeOp`/save without
 *  reshaping it. */
export interface CommandOperation extends EditOp {
    /** Discriminator for downstream reducers and tests. Mirrors `Operation`. */
    type: Operation;
    /** 'single-index' for ignored/auto-suppress style edits, 'structural'
     *  for split/merge/delete/trim that move boundaries or alter list shape. */
    kind: 'single-index' | 'structural';
    /** Snapshot pair captured at create-time (before) and finalize-time (after). */
    snapshots: { before: SegSnapshot[]; after: SegSnapshot[] };
    /** Resolved (chapter, index) for the primary target — used by the dispatcher
     *  to route store updates and by save to tag the chapter. */
    targetSegmentIndex: { chapter: number; index: number };
    /** Affected chapters list mirrored on the op for save-time convenience. */
    affected_chapters?: number[];
    /** Direction tag for merge ops (kept here for save payload compatibility). */
    merge_direction?: 'prev' | 'next';
    /** Command envelope — required by the save handler validator (Phase 7).
     *  `command.type` must match the enclosing `op.type`. */
    command: { type: Operation; [k: string]: unknown };
}

/** Mutated state slice — `byId` carries inserted/updated segments; deleted
 *  uids appear in `removedSegmentUids`. `affectedChapter` is the primary
 *  chapter the dispatcher hands to `markDirty`. */
export interface CommandNextState {
    byId: Record<string, Segment>;
    affectedChapter: number;
    removedSegmentUids?: string[];
    insertedSegmentUids?: string[];
    /** New chapter ordering (uids) — set when the command rearranges/extends
     *  the chapter list (split/delete/merge). The dispatcher uses this to
     *  rewrite `idsByChapter` in Phase 4 normalized state. */
    idsByChapter?: Record<number, string[]>;
}

export interface CommandResult {
    nextState: CommandNextState;
    operation: CommandOperation;
    affectedChapters: number[];
    /** Validation hints — categories the command resolved (auto-suppressed)
     *  vs introduced. `resolved` always populated when auto-suppress fires;
     *  `introduced` is reserved for future flows. */
    validationDelta?: { resolved: string[]; introduced: string[] };
    /** Patch envelope describing the forward change. */
    patch: SegmentPatch;
}

// ---------------------------------------------------------------------------
// State input
// ---------------------------------------------------------------------------

/** Reducer input — segment slice keyed by uid plus chapter index lookup.
 *  `selectedChapter` is the active chapter when the command originates
 *  from the main list (used as a fallback when a segment lacks `chapter`). */
export interface ApplyCommandState {
    byId: Record<string, Segment>;
    idsByChapter: Record<number, string[]>;
    selectedChapter: number | null;
}

/** Optional reducer context — kept open for Phase 4 + Phase 5 extensions. */
export interface ApplyCommandContext {
    /** Override the uid generator (test seam). */
    uid?: () => string;
}
