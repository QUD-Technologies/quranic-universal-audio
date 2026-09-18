/**
 * Segments keyboard-shortcut catalogue — the single source of truth for every
 * key the Segments tab listens for, shared by the dispatch resolver
 * (utils/keyboard.ts) and the footer reference/edit popover (ShortcutsGuide).
 *
 * Each action carries the CONTEXT pool it lives in:
 *   - 'default'   — main-list browsing. Also active inside an open accordion
 *                   (the accordion pool extends, never replaces, default).
 *   - 'accordion' — only while a validation accordion is open; acts on the
 *                   focused (playing, non-context) card row.
 *   - 'edit'      — only while in trim / split edit mode; the stepper + cursor
 *                   keys that override seek/seek.
 *
 * A key TOKEN is `e.code` with an optional `Ctrl+` prefix (e.g. `KeyA`,
 * `Space`, `ArrowUp`, `Comma`, `Ctrl+KeyS`). Only `rebindable` actions can be
 * remapped by the user; structural keys (arrows in edit, Tab, Enter, Escape,
 * Ctrl+S) are fixed and shown as reference only.
 */

import * as m from '../../../lib/paraglide/messages';

export type ShortcutContext = 'default' | 'accordion' | 'edit';

export interface ShortcutAction {
    /** Stable id — also the localStorage override key. */
    id: string;
    /** Human label shown in the popover. */
    label: () => string;
    /** Which dispatch pool this action belongs to. */
    context: ShortcutContext;
    /** Default key token (`e.code`, optional `Ctrl+` prefix). */
    defaultKey: string;
    /** False = fixed structural key (cannot be remapped). */
    rebindable: boolean;
}

/** Display grouping for the popover — each section maps 1:1 to a list of
 *  action ids, in render order. Kept separate from the flat catalogue so the
 *  resolver stays a simple id→action lookup. */
export interface ShortcutSection {
    title: () => string;
    /** One-line orientation shown under the section title. */
    hint: () => string;
    ids: string[];
}

