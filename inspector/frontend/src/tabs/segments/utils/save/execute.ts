/**
 * Execute the save operation — iterate dirty chapters, POST to server,
 * clean up dirty state on success.
 */

import { get as storeGet } from 'svelte/store';

import { type ApiErrorBody,friendlyError } from '../../../../lib/errors/friendly';
import * as m from '../../../../lib/paraglide/messages';
import { SIGN_IN_MESSAGES } from '../../../../lib/sign-in-messages';
import { openSignInModal } from '../../../../lib/stores/sign-in-modal';
import { pushToast } from '../../../../lib/stores/toast';
import type { SegSaveResponse } from '../../../../lib/types/generated/schemas';
import type { EditOp, Segment } from '../../../../lib/types/view-models';
import {
    getChapterSegments,
    selectedReciter,
} from '../../stores/chapter';
import {
    clearSavedOps,
    getChapterOps,
    getDirtyMap,
} from '../../stores/dirty';
import { saveButtonLabel } from '../../stores/save';
import { resetHistoryLoader } from '../history/loader';
import { refreshValidation } from '../validation/refresh';
export { buildPayloadFromCommandResult } from './payload';

// ---------------------------------------------------------------------------
// Payload types
// ---------------------------------------------------------------------------

// Migration #5: matched_text + phonemes_asr are not sent. The server derives
// matched_text from matched_ref via dk_text_for_ref; phonemes_asr was retired
// from the disk shape entirely (the schema pre-validator strips it on read).
interface SaveSegmentPayloadFull {
    segment_uid: string;
    time_start: number;
    time_end: number;
    matched_ref: string;
    confidence: number;
    audio_url: string;
    wrap_word_ranges?: unknown;
    ignored_categories?: string[];
    is_wasl?: boolean;
}

interface SaveSegmentPayloadPatch {
    index: number;
    segment_uid: string;
    matched_ref: string;
    confidence: number;
    ignored_categories?: string[];
    is_wasl?: boolean;
}

interface SavePayloadFull {
    full_replace: true;
    segments: SaveSegmentPayloadFull[];
    operations: EditOp[];
}

interface SavePayloadPatch {
    segments: SaveSegmentPayloadPatch[];
    operations: EditOp[];
}

// ---------------------------------------------------------------------------
// CommandResult → save payload bridge
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// executeSave
// ---------------------------------------------------------------------------

function isChapterHalfDirty(chOps: EditOp[]): boolean {
    const splitOps = chOps.filter(o => o.op_type === 'split_segment');
    if (splitOps.length === 0) return false;

    for (const splitOp of splitOps) {
        // A staged cross-verse commit carries `wasls[]` on its command: refs
        // came from the sidecar and every boundary was labelled before the
        // op existed, so there is no ref-confirm chain to wait for.
        const cmd = (splitOp as { command?: { wasls?: unknown } }).command;
        if (cmd && Array.isArray(cmd.wasls)) continue;
        const afterSegs = splitOp.targets_after as Record<string, any>[] | undefined;
        if (!afterSegs || afterSegs.length < 2) continue;
        
        const uid1 = afterSegs[0]!.segment_uid as string;
        const uid2 = afterSegs[1]!.segment_uid as string;
        
        if (!uid1 || !uid2) continue;

        let uid1Confirmed = false;
        let uid2Confirmed = false;

        const splitIndex = chOps.indexOf(splitOp);
        for (let i = splitIndex + 1; i < chOps.length; i++) {
            const o = chOps[i];
            if (!o) continue;
            
            if (o.op_type === 'edit_reference' || o.op_type === 'confirm_reference') {
                const ta = o.targets_after as Record<string, any>[] | undefined;
                if (ta && ta[0] && ta[0].segment_uid === uid1) uid1Confirmed = true;
                if (ta && ta[0] && ta[0].segment_uid === uid2) uid2Confirmed = true;
            }
            
            if (o.op_type === 'merge_segments' || o.op_type === 'delete_segment') {
                const tb = o.targets_before as Record<string, any>[] | undefined;
                if (tb) {
                    if (tb.some(t => t.segment_uid === uid1)) uid1Confirmed = true;
                    if (tb.some(t => t.segment_uid === uid2)) uid2Confirmed = true;
                }
            }
        }

        if (!uid1Confirmed || !uid2Confirmed) return true;
    }
    
    return false;
}

let _isSaving = false;
let _saveQueued = false;
let _queuedIsAutoSave = true;

