/**
 * FE-only view-models + derived read shapes (Segments tab).
 *
 * These have no single wire producer: the editor's working `Segment` is a
 * superset merge of two distinct wire rows plus client-only fields; the
 * `EditOp`/`EditOpPatch`/`HistoryBatch` shapes are the FE's view of the
 * edit-history presentation read (the persisted ops are modelled separately in
 * `qua_shared`); `GenerationBoundary`/`HistorySummary` are FE rollups; the
 * `Ref`/`VerseRef` string aliases narrow the bare-`string` wire fields. The
 * derived `/api/seg/*` reads (`SegEditHistoryResponse`, `SegStatsResponse`,
 * `SegSaveChartResponse`, `SegChaptersResponse`) have no generated model and
 * live here too. Codegen'd wire rows are imported from `./generated/schemas`.
 */

import type { Actor as GenActor, ErrorEnvelope, SegmentFlagView, SegWordTiming } from './generated/schemas';

// ---------------------------------------------------------------------------
// Reference strings: "surah:ayah[:word]" or compound "S:A:W-S:A:W"
// FE-only aliases — the wire carries these as bare `string`.
// ---------------------------------------------------------------------------

/** Word reference "surah:ayah:word" or word-range "S:A:W-S:A:W". */
export type Ref = string;

/** Verse reference "surah:ayah" (single) or "S:A-S:A2" (compound cross-verse). */
export type VerseRef = string;

// ---------------------------------------------------------------------------
// Actor (re-export of the generated identity stamp)
// ---------------------------------------------------------------------------

/** Actor identity stamped on flags / edit batches. */
export type Actor = GenActor;

// ---------------------------------------------------------------------------
// Working segment (view-model superset)
// ---------------------------------------------------------------------------

/**
 * The Segments-tab working segment — a FE-only view-model, not a wire row.
 *
 * The wire has TWO distinct shapes (`SegDataSegment` from `/data`, with a flat
 * `audio_url`; `SegAllSegment` from `/all`, with `chapter`/`segment_uid`/
 * `entry_ref`). The editor merges them into ONE mutable object and injects
 * client-only working fields (`matched_text`, `silence_after_ms`, `_derived`,
 * …) at load time, then mutates it in place through the edit pipeline. That
 * merged object is genuinely FE-only — no single generated row models it — so
 * it stays a real definition here, built as the superset of both wire rows
 * plus the client-only fields. The strict generated rows remain the element
 * types of `SegAllResponse.segments` / `SegDataResponse.segments`.
 */
export interface Segment {
    // Wire fields common to both rows
    index: number;
    entry_idx: number;
    time_start: number; // milliseconds
    time_end: number; // milliseconds
    matched_ref: Ref;
    confidence: number; // 0..1
    ignored_categories?: string[] | null;
    is_wasl?: boolean | null;
    /** Opaque — used by repetition detection. */
    wrap_word_ranges?: unknown;
    // From SegDataSegment (`/data`) — injected onto `/all` rows by the editor
    audio_url?: string;
    // From SegAllSegment (`/all`)
    chapter?: number;
    segment_uid?: string;
    entry_ref?: string;
    /** Manual "needs a second look" flag thread. Present (on /api/seg/all)
     *  only when the segment is flagged. */
    flag?: SegmentFlagView | null;
    /** Existing reviewed word boundaries, available on timing-review samples. */
    word_timings?: SegWordTiming[] | null;
    // ---- Client-only working fields (no wire producer) ----
    /** Reference text, populated client-side for display/edit. */
    matched_text?: string;
    /** Back-compat legacy boolean from pre-categories ignore flag. */
    ignored?: boolean;
    /** Client-computed silence gap to the next same-entry neighbour (ms);
     *  `null` when there is no downstream neighbour. */
    silence_after_ms?: number | null;
    silence_after_raw_ms?: number | null;
    /** Client-only flag for filter "neighbour" highlighting. */
    _isNeighbour?: boolean;
    /** Client-only cached derived reference info; cleared on ref edit. */
    _derived?: unknown;
}

// ---------------------------------------------------------------------------
// Edit operations / patches — FE working/derived shapes
// ---------------------------------------------------------------------------