export const SHORTCUT_ACTIONS: ShortcutAction[] = [
    // ---- Playback (default) ----
    { id: 'play_pause',    label: m.segments_shortcuts_action_play_pause,    context: 'default',   defaultKey: 'Space',      rebindable: false },
    { id: 'seek_back',     label: m.segments_shortcuts_action_seek_back,     context: 'default',   defaultKey: 'ArrowLeft',  rebindable: false },
    { id: 'seek_fwd',      label: m.segments_shortcuts_action_seek_fwd,      context: 'default',   defaultKey: 'ArrowRight', rebindable: false },
    { id: 'nav_prev',      label: m.segments_shortcuts_action_nav_prev,      context: 'default',   defaultKey: 'ArrowUp',    rebindable: false },
    { id: 'nav_next',      label: m.segments_shortcuts_action_nav_next,      context: 'default',   defaultKey: 'ArrowDown',  rebindable: false },
    { id: 'speed_down',    label: m.segments_shortcuts_action_speed_down,    context: 'default',   defaultKey: 'Comma',      rebindable: true },
    { id: 'speed_up',      label: m.segments_shortcuts_action_speed_up,      context: 'default',   defaultKey: 'Period',     rebindable: true },
    { id: 'autoscroll',    label: m.segments_shortcuts_action_autoscroll,    context: 'default',   defaultKey: 'KeyJ',       rebindable: true },
    { id: 'autoplay',      label: m.segments_shortcuts_action_autoplay,      context: 'default',   defaultKey: 'KeyK',       rebindable: true },

    // ---- Editing (default — acts on current / focused segment) ----
    { id: 'adjust',        label: m.segments_shortcuts_action_adjust,        context: 'default',   defaultKey: 'KeyA',       rebindable: true },
    { id: 'split',         label: m.segments_shortcuts_action_split,         context: 'default',   defaultKey: 'KeyS',       rebindable: true },
    { id: 'edit_ref',      label: m.segments_shortcuts_action_edit_ref,      context: 'default',   defaultKey: 'KeyE',       rebindable: true },
    { id: 'history',       label: m.segments_shortcuts_action_history,       context: 'default',   defaultKey: 'KeyH',       rebindable: true },
    { id: 'save',          label: m.segments_shortcuts_action_save,          context: 'default',   defaultKey: 'Ctrl+KeyS',  rebindable: false },

    // ---- Inside a flagged card (accordion) ----
    { id: 'goto',          label: m.segments_shortcuts_action_goto,          context: 'accordion', defaultKey: 'KeyG',       rebindable: true },
    { id: 'toggle_context',label: m.segments_shortcuts_action_toggle_context,context: 'accordion', defaultKey: 'KeyC',       rebindable: true },
    { id: 'ignore',        label: m.segments_shortcuts_action_ignore,        context: 'accordion', defaultKey: 'KeyL',       rebindable: true },
    { id: 'autofill',      label: m.segments_shortcuts_action_autofill,      context: 'accordion', defaultKey: 'KeyF',       rebindable: true },
    { id: 'mark_wasl',     label: m.segments_shortcuts_action_mark_wasl,     context: 'accordion', defaultKey: 'Digit1',     rebindable: true },
    { id: 'mark_waqf',     label: m.segments_shortcuts_action_mark_waqf,     context: 'accordion', defaultKey: 'Digit2',     rebindable: true },

    // ---- While adjusting / splitting (edit) ----
    { id: 'edit_step_back',label: m.segments_shortcuts_action_edit_step_back,context: 'edit',      defaultKey: 'ArrowLeft',  rebindable: false },
    { id: 'edit_step_fwd', label: m.segments_shortcuts_action_edit_step_fwd, context: 'edit',      defaultKey: 'ArrowRight', rebindable: false },
    { id: 'edit_cycle',    label: m.segments_shortcuts_action_edit_cycle,    context: 'edit',      defaultKey: 'Tab',        rebindable: false },
    { id: 'edit_replay',   label: m.segments_shortcuts_action_edit_replay,   context: 'edit',      defaultKey: 'KeyR',       rebindable: false },
    { id: 'edit_confirm',  label: m.segments_shortcuts_action_edit_confirm,  context: 'edit',      defaultKey: 'Enter',      rebindable: false },
    { id: 'edit_cancel',   label: m.segments_shortcuts_action_edit_cancel,   context: 'edit',      defaultKey: 'Escape',     rebindable: false },
];

export const SHORTCUT_SECTIONS: ShortcutSection[] = [
    {
        title: m.segments_shortcuts_section_playback_title,
        hint: m.segments_shortcuts_section_playback_hint,
        ids: ['play_pause', 'seek_back', 'seek_fwd', 'nav_prev', 'nav_next', 'speed_down', 'speed_up', 'autoscroll', 'autoplay'],
    },
    {
        title: m.segments_shortcuts_section_editing_title,
        hint: m.segments_shortcuts_section_editing_hint,
        ids: ['adjust', 'split', 'edit_ref', 'history', 'save'],
    },
    {
        title: m.segments_shortcuts_section_flagged_title,
        hint: m.segments_shortcuts_section_flagged_hint,
        ids: ['goto', 'toggle_context', 'ignore', 'autofill', 'mark_wasl', 'mark_waqf'],
    },
    {
        title: m.segments_shortcuts_section_adjusting_title,
        hint: m.segments_shortcuts_section_adjusting_hint,
        ids: ['edit_step_back', 'edit_step_fwd', 'edit_cycle', 'edit_replay', 'edit_confirm', 'edit_cancel'],
    },
];

const _BY_ID = new Map(SHORTCUT_ACTIONS.map((a) => [a.id, a]));

export function actionById(id: string): ShortcutAction | undefined {
    return _BY_ID.get(id);
}