export async function executeSave(isAutoSave = false): Promise<void> {
    if (_isSaving) {
        _saveQueued = true;
        if (!isAutoSave) _queuedIsAutoSave = false;
        return;
    }
    
    const reciter = storeGet(selectedReciter);
    if (!reciter) return;

    _isSaving = true;
    _saveQueued = false;
    const isCurrentRunAutoSave = isAutoSave;
    _queuedIsAutoSave = true;

    saveButtonLabel.set('Saving...');

    let savedChanges = 0;
    let savedChapters = 0;
    let allOk = true;

    try {
        // Snapshot the operations and build payloads BEFORE yielding to network IO
        // so that concurrent edits arriving during the fetch don't get mixed in.
        const pendingSaves = [];
        // Successfully-saved (chapter, ops) tuples that still need their pending
        // ops cleared from the op log. Held back until edit-history has been
        // refreshed so the op is always reachable in either pending OR batches
        // — never in neither.
        const pendingClears: Array<{ ch: number; ops: EditOp[] }> = [];

        for (const [ch, entry] of getDirtyMap()) {
            const chOps = [...getChapterOps(ch)]; // copy array of current ops
            
            // Skip autosave for chapters with an incomplete (half-dirty) split
            if (isCurrentRunAutoSave && isChapterHalfDirty(chOps)) {
                continue;
            }
            
            const chSegs: Segment[] = getChapterSegments(ch);
            let payload: SavePayloadFull | SavePayloadPatch | null = null;

            if (entry.structural) {
                payload = {
                    full_replace: true,
                    segments: chSegs.map(s => {
                        const o: SaveSegmentPayloadFull = {
                            segment_uid: s.segment_uid || '',
                            time_start: s.time_start,
                            time_end: s.time_end,
                            matched_ref: s.matched_ref,
                            confidence: s.confidence,
                            audio_url: s.audio_url || '',
                            ignored_categories: s.ignored_categories ?? [],
                        };
                        if (s.wrap_word_ranges) o.wrap_word_ranges = s.wrap_word_ranges;
                        o.is_wasl = s.is_wasl === true;
                        return o;
                    }),
                    operations: chOps,
                };
            } else {
                const updates: SaveSegmentPayloadPatch[] = [];
                for (const idx of entry.indices) {
                    const seg = chSegs.find(s => s.index === idx);
                    if (seg) {
                        const upd: SaveSegmentPayloadPatch = {
                            index: seg.index,
                            segment_uid: seg.segment_uid || '',
                            matched_ref: seg.matched_ref,
                            confidence: seg.confidence,
                            ignored_categories: seg.ignored_categories ?? [],
                            is_wasl: seg.is_wasl === true,
                        };
                        updates.push(upd);
                    }
                }
                if (updates.length > 0) {
                    payload = { segments: updates, operations: chOps };
                }
            }

            if (!payload) continue;

            // History-row peaks are generated server-side at save time by
            // slicing the baked chapter peaks (services/audio/op_peaks.py) — no
            // client payload needed.
            pendingSaves.push({ chapter: ch, payload, ops: chOps });
        }

        // Execute network requests for captured snapshots.
        // We use raw `fetch` so non-2xx responses surface a visible toast —
        // `fetchJson` swallows the HTTP status and the UI was failing silently
        // (Saving... stuck because dirty state never cleared on a 403/500).
        for (const { chapter: ch, payload, ops } of pendingSaves) {
            let res: Response;
            try {
                res = await fetch(`/api/seg/save/${reciter}/${ch}`, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            } catch (e) {
                console.error(`Save network error (ch ${ch}):`, e);
                pushToast({
                    kind: 'error',
                    text: m.segments_save_network_error_toast(),
                    ttl: 6000,
                });
                allOk = false;
                break;
            }
            if (!res.ok) {
                let body: ApiErrorBody | undefined;
                try {
                    body = await res.json() as ApiErrorBody;
                } catch { /* non-JSON body */ }
                // Keep the raw backend prose for the console; show friendly copy.
                console.error(`Save error (ch ${ch}, ${res.status}):`, body?.error ?? '(no body)');
                if (res.status === 401) {
                    openSignInModal(null, SIGN_IN_MESSAGES.save);
                } else {
                    pushToast({ kind: 'error', text: friendlyError(body, res.status), ttl: 6000 });
                }
                allOk = false;
                break;
            }
            let result: SegSaveResponse & { error?: string };
            try {
                result = await res.json() as SegSaveResponse & { error?: string };
            } catch {
                pushToast({
                    kind: 'error',
                    text: m.segments_save_malformed_response_toast(),
                    ttl: 6000,
                });
                allOk = false;
                break;
            }
            if (!result.ok) {
                console.error(`Save error (ch ${ch}):`, result.error);
                pushToast({
                    kind: 'error',
                    text: result.error || m.segments_save_unknown_error_toast(),
                    ttl: 6000,
                });
                allOk = false;
                break;
            }
            // Defer clearSavedOps until after the validation refresh below.
            // The post-save validate response carries the updated split-group
            // closure under ``split_group_index``; clearing the op log before
            // it lands would leave getSplitGroupMembers walking only the
            // parent uid for a frame and blink a fresh child out of the open
            // accordion card.
            pendingClears.push({ ch, ops });
            savedChanges += ops.length;
            savedChapters++;
        }

        if (savedChapters > 0) {
            if (allOk) {
                const msg = savedChapters > 1
                    ? m.segments_save_success_multi_chapter({ changes: savedChanges, chapters: savedChapters })
                    : m.segments_save_success_single_chapter({ count: savedChanges });
                saveButtonLabel.set(msg);
                setTimeout(() => { saveButtonLabel.set('Save'); }, 2500);
            } else {
                saveButtonLabel.set('Save');
            }

            // Refresh validation FIRST (it ships the new split_group_index),
            // then atomically apply the deferred pending-op clears.
            // clearSavedOps bumps dirtyTick which the validation-card memo
            // for splits depends on — landing the new index before clearing
            // the op log avoids a transient blink of the second child.
            // History panel: the lazy loader memoizes per-reciter, so reset
            // it so the next showHistoryView() actually re-fetches the
            // batches list (including the batch we just appended). Without
            // this, /api/seg/edit-history is served from the FE's cached
            // promise and the user sees stale rows.
            try {
                await refreshValidation();
            } catch (e) {
                console.error('Error refreshing validation:', e);
            }
            for (const { ch, ops } of pendingClears) clearSavedOps(ch, ops);
            resetHistoryLoader();
        } else {
            // Nothing saved (either nothing to save or error before first commit)
            saveButtonLabel.set('Save');
        }
    } catch (e) {
        console.error('Save failed:', e);
        saveButtonLabel.set('Save');
    } finally {
        _isSaving = false;
        if (_saveQueued) {
            // Give a short breather, then process queued save
            const isAuto = _queuedIsAutoSave;
            setTimeout(() => { void executeSave(isAuto); }, 50);
        }
    }
}
