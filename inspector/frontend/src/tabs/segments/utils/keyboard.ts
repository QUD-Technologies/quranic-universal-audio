/**
 * Segments-tab keyboard dispatch.
 *
 * A pressed key is turned into a context-scoped action via the user-editable
 * binding store (`shortcuts/store.ts`). Three contexts decide which pool of
 * bindings is live:
 *   - 'edit'      — trim / split mode. ←/→ drive the boundary stepper, Tab
 *                   cycles the active cursor/region, Enter/Escape confirm/cancel
 *                   (plus Space + ,/. for preview play + speed). Letters inert.
 *   - 'accordion' — a validation accordion is open. Accordion-pool actions
 *                   (goto / ignore / auto-fill / toggle-context) act on the
 *                   focused card via the active-row registry; ↑/↓ + autoplay
 *                   navigate the accordion sequence. Default-pool actions still
 *                   apply (acting on the focused card row). ←/→ answer a
 *                   waiting single WASL/WAQF boundary instead of seeking
 *                   (see `waslBoundaryKey`).
 *   - 'default'   — main-list browsing.
 *
 * Row/card edit actions (A/S/E/G/L/F/C) are dispatched through the
 * `activeRowActions` registry, which the primary SegmentRow publishes — so a
 * keyboard edit behaves identically to clicking that row's button (same
 * validation-category auto-ignore, mountId, overlay positioning).
 *
 * `handleSegmentsKey` returns `true` when it handled the event (the caller then
 * `preventDefault()`s).
 */

import { get } from 'svelte/store';

import { SIGN_IN_MESSAGES } from '../../../lib/sign-in-messages';
import { editingMode } from '../../../lib/stores/editing-mode';
import { openGuidesGate } from '../../../lib/stores/guides-gate';
import { openSignInModal } from '../../../lib/stores/sign-in-modal';
import { LS_KEYS } from '../../../lib/utils/constants';
import { shouldHandleKey } from '../../../lib/utils/keyboard-guard';
import { cycleSpeedStore, SEGMENTS_SPEEDS } from '../../../lib/utils/speed-control';
import { resolve, tokenFromEvent } from '../shortcuts/store.svelte';
import { activeRowActions, type RowActionBundle } from '../stores/active-actions';
import { segCurrentIdx } from '../stores/chapter';
import { isDirty } from '../stores/dirty';
import { editMode, soleWaslBoundaryCommit } from '../stores/edit';
import { displayedSegments } from '../stores/filters';
import { historyVisible } from '../stores/history';
import { savedFilterView } from '../stores/navigation';
import {
    autoPlayEnabled,
    autoScrollEnabled,
    playbackSpeed,
    segPort,
} from '../stores/playback';
import { savePreviewVisible } from '../stores/save';
import { valUiOpenCategory } from '../stores/validation';
import { accordionStep } from './accordion-nav';
import { EDIT_NUDGE_MS, KEY_SEEK_SECONDS } from './constants';
import { _restoreFilterView } from './data/navigation-actions';
import { exitEditMode, getEditingSeg } from './edit/common';
import { beginRefEdit } from './edit/reference';
import { confirmSplit, cycleSplitSelection, nudgeActiveSplitCursor, replayCurrentSplitSelection } from './edit/split';
import { confirmTrim, cycleTrimBoundary, nudgeActiveTrimBoundary, previewTrimAudio } from './edit/trim';
import { hideHistoryView, showHistoryView } from './history/actions';
import { onSegPlayClick, playFromSegment } from './playback/playback';
import { confirmSaveFromPreview, hideSavePreview, onSegSaveClick } from './save/actions';

/**
 * Honour the same edit gate as the `editGate` action for keyboard-initiated
 * edits — clicks go through `editGate`, but keyboard shortcuts would otherwise
 * bypass it. Returns `true` when editing is blocked (the caller must NOT
 * perform the mutation) and surfaces the right prompt: the sign-in modal for
 * anonymous, the guide onboarding modal for `guides_unread`. Other view reasons
 * (wrong-assignee, marked_ready, …) just block silently.
 */
