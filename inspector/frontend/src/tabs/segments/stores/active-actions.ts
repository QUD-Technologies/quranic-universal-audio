/**
 * Active-row action registry — the bridge that lets the global keyboard
 * dispatcher (utils/keyboard.ts) invoke per-row / per-card edit actions without
 * re-deriving the target's DOM row, mountId, or validation category.
 *
 * The "primary" SegmentRow (the playing, non-context, editable row — in the
 * main list OR inside an open accordion card) publishes a callback bundle here.
 * Row-owned actions (adjust / split / edit-ref / goto / delete) are wired by
 * SegmentRow itself; card-owned actions (ignore / auto-fill / toggle-context)
 * are passed down as callbacks from the accordion card and forwarded into the
 * bundle (ignore / auto-fill / toggle-context / set-wasl). Keyboard shortcuts then call `get(activeRowActions)?.adjust?.()` etc.,
 * guaranteeing identical behaviour to clicking the row's buttons.
 *
 * `owner` is the publishing row's per-mount Symbol so a row only clears the
 * entry it actually set (twin rows / re-renders can't clobber each other).
 */

import { writable } from 'svelte/store';

export interface RowActionBundle {
    owner: symbol;
    chapter: number;
    index: number;
    uid: string | null;
    adjust?: () => void;
    split?: () => void;
    editRef?: () => void;
    goto?: () => void;
    delete?: () => void;
    ignore?: () => void;
    autofill?: () => void;
    toggleContext?: () => void;
    /** Label the card's WASL/WAQF boundary (← waṣl / → waqf). Published only
     *  by cross-verse cards that render exactly ONE boundary, so the key can
     *  never be ambiguous; absent everywhere else (the arrows then seek). */
    setWasl?: (value: boolean) => void;
}

export const activeRowActions = writable<RowActionBundle | null>(null);

export function publishRowActions(b: RowActionBundle): void {
    activeRowActions.set(b);
}

export function clearRowActions(owner: symbol): void {
    activeRowActions.update((cur) => (cur && cur.owner === owner ? null : cur));
}
