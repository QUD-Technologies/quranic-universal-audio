/**
 * Segments tab — validation data store.
 *
 * Shape: single `SegValidateResponse | null`.
 *
 * Items carry `segment_uid` for stable identity through structural edits.
 * Stale items (uid absent from live state) are filtered before render by
 * `filterStaleIssues` in ValidationPanel.
 *
 * `waslRecheck` mirrors the payload's `wasl_recheck` uids: boundaries whose
 * WASL / WAQF answer is re-asked render as unset until answered.
 */

import { derived, writable } from 'svelte/store';

import type { SegValidateResponse } from '../../../lib/types/generated/schemas';
import { getOpLog } from './dirty';

/** Validation data for the currently-loaded reciter, or null if none loaded. */
export const segValidation = writable<SegValidateResponse | null>(null);

/** `"<chapter>:<index>"` of every segment a `missing_words` item points at.
 *  Sample-mode rows read this to show the missing-words chip; `seg_indices`
 *  are chapter-relative, the same numbering as `/all`'s `index`. */
export const missingWordsSegKeys = derived(segValidation, ($v) => {
    const keys = new Set<string>();
    for (const item of $v?.missing_words ?? []) {
        for (const idx of item.seg_indices ?? []) keys.add(`${item.chapter}:${idx}`);
    }
    return keys;
});

/** Server-supplied split-group closures keyed by root uid. Read by accordion
 *  cards to expand a split chain without subscribing to historyData. Empty
 *  map until the first validate response lands. */
export const splitGroupIndex = derived(
    segValidation,
    ($v) => ($v?.split_group_index ?? {}) as Record<string, string[]>,
);

/** Left-piece uids of boundaries whose WASL / WAQF answer is re-asked.
 *  Reseeded from every validate payload (empty when none is loaded), minus
 *  uids already answered by an unsaved `set_is_wasl` op; the picker drops a
 *  uid once answered. */
export const waslRecheck = writable<Set<string>>(new Set());

function _answeredInOpLog(): Set<string> {
    const out = new Set<string>();
    for (const ops of getOpLog().values()) {
        for (const op of ops) {
            if (op.op_type !== 'set_is_wasl') continue;
            for (const snap of op.targets_before) {
                const uid = snap.segment_uid;
                if (typeof uid === 'string' && uid) out.add(uid);
            }
        }
    }
    return out;
}

segValidation.subscribe(($v) => {
    const answered = _answeredInOpLog();
    waslRecheck.set(new Set(($v?.wasl_recheck ?? []).filter((uid) => !answered.has(uid))));
});

/** Drop `uid` from the re-check set once its boundary has been answered. */
export function resolveWaslRecheck(uid: string): void {
    waslRecheck.update((s) => {
        if (!s.has(uid)) return s;
        const next = new Set(s);
        next.delete(uid);
        return next;
    });
}

// ---- UI state persistence (in-memory) ----
export const valUiOpenCategory = writable<string | null>(null);
export const valUiLcThreshold = writable<number | null>(null);
export const valUiScrollTop = writable<number>(0);
export const valUiMeasuredCardHeight = writable<number | null>(null);

/** True iff a validation accordion is open. Accordion view and chapter-cards
 *  view are mutually exclusive — `SegmentsTab` gates `<SegmentsList>` on
 *  `!$accordionViewActive`. Derived (not a writable) so external mutations
 *  can't desync it from the source of truth. */
export const accordionViewActive = derived(
    valUiOpenCategory,
    ($c) => $c !== null,
);

/** Set validation data (e.g. after fetching /api/seg/validate). */
export function setValidation(data: SegValidateResponse): void {
    segValidation.set(data);
}

/** Clear validation data (e.g. on reciter change / clear). */
export function clearValidation(): void {
    segValidation.set(null);
    valUiOpenCategory.set(null);
    valUiScrollTop.set(0);
}
