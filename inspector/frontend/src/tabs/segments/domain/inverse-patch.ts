/**
 * Frontend port of `inspector/domain/command.py::apply_inverse_patch`.
 *
 * Operates on the flat `Segment[]` array carried by `segAllData.segments`
 * (no entries grouping needed — chapter membership is read off
 * `seg.chapter`).
 *
 * Used by the pending-discard path: each unsaved op carries a forward
 * `patch` (built by `applyCommand`), and inverting that patch undoes the
 * op without round-tripping through the server.
 *
 * Inverse:
 * 1. Drop segments whose uid ∈ `insertedIds` (created by the forward op).
 * 2. For each remaining seg whose uid is in `before` (and not in
 *    `removedIds`), replace it with a Segment rebuilt from the `before`
 *    snapshot — restores in-place mutations (trim / edit_reference /
 *    ignore_issue / auto_fix / confirm_reference).
 * 3. Re-insert segments whose uid ∈ `removedIds`, rebuilt from `before`
 *    (these were deleted by the forward op).
 * 4. Sort segments belonging to any chapter in `affectedChapterIds` by
 *    `time_start`. Other chapters keep their existing relative order.
 */

import type { EditOpPatch, Segment } from '../../../lib/types/view-models';

/** Snapshot keys we mirror onto a Segment. The snapshot was produced by
 *  `snapshotSeg` (stores/dirty.ts) which writes `index_at_save` rather
 *  than `index` — restore that mapping on the way back. */
function _segFromSnapshot(snap: Record<string, unknown>): Segment {
    const seg: Segment = {
        index: (snap.index_at_save as number) ?? 0,
        entry_idx: (snap.entry_idx as number) ?? 0,
        time_start: (snap.time_start as number) ?? 0,
        time_end: (snap.time_end as number) ?? 0,
        matched_ref: (snap.matched_ref as string) ?? '',
        matched_text: (snap.matched_text as string) ?? '',
        confidence: (snap.confidence as number) ?? 0,
        audio_url: (snap.audio_url as string) ?? '',
    };
    if (snap.segment_uid) seg.segment_uid = snap.segment_uid as string;
    if (snap.chapter != null) seg.chapter = snap.chapter as number;
    if (snap.entry_ref) seg.entry_ref = snap.entry_ref as string;
    if (snap.wrap_word_ranges) seg.wrap_word_ranges = snap.wrap_word_ranges;
    if (typeof snap.is_wasl === 'boolean') seg.is_wasl = snap.is_wasl;
    if (Array.isArray(snap.join_verdicts)) {
        seg.join_verdicts = (snap.join_verdicts as NonNullable<Segment['join_verdicts']>).map((j) => ({ ...j }));
    }
    if (Array.isArray(snap.ignored_categories) && snap.ignored_categories.length > 0) {
        seg.ignored_categories = [...(snap.ignored_categories as string[])];
    }
    return seg;
}

/** Apply the inverse of *patch* to *segments* and return a new array. Pure —
 *  the input array is not mutated. */
export function applyInversePatchToSegments(
    segments: Segment[],
    patch: EditOpPatch,
): Segment[] {
    // Index before-snaps by uid for O(1) lookup.
    const beforeByUid = new Map<string, Record<string, unknown>>();
    for (const snap of patch.before) {
        const uid = snap.segment_uid as string | undefined;
        if (uid) beforeByUid.set(uid, snap);
    }
    const insertedSet = new Set(patch.insertedIds);
    const removedSet = new Set(patch.removedIds);

    // 1. Drop inserted uids.
    let next = segments.filter((s) => {
        const uid = s.segment_uid;
        return !uid || !insertedSet.has(uid);
    });

    // 2. Restore in-place mutations.
    next = next.map((s) => {
        const uid = s.segment_uid;
        if (uid && beforeByUid.has(uid) && !removedSet.has(uid)) {
            return _segFromSnapshot(beforeByUid.get(uid)!);
        }
        return s;
    });

    // 3. Re-insert segments that the forward command removed.
    for (const uid of patch.removedIds) {
        const snap = beforeByUid.get(uid);
        if (!snap) continue;
        next.push(_segFromSnapshot(snap));
    }

    // 4. Sort affected chapters by time_start. Stable: pull each affected
    //    chapter's segs out, sort them, splice back at their original
    //    chapter-block positions. Other chapters' relative order is
    //    preserved.
    if (patch.affectedChapterIds.length > 0) {
        const affected = new Set(patch.affectedChapterIds);
        // Group: in-order positions of segs by chapter membership.
        // Strategy: walk once, partition segs into per-chapter buckets for
        // affected chapters; rebuild output by interleaving non-affected segs
        // with sorted affected segs in original positional order.
        const sortedByCh = new Map<number, Segment[]>();
        for (const ch of affected) {
            const segsInCh = next.filter((s) => (s.chapter ?? -1) === ch);
            segsInCh.sort((a, b) => a.time_start - b.time_start);
            sortedByCh.set(ch, segsInCh);
        }
        const result: Segment[] = [];
        const consumed = new Map<number, number>();
        for (const s of next) {
            const ch = s.chapter ?? -1;
            if (affected.has(ch)) {
                // Pull next sorted seg from this chapter's bucket.
                const i = consumed.get(ch) ?? 0;
                const sorted = sortedByCh.get(ch)!;
                const picked = sorted[i];
                if (picked) result.push(picked);
                consumed.set(ch, i + 1);
            } else {
                result.push(s);
            }
        }
        next = result;
    }

    return next;
}
