import type { JoinVerdict, SegDataResponse } from '../../../lib/types/generated/schemas';
import type { PeakBucket } from '../../../lib/types/peaks-transport';
import type { EditOp, HistoryBatch } from '../../../lib/types/view-models';

// ---------------------------------------------------------------------------
// Split chain + history types
// ---------------------------------------------------------------------------

export interface EditChainOp {
    op: EditOp;
    batch: HistoryBatch;
}

/** Narrow view of a segment snapshot as referenced by history views. Loose
 *  by design — unknown fields preserved via index signature. */
export interface HistorySnapshot {
    join_verdicts?: JoinVerdict[] | null;
    index_at_save?: number;
    segment_uid?: string;
    audio_url?: string;
    /** Chapter (surah) the snapshot belongs to. Stamped by extraction on
     *  pipeline-op snapshots; may be undefined on legacy records. */
    chapter?: number;
    time_start: number;
    time_end: number;
    matched_ref?: string;
    matched_text?: string;
    confidence?: number;
    wrap_word_ranges?: unknown;
    [k: string]: unknown;
}

/** Group of related operations chained by segment lineage. */
export interface EditChain {
    rootSnap?: HistorySnapshot;
    rootBatch: HistoryBatch;
    ops: EditChainOp[];
    latestDate: string;
}

/** Flattened history display item produced by `flattenBatchesToItems`. */
export interface OpFlatItem {
    type: 'op-card' | 'strip-specials-card' | 'multi-chapter-card' | 'revert-card';
    group: EditOp[];
    chapter: number | null;
    chapters?: number[];
    batchId: string | null;
    date: string;
    isRevert: boolean;
    isPending: boolean;
    batchIdx: number;
    groupIdx: number;
}

// ---------------------------------------------------------------------------
// Filter types
// ---------------------------------------------------------------------------

/** One active filter row — field + comparator + literal value. */
export interface SegActiveFilter {
    field: string;
    op: string;
    value: number | null;
}

/** Saved UI snapshot so navigation.ts can restore a filter + scroll view. */
export interface SegSavedFilterView {
    filters: SegActiveFilter[];
    chapter: string;
    verse: string;
    scrollTop: number;
}

// ---------------------------------------------------------------------------
// Data state types
// ---------------------------------------------------------------------------

/** Augmented `SegDataResponse` — client may overwrite audio_url with a proxy URL. */
export type SegDataState = SegDataResponse;

// ---------------------------------------------------------------------------
// Dirty state
// ---------------------------------------------------------------------------

/** Dirty-map entry — edited indices plus structural-change flag. */
export interface DirtyEntry {
    indices: Set<number>;
    structural: boolean;
}

// ---------------------------------------------------------------------------
// Accordion / edit context
// ---------------------------------------------------------------------------

/** Snapshot of the edit-chain state captured while showing the save preview. */
export interface SavedChainsSnapshot {
    editChains: Map<string, EditChain> | null;
    chainedOpIds: Set<string> | null;
}

/** Saved scroll position snapshot around showSavePreview. */
export interface SegSavedPreviewState {
    scrollTop: number;
}

// ---------------------------------------------------------------------------
// Peaks (covering-range + observer queue)
// ---------------------------------------------------------------------------

/** Segment-level peaks entry keyed by URL (covering-range cache).
 *
 * ``peaks`` is ``PeakBucket[]`` for ffmpeg fallback slices (30 bps floats) and
 * ``Int8Array`` for history-peaks records decoded from the b64 wire shape
 * (10 bps). Both are handled transparently by ``peaks-view.ts`` /
 * ``draw-seg.ts``. */
export interface SegPeaksRangeEntry {
    startMs: number;
    endMs: number;
    peaks: PeakBucket[] | Int8Array;
    durationMs: number;
}

// ---------------------------------------------------------------------------
// Timer handles
// ---------------------------------------------------------------------------

export type TimerHandle = ReturnType<typeof setTimeout>;
export type RafHandle = number;

// ---------------------------------------------------------------------------
// Preview loop mode
// ---------------------------------------------------------------------------

/** `_previewLooping` flag. ``split-left`` / ``split-right`` cover the
 *  binary (single-cursor) split; ``split-region-{i}`` covers the N≥2
 *  multi-cursor split (region ``i`` runs between cursor ``i-1`` and
 *  cursor ``i``, with seg endpoints as sentinels). */
export type PreviewLoopMode =
    | false
    | 'trim'
    | 'split-left'
    | 'split-right'
    | `split-region-${number}`;

// ---------------------------------------------------------------------------
// Classification / ops
// ---------------------------------------------------------------------------

export interface CreateOpOptions {
    contextCategory?: string | null;
    fixKind?: string;
}

/** Snapshot of a segment captured at op-start / op-end. */
export type SegSnapshot = Record<string, unknown>;