function gateKeyboardEdit(): boolean {
    const mode = get(editingMode);
    if (mode.kind !== 'view') return false; // editable — let the edit proceed
    if (mode.viewReason === 'unauthenticated') {
        openSignInModal(null, SIGN_IN_MESSAGES.edit);
    } else if (mode.viewReason === 'guides_unread') {
        openGuidesGate('gate');
    }
    return true;
}

/** Invoke a registry-published row/card action. Returns false when no primary
 *  row is published (so the key is left unhandled). Mutating actions gate first
 *  — after confirming a target exists, so a blocked edit surfaces the prompt. */
function callRowAction(name: keyof RowActionBundle, gate: boolean): boolean {
    const bundle = get(activeRowActions);
    const fn = bundle?.[name];
    if (typeof fn !== 'function') return false;
    if (gate && gateKeyboardEdit()) return true;
    (fn as () => void)();
    return true;
}

/** ↑/↓ within the main list — seek-plays the prev/next displayed segment. */
function navDefault(dir: 1 | -1): boolean {
    const displayed = get(displayedSegments);
    if (!displayed || displayed.length === 0) return true;
    const curIdx = get(segCurrentIdx);
    const curPos = displayed.findIndex((s) => s.index === curIdx);
    let pos: number;
    if (dir < 0) pos = curPos > 0 ? curPos - 1 : 0;
    else pos = curPos >= 0 && curPos < displayed.length - 1 ? curPos + 1 : (curPos === -1 ? 0 : curPos);
    const t = displayed[pos];
    if (t) playFromSegment(t.index, t.chapter);
    return true;
}

/** ↑/↓ within the open accordion — plays the prev/next card (skips context). */
function navAccordion(dir: 1 | -1): boolean {
    const ref = accordionStep(dir);
    if (ref) playFromSegment(ref.index, ref.chapter, undefined, { isAccordionPlay: true });
    return true;
}

function seek(deltaSec: number): boolean {
    segPort.seek(segPort.currentTimeMs() + deltaSec * 1000);
    return true;
}

function cycleSpeed(dir: 'up' | 'down'): boolean {
    const rate = cycleSpeedStore(playbackSpeed, dir, LS_KEYS.SEG_SPEED, SEGMENTS_SPEEDS);
    // Write through the port directly — a reactive `$: el.playbackRate` block
    // races Svelte's `<select value>` DOM update and steals focus mid-keydown.
    segPort.setPlaybackRate(rate);
    return true;
}

function toggleAutoplay(): boolean {
    const next = !get(autoPlayEnabled);
    autoPlayEnabled.set(next);
    try { localStorage.setItem(LS_KEYS.SEG_AUTOPLAY, String(next)); } catch { /* ignore */ }
    return true;
}

function toggleHistory(): boolean {
    if (get(historyVisible)) hideHistoryView();
    else showHistoryView();
    return true;
}

function toggleAutoscroll(): boolean {
    const next = !get(autoScrollEnabled);
    autoScrollEnabled.set(next);
    try { localStorage.setItem(LS_KEYS.SEG_AUTOSCROLL, String(next)); } catch { /* ignore */ }
    return true;
}

function save(): boolean {
    if (!isDirty()) return false;
    if (gateKeyboardEdit()) return true;
    onSegSaveClick();
    return true;
}

/** Begin a reference edit. Prefers the focused-row registry (accordion card /
 *  playing row); falls back to the current displayed segment so E works on a
 *  paused main-list row even when no row published a bundle. */
function editRefAction(): boolean {
    if (callRowAction('editRef', true)) return true;
    const curIdx = get(segCurrentIdx);
    if (get(editMode) || curIdx < 0) return false;
    const seg = get(displayedSegments)?.find((s) => s.index === curIdx);
    if (!seg) return false;
    if (gateKeyboardEdit()) return true;
    beginRefEdit(seg, null);
    return true;
}

