/**
 * Staged cross-verse split — per-session WASL/WAQF picks made on a split
 * that has NOT been dispatched yet.
 *
 * A cross-verse card with a sidecar auto-split entry renders its N pieces
 * up front (see `utils/validation/staged-split.ts`) and asks for a label on
 * every inter-piece boundary. The picks live here, keyed by the parent
 * (root) uid, until all `cursors.length` slots are answered — then the card
 * dispatches ONE `split` command carrying `wasls[]` and clears the entry.
 *
 * Child uids are minted once per parent and memoised so re-renders don't
 * reshuffle them; the commit reuses the same uids so a staged row and its
 * committed twin share identity.
 *
 * Session-only. Cleared on reciter switch via `clear-per-reciter-state.ts`.
 */

import { writable } from 'svelte/store';

export type StagedPick = boolean | undefined;
export type StagedPicks = Record<string, StagedPick[]>;

export const stagedWaslPicks = writable<StagedPicks>({});

const _childUids = new Map<string, string[]>();

/** Record a pick for boundary `idx` of `parentUid` (slots sized to `n`). */
export function setStagedPick(parentUid: string, idx: number, value: boolean, n: number): void {
    stagedWaslPicks.update((all) => {
        const cur = all[parentUid] ?? [];
        const next: StagedPick[] = new Array<StagedPick>(n).fill(undefined);
        for (let i = 0; i < Math.min(cur.length, n); i++) next[i] = cur[i];
        next[idx] = value;
        return { ...all, [parentUid]: next };
    });
}

export function clearStagedPicks(parentUid: string): void {
    stagedWaslPicks.update((all) => {
        if (!(parentUid in all)) return all;
        const next = { ...all };
        delete next[parentUid];
        return next;
    });
    _childUids.delete(parentUid);
}

export function clearAllStagedPicks(): void {
    stagedWaslPicks.set({});
    _childUids.clear();
}

/** Stable child uids for the staged pieces after piece 0 (`n` = cursor count). */
export function stagedChildUidsFor(parentUid: string, n: number): string[] {
    const cur = _childUids.get(parentUid);
    if (cur && cur.length === n) return cur;
    const fresh = Array.from({ length: n }, () => crypto.randomUUID());
    _childUids.set(parentUid, fresh);
    return fresh;
}

/** True when every one of `n` boundaries has a pick. */
export function allPicked(picks: StagedPick[] | undefined, n: number): picks is boolean[] {
    if (!picks || picks.length !== n) return false;
    return picks.every((p) => p !== undefined);
}
