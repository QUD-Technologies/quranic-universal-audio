/**
 * O(1) DOM-ref registry for SegmentRow elements.
 *
 * Each non-readOnly SegmentRow registers itself on mount and deregisters on
 * destroy. The playback hot path (drawActivePlayhead, 60fps) reads from here
 * instead of calling querySelector on every frame.
 *
 * Plain Map — no Svelte reactivity. The playback loop never needs to subscribe
 * to registry changes; it just reads the current value per frame.
 *
 * Keyed by `${chapter}:${index}` so same-index rows in different chapters
 * (validation panels mounted with chapter=null span the whole corpus) don't
 * collide. The value is a Set of RowEntry so multiple mounts of the SAME
 * (chapter, index) can coexist — the primary main-list row AND any validation
 * accordion row showing the same segment both register, and drawActivePlayhead
 * paints a playhead on every twin.
 *
 * Each RowEntry carries a unique `mountId` so deregister disambiguates which
 * mount is unmounting without relying on the row/canvas element reference.
 */

import type { Segment } from '../../../../lib/types/view-models';
import type { SegCanvas } from '../../types/segments-waveform';

/** Which DOM context a registered row is mounted in. Mirrors the
 *  `instanceRole` prop on SegmentRow so the registry can answer
 *  "who has this (chapter, index) mounted and in what context?" —
 *  used by programmatic edit handoffs (chain after split, auto-fix)
 *  to prefer an accordion mount when one exists before falling back
 *  to the main-list row. */
export type RowInstanceRole = 'main' | 'accordion' | 'history' | 'preview';

export interface RowEntry {
    /** Unique per-mount identifier — used to disambiguate twin deregistration. */
    mountId: symbol;
    row: HTMLElement;
    canvas: SegCanvas | null;
    instanceRole: RowInstanceRole;
    /**
     * Staged (not-yet-dispatched) split piece this mount renders, when it is
     * one. Staged pieces all carry the PARENT's (chapter, index) — they have
     * no store identity of their own — so the registry key alone cannot tell
     * them apart, and the store segment's time window is the parent's, not
     * the piece's. The playhead draw layer reads this to paint each piece
     * against its own window, and the edit-handoff picker skips these mounts
     * (a staged row owns no edit affordances).
     */
    segOverride: Segment | null;
}

/** Map<"chapter:index", Set<RowEntry>>. Set preserves insertion order so the
 *  iteration order during drawActivePlayhead is stable (main-list row typically
 *  registers first). */
const _registry = new Map<string, Set<RowEntry>>();

function _key(chapter: number, index: number): string {
    return `${chapter}:${index}`;
}

export function registerRow(
    chapter: number,
    index: number,
    row: HTMLElement,
    canvas: HTMLCanvasElement | undefined,
    mountId: symbol,
    instanceRole: RowInstanceRole,
    segOverride: Segment | null = null,
): void {
    const key = _key(chapter, index);
    let bucket = _registry.get(key);
    if (!bucket) {
        bucket = new Set<RowEntry>();
        _registry.set(key, bucket);
    }
    bucket.add({ mountId, row, canvas: (canvas as SegCanvas) ?? null, instanceRole, segOverride });
}

export function deregisterRow(chapter: number, index: number, mountId: symbol): void {
    const key = _key(chapter, index);
    const bucket = _registry.get(key);
    if (!bucket) return;
    for (const entry of bucket) {
        if (entry.mountId === mountId) {
            bucket.delete(entry);
            break;
        }
    }
    if (bucket.size === 0) _registry.delete(key);
}

/** Iterator of all mounted entries for a (chapter, index) pair. Returns an
 *  empty iterable when none are mounted (accordion collapsed, row scrolled
 *  out of the virtualized window, etc.). */
export function getRowEntriesFor(chapter: number, index: number): Iterable<RowEntry> {
    return _registry.get(_key(chapter, index)) ?? EMPTY_ITER;
}

/** Resolve one mounted row entry by its mountId for a concrete
 *  (chapter, index) pair. Used by edit flows that need to preserve the
 *  initiating UI context across a mutation. */
export function getRowEntryForMount(
    chapter: number,
    index: number,
    mountId: symbol,
): RowEntry | null {
    for (const entry of getRowEntriesFor(chapter, index)) {
        if (entry.mountId === mountId) return entry;
    }
    return null;
}

const EMPTY_ITER: Iterable<RowEntry> = { [Symbol.iterator]: () => ({ next: () => ({ done: true, value: undefined }) }) };

export function clearRowRegistry(): void {
    _registry.clear();
}