/** Trim / split edit-mode keys. `mode` is the active edit mode. */
function handleEditKey(e: KeyboardEvent, mode: 'trim' | 'split'): boolean {
    switch (e.code) {
        case 'Space':
            onSegPlayClick();
            return true;
        case 'Comma':
        case 'Period':
            return cycleSpeed(e.code === 'Period' ? 'up' : 'down');
        case 'ArrowLeft':
            if (mode === 'trim') nudgeActiveTrimBoundary(-EDIT_NUDGE_MS);
            else nudgeActiveSplitCursor(-EDIT_NUDGE_MS);
            return true;
        case 'ArrowRight':
            if (mode === 'trim') nudgeActiveTrimBoundary(EDIT_NUDGE_MS);
            else nudgeActiveSplitCursor(EDIT_NUDGE_MS);
            return true;
        case 'Tab':
            if (mode === 'trim') cycleTrimBoundary();
            else cycleSplitSelection(1);
            return true;
        case 'KeyR':
            if (mode === 'trim') previewTrimAudio(null, { mode: 'cold' });
            else replayCurrentSplitSelection();
            return true;
        case 'Enter': {
            const seg = getEditingSeg();
            if (seg) {
                if (gateKeyboardEdit()) return true;
                if (mode === 'trim') confirmTrim(seg);
                else confirmSplit(seg);
            }
            return true;
        }
        case 'Escape':
            exitEditMode();
            return true;
        default:
            return false;
    }
}

/**
 * ←/→ on a waiting cross-verse WASL/WAQF boundary — answers it in one
 * keypress (← waṣl, → waqf) instead of seeking, then lets the split chain
 * advance. Only fires for a card whose two pieces share a SINGLE boundary
 * (`soleWaslBoundaries`), so it reads the same whether the user is on the
 * first or the second part; a card with more boundaries is left to its
 * focused picker (Tab / Enter) and the arrows keep seeking. Returns true
 * when the key was consumed.
 */
function waslBoundaryKey(e: KeyboardEvent): boolean {
    if (e.code !== 'ArrowLeft' && e.code !== 'ArrowRight') return false;
    const commit = soleWaslBoundaryCommit();
    if (!commit) return false;
    if (gateKeyboardEdit()) return true;
    commit(e.code === 'ArrowLeft');
    return true;
}

export function handleSegmentsKey(e: KeyboardEvent): boolean {
    if (!shouldHandleKey(e, 'segments')) return false;

    const mode = get(editMode);
    if (mode === 'trim' || mode === 'split') {
        return handleEditKey(e, mode);
    }

    // Non-edit multi-purpose structural keys not part of the rebindable pools.
    if (e.code === 'Escape') {
        if (get(savePreviewVisible)) { hideSavePreview(); return true; }
        if (get(savedFilterView)) { _restoreFilterView(); return true; }
        return false;
    }
    if (e.code === 'Enter') {
        if (get(savePreviewVisible)) {
            if (gateKeyboardEdit()) return true;
            confirmSaveFromPreview();
            return true;
        }
        return false;
    }

    const ctx = get(valUiOpenCategory) !== null ? 'accordion' : 'default';
    if (ctx === 'accordion' && waslBoundaryKey(e)) return true;

    const actionId = resolve(tokenFromEvent(e), ctx);
    if (!actionId) return false;

    switch (actionId) {
        case 'play_pause':     onSegPlayClick(); return true;
        case 'seek_back':      return seek(-KEY_SEEK_SECONDS);
        case 'seek_fwd':       return seek(KEY_SEEK_SECONDS);
        case 'nav_prev':       return ctx === 'accordion' ? navAccordion(-1) : navDefault(-1);
        case 'nav_next':       return ctx === 'accordion' ? navAccordion(1) : navDefault(1);
        case 'speed_down':     return cycleSpeed('down');
        case 'speed_up':       return cycleSpeed('up');
        case 'autoscroll':     return toggleAutoscroll();
        case 'autoplay':       return toggleAutoplay();
        case 'history':        return toggleHistory();
        case 'save':           return save();

        // Row / card edit actions via the active-row registry.
        case 'adjust':         return callRowAction('adjust', true);
        case 'split':          return callRowAction('split', true);
        case 'edit_ref':       return editRefAction();
        case 'goto':           return callRowAction('goto', false);
        case 'toggle_context': return callRowAction('toggleContext', false);
        case 'ignore':         return callRowAction('ignore', true);
        case 'autofill':       return callRowAction('autofill', true);

        default:               return false;
    }
}