/** Forward-change patch envelope produced by `applyCommand` and consumed by
 *  inverse-patch logic. FE working shape — `applyCommand` always builds every
 *  field, so all are required here (the persisted `EditOpPatch` schema tolerates
 *  partials). Structural mirror of the Python `SegmentPatch` dataclass. */
export interface EditOpPatch {
    before: Array<Record<string, unknown>>;
    after: Array<Record<string, unknown>>;
    removedIds: string[];
    insertedIds: string[];
    affectedChapterIds: number[];
}

/** Edit operation record — the FE working/derived op shape. Built client-side
 *  by `applyCommand` and echoed back in the `/api/seg/edit-history` derived
 *  presentation read (which has no Pydantic producer). The persisted on-disk op
 *  is modelled separately by `EditOperation` in `qua_shared`; this is the FE's
 *  view-model and carries the client-only `merge_direction` tag (set on merge
 *  ops, stripped before persisting) plus a finalize-time forward `patch`. */
export interface EditOp {
    op_id: string;
    op_type: string;
    op_context_category: string | null;
    fix_kind: string | null;
    targets_before: Array<Record<string, unknown>>;
    targets_after: Array<Record<string, unknown>>;
    /** Set on merge ops — `'prev'` or `'next'`. Client-only. */
    merge_direction?: 'prev' | 'next';
    /** Forward-change patch attached at finalize time; reversed on undo/discard. */
    patch?: EditOpPatch;
}

/** Edit history batch as returned by /api/seg/edit-history. FE-only read shape:
 *  the route derives a presentation form (`save_mode`/`is_revert`/
 *  `reverted_op_ids`, nullable `batch_id` for pending) that no Pydantic schema
 *  models — the persisted batch is `EditHistoryBatch` in `qua_shared`. */
export interface HistoryBatch {
    batch_id: string | null;
    batch_type: string | null;
    saved_at_utc: string | null;
    chapter: number | null;
    chapters?: number[];
    save_mode: string | null;
    is_revert: boolean;
    operations: EditOp[];
    reverted_op_ids?: string[];
}

// ---------------------------------------------------------------------------
// Edit-history rollups — FE-only (no generated producer modelled)
// ---------------------------------------------------------------------------

/** One TS-generation boundary in the edit-history timeline (ascending). The
 *  FE partitions edit batches into tiers split by these `produced_at` times.
 *  `published` marks a generation that an HF publish landed on. */
export interface GenerationBoundary {
    version: string | null;
    produced_at: string | null;
    published: boolean;
    published_at: string | null;
}

export interface HistorySummary {
    total_operations: number;
    total_batches: number;
    chapters_edited: number;
    op_counts: Record<string, number>;
    fix_kind_counts: Record<string, number>;
    /** Count of unique verse refs touched across all operations; optional for forward compat. */
    verses_edited?: number;
}

// ---------------------------------------------------------------------------
// Derived /api/seg/* reads — FE-only (no generated model)
// ---------------------------------------------------------------------------

/** GET /api/seg/chapters/:reciter — 404 returns {error}. FE-only union. */
export type SegChaptersResponse = number[] | ErrorEnvelope;

/** GET /api/seg/edit-history/:reciter. FE-only — the FE-shaped history read
 *  (HistoryBatch/HistorySummary/GenerationBoundary are FE rollups). */
export interface SegEditHistoryResponse {
    batches: HistoryBatch[];
    summary: HistorySummary | null;
    /** Ascending TS-generation boundaries — drives the history tier filter.
     *  Absent/empty when the reciter has never had timestamps generated. */
    generations?: GenerationBoundary[];
}

/** GET /api/seg/stats/:reciter — distributions + percentiles. FE-only (shape
 *  varies; no generated model). */
export interface SegStatsResponse {
    distributions?: Record<string, { bins: number[]; counts: number[]; percentiles?: Record<string, number> }>;
    vad_params?: {
        min_silence_ms: number;
        min_speech_ms?: number;
        pad_left_ms?: number;
        pad_right_ms?: number;
        min_silence_floor_ms?: number;
        [k: string]: unknown;
    };
    [k: string]: unknown;
}

/** POST /api/seg/stats/:reciter/save-chart (multipart). FE-only. */
export interface SegSaveChartResponse {
    ok?: boolean;
    path?: string;
    error?: string;
}
