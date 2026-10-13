/** Cross-verse connected-reading helpers for native occasions. */

import type { ChapterOccasion } from './occasions';

export interface WaslGroup {
    fromIdx: number;
    toIdx: number;
    refs: string[];
    startMs: number;
    endMs: number;
}

export function waslGroupOf(occasions: ChapterOccasion[], focusIdx: number): WaslGroup {
    let from = focusIdx;
    let to = focusIdx;
    while (from > 0 && occasions[from - 1]!.waslOutTo === occasions[from]!.ref) from--;
    while (to < occasions.length - 1 && occasions[to]!.waslOutTo === occasions[to + 1]!.ref) to++;
    const members = occasions.slice(from, to + 1);
    const parts = members.flatMap((occasion) => occasion.parts);
    return {
        fromIdx: from,
        toIdx: to,
        refs: members.map((occasion) => occasion.ref),
        startMs: Math.min(...parts.map((part) => part.t[0])),
        endMs: Math.max(...parts.map((part) => part.t[1])),
    };
}

export function isInWaslGroup(occasions: ChapterOccasion[], focusIdx: number): boolean {
    const occasion = occasions[focusIdx];
    if (!occasion) return false;
    return Boolean(
        occasion.waslOutTo
        || (focusIdx > 0 && occasions[focusIdx - 1]!.waslOutTo === occasion.ref),
    );
}
