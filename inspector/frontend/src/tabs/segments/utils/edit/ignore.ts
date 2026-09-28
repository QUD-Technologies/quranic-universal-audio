/**
 * Ignore-issue dispatcher.
 *
 * Wraps `applyCommand({type: 'ignoreIssue', ...})` and the live-store glue:
 * resolves the seg's chapter, finalizes the EditOp through `dirty.ts`,
 * marks the chapter dirty, and refreshes the seg via the chapter store.
 * The Svelte card component dispatches this wrapper instead of building
 * an op + mutation inline.
 */

import { get } from 'svelte/store';

import type { Segment } from '../../../../lib/types/view-models';
import { applyCommand } from '../../domain/apply-command';
import { containedVerdicts, putVerdicts, resolvedVerdicts } from '../../domain/join-verdict';
import { reviewBoundary } from '../validation/join-review';
import { segValidation } from '../../stores/validation';
import type { StagedSplit } from '../validation/staged-split';
import { refreshSegInStore, selectedChapter } from '../../stores/chapter';
import {
    finalizeOp,
    markDirty,
    setPendingOp,
} from '../../stores/dirty';
import { isIgnoredFor } from '../validation/classified-issues';

/**
 * Mark `category` ignored on `seg` by dispatching an `ignoreIssue` command,
 * then commit the resulting op to the dirty store and refresh `seg` in
 * the chapter store.
 *
 * Returns false if the seg already has the category recorded (caller
 * should disable the Ignore button via reactive guard, but this is a
 * second line of defense for keyboard / programmatic entries).
 */
export function ignoreIssueOnSegment(seg: Segment, category: string, boundary?: StagedSplit | null): boolean {
    const item = get(segValidation)?.missed_waqf?.find((it) => it.segment_uid === seg.segment_uid);
    const asked = boundary ?? (item ? reviewBoundary(item) : null);
    const answers = category === 'missed_waqf' && asked ? containedVerdicts(seg, resolvedVerdicts(asked, [])) : [];
    if (isIgnoredFor(seg, category) && JSON.stringify(putVerdicts(seg.join_verdicts, answers)) === JSON.stringify(seg.join_verdicts ?? [])) return false;
    const segChapter = seg.chapter ?? parseInt(get(selectedChapter));

    // Reducer expects a uid-keyed slice. The dispatcher provides exactly the
    // target seg; auto-suppress is irrelevant for ignoreIssue (the category
    // append IS the suppression).
    const uid = seg.segment_uid;
    if (!uid) return false;
    const result = applyCommand(
        {
            byId: { [uid]: seg },
            idsByChapter: { [segChapter]: [uid] },
            selectedChapter: segChapter,
        },
        { type: 'ignoreIssue', segmentUid: uid, category, joinVerdicts: answers },
    );

    const updated = result.nextState.byId[uid];
    if (updated) {
        seg.confidence = updated.confidence;
        seg.join_verdicts = updated.join_verdicts;
        seg.ignored_categories = updated.ignored_categories
            ? [...updated.ignored_categories]
            : seg.ignored_categories;
        delete (seg as Segment & { _derived?: unknown })._derived;
    }
    markDirty(segChapter, seg.index);
    refreshSegInStore(seg);

    setPendingOp(null);
    if (result.patch) result.operation.patch = result.patch;
    finalizeOp(segChapter, result.operation);
    return true;
}
