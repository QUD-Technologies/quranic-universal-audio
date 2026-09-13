/**
 * Mark-ready blocking counts — the FE projection of each blocking category,
 * matching what the accordion badge shows rather than the raw server array.
 *
 *  - `low_confidence`: items under the FE default threshold only.
 *  - everything else: stale-filtered length, minus items the server marks
 *    `resolved` (a cross-verse split kept for review — it no longer blocks;
 *    only an unlabelled boundary does).
 */

import type {
    SegValAnyItem,
    SegValLowConfidenceItem,
    SegValidateResponse,
} from '../../../../lib/types/generated/schemas';
import type { BlockingCountKey } from '../../copy/mark-ready';
import { filterStaleIssues } from './stale';

function isResolved(item: SegValAnyItem): boolean {
    return (item as { resolved?: boolean }).resolved === true;
}

export function blockingCountFor(
    key: BlockingCountKey,
    v: SegValidateResponse | null,
    liveUids: Set<string>,
    lcDefault: number,
): number {
    if (!v) return 0;
    const slot = (v as unknown as Record<string, SegValAnyItem[] | undefined>)[key];
    if (!slot || slot.length === 0) return 0;
    const live = filterStaleIssues(slot, liveUids).filter((it) => !isResolved(it));
    if (key === 'low_confidence') {
        let n = 0;
        for (const item of live as SegValLowConfidenceItem[]) {
            if (item.confidence * 100 < lcDefault) n++;
        }
        return n;
    }
    return live.length;
}
