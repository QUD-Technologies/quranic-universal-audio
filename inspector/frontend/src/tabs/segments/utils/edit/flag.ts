/**
 * Flag dispatcher.
 *
 * Wraps `applyCommand({type: 'flagSegment', ...})` and the live-store glue:
 * resolves the seg's chapter, optimistically updates `seg.flag`, finalizes the
 * EditOp through `dirty.ts`, and marks the chapter dirty so the normal save
 * flow (autosave or manual) persists it. The actor + timestamps are stamped
 * server-side — `author` is only the optimistic display identity.
 *
 * Mirrors `ignore.ts`: a flag is just another single-index edit that rides the
 * existing op-log + save pipeline.
 */

import { get } from 'svelte/store';

import type { FlagAuthor } from '../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../lib/types/view-models';
import { applyCommand } from '../../domain/apply-command';
import type { FlagIntent } from '../../domain/command';
import { refreshSegInStore, selectedChapter } from '../../stores/chapter';
import { finalizeOp, markDirty, setPendingOp } from '../../stores/dirty';

/**
 * Dispatch a flag mutation on `seg`:
 *  - `set`      — create a flag (comment required).
 *  - `edit`     — replace the root comment; an EMPTY comment clears the flag.
 *  - `followup` — append a reply (comment required).
 *  - `delete`   — remove the whole thread (`segments.delete_flag` holders).
 *
 * Returns false on a no-op (no uid, or a required comment was blank).
 */
export function flagSegment(
    seg: Segment,
    comment: string,
    intent: FlagIntent,
    author: FlagAuthor,
): boolean {
    const uid = seg.segment_uid;
    if (!uid) return false;
    const trimmed = comment.trim();
    // `edit` (unflag) and `delete` allow an empty comment; set/followup don't.
    if (intent !== 'edit' && intent !== 'delete' && !trimmed) return false;
    const segChapter = seg.chapter ?? parseInt(get(selectedChapter));

    const result = applyCommand(
        {
            byId: { [uid]: seg },
            idsByChapter: { [segChapter]: [uid] },
            selectedChapter: segChapter,
        },
        { type: 'flagSegment', segmentUid: uid, comment: trimmed, intent, author },
    );

    const updated = result.nextState.byId[uid];
    if (updated) {
        seg.flag = updated.flag;
        delete (seg as Segment & { _derived?: unknown })._derived;
    }
    markDirty(segChapter, seg.index);
    refreshSegInStore(seg);

    setPendingOp(null);
    if (result.patch) result.operation.patch = result.patch;
    finalizeOp(segChapter, result.operation);
    return true;
}
