/**
 * Segments keyboard-shortcut bindings — user-editable, localStorage-backed.
 *
 * Holds per-action key overrides on top of `SHORTCUT_ACTIONS` defaults.
 * `keyFor`/`setBinding`/`resetAll` drive the footer popover; `resolve` is the
 * reverse lookup the keydown dispatcher (utils/keyboard.ts) uses to turn a
 * pressed key TOKEN into an action id, scoped to the active context.
 *
 * A token is `e.code` with an optional `Ctrl+` prefix (Ctrl OR Meta both
 * normalise to `Ctrl+`), e.g. `KeyA`, `Space`, `ArrowUp`, `Ctrl+KeyS`.
 *
 * Conflicts: 'default' and 'accordion' actions can be live at the same time
 * (the accordion pool extends default), so they share one uniqueness group;
 * 'edit' is its own group. `setBinding` refuses a token already used by
 * another rebindable action in the same group.
 */

import { LS_KEYS } from '../../../lib/utils/constants';
import {
    actionById,
    SHORTCUT_ACTIONS,
    type ShortcutAction,
    type ShortcutContext,
} from './defaults';

const KEY = LS_KEYS.SEG_SHORTCUTS;

function loadOverrides(): Record<string, string> {
    if (typeof localStorage === 'undefined') return {};
    try {
        const raw = localStorage.getItem(KEY);
        if (!raw) return {};
        const parsed = JSON.parse(raw) as Record<string, unknown>;
        const out: Record<string, string> = {};
        for (const [id, tok] of Object.entries(parsed)) {
            // Only keep overrides for actions that still exist and are rebindable.
            const a = actionById(id);
            if (a?.rebindable && typeof tok === 'string' && tok) out[id] = tok;
        }
        return out;
    } catch {
        return {};
    }
}

function persist(o: Record<string, string>): void {
    if (typeof localStorage === 'undefined') return;
    try {
        localStorage.setItem(KEY, JSON.stringify(o));
    } catch {
        /* quota / private-mode — non-fatal */
    }
}

const overrides = $state<Record<string, string>>(loadOverrides());

/** Current key token bound to an action (override if set, else default). */
export function keyFor(id: string): string {
    return overrides[id] ?? actionById(id)?.defaultKey ?? '';
}

/** True when the action's binding has been customised away from its default. */
export function isCustom(id: string): boolean {
    return id in overrides;
}

/** Whether ANY binding has been customised (drives the "Reset" affordance). */
export function hasCustomBindings(): boolean {
    return Object.keys(overrides).length > 0;
}

/** Conflict group: default+accordion share one; edit is separate. */
function groupOf(ctx: ShortcutContext): 'main' | 'edit' {
    return ctx === 'edit' ? 'edit' : 'main';
}

export interface RebindResult {
    ok: boolean;
    conflict?: ShortcutAction;
}

/**
 * Rebind `id` to `token`. Refuses (ok:false + the offending action) when the
 * token already belongs to another rebindable action in the same conflict
 * group. Re-binding to the action's own default clears the override.
 */
export function setBinding(id: string, token: string): RebindResult {
    const action = actionById(id);
    if (!action || !action.rebindable || !token) return { ok: false };

    const group = groupOf(action.context);
    for (const other of SHORTCUT_ACTIONS) {
        if (other.id === id) continue;
        if (groupOf(other.context) !== group) continue;
        if (keyFor(other.id) === token) return { ok: false, conflict: other };
    }

    if (token === action.defaultKey) {
        delete overrides[id];
    } else {
        overrides[id] = token;
    }
    persist(overrides);
    return { ok: true };
}

/** Restore every binding to its default. */
export function resetAll(): void {
    for (const k of Object.keys(overrides)) delete overrides[k];
    persist(overrides);
}

/**
 * Resolve a pressed key token to an action id for the given context.
 *  - 'edit'      → only edit-pool actions.
 *  - 'accordion' → accordion-pool first (overrides), then default-pool.
 *  - 'default'   → default-pool only.
 * Returns null when nothing is bound to the token in scope.
 */
export function resolve(token: string, mode: ShortcutContext): string | null {
    if (!token) return null;
    const pools: ShortcutContext[] =
        mode === 'edit' ? ['edit'] : mode === 'accordion' ? ['accordion', 'default'] : ['default'];
    for (const pool of pools) {
        for (const a of SHORTCUT_ACTIONS) {
            if (a.context !== pool) continue;
            if (keyFor(a.id) === token) return a.id;
        }
    }
    return null;
}

/** Build a key TOKEN from a keyboard event. Returns '' for a bare modifier. */
export function tokenFromEvent(e: KeyboardEvent): string {
    const code = e.code;
    if (!code || /^(Control|Shift|Alt|Meta|OS)(Left|Right)?$/.test(code)) return '';
    const ctrl = e.ctrlKey || e.metaKey ? 'Ctrl+' : '';
    return `${ctrl}${code}`;
}

const _CODE_LABELS: Record<string, string> = {
    Space: 'Space',
    ArrowLeft: '←',
    ArrowRight: '→',
    ArrowUp: '↑',
    ArrowDown: '↓',
    Comma: ',',
    Period: '.',
    Slash: '/',
    Backslash: '\\',
    Minus: '-',
    Equal: '=',
    Semicolon: ';',
    Quote: '’',
    BracketLeft: '[',
    BracketRight: ']',
    Backquote: '`',
    Tab: 'Tab',
    Enter: 'Enter',
    Escape: 'Esc',
    Backspace: '⌫',
    Delete: 'Del',
};

/** Human-readable label for a key token (e.g. `Ctrl+KeyS` → "Ctrl S"). */
export function prettyKey(token: string): string {
    if (!token) return '—';
    const parts = token.split('+');
    const code = parts.pop() as string;
    const mod = parts.includes('Ctrl') ? 'Ctrl ' : '';
    let label = _CODE_LABELS[code];
    if (!label) {
        if (code.startsWith('Key')) label = code.slice(3);
        else if (code.startsWith('Digit')) label = code.slice(5);
        else label = code;
    }
    return `${mod}${label}`;
}
