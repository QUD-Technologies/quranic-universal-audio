/**
 * Theme store — the single owner of the theme choice.
 *
 * Three MODES: 'system' (default — follow the device's `prefers-color-scheme`
 * live), or an explicit 'light' / 'dark' override. The resolved theme (always
 * 'light' or 'dark') is what everything else reads (`current` / `isLight` /
 * `theme$`).
 *
 * The `data-theme` attribute is first set on <html> by the inline script in
 * index.html (flash-free, before first paint). This store reads that as its
 * initial truth, then owns every change: it updates the `$state`, persists the
 * mode to localStorage, re-sets the attribute (re-skinning all token CSS), and
 * fires a window `themechange` event so the imperative 2D-canvas surfaces
 * (waveforms, charts) — which a CSS attribute flip can't reach — repaint. See
 * lib/utils/canvas-theme.ts for the resolver those canvases read.
 *
 * In 'system' mode a `matchMedia` change listener re-resolves live, so flipping
 * the OS between light/dark updates the app with no reload — this is also the
 * robust default inside the HF Space iframe, where `prefers-color-scheme` always
 * works but localStorage may be partitioned/blocked for a third-party frame.
 */

import { readable } from 'svelte/store';

import { LS_KEYS } from '../utils/constants';

/** The resolved theme actually applied to the DOM. */
export type Theme = 'light' | 'dark';
/** The user's choice: an explicit theme, or 'system' (follow the device). */
export type ThemeMode = 'system' | 'light' | 'dark';

/** The custom event canvas components listen for to repaint on a theme flip. */
export const THEME_CHANGE_EVENT = 'themechange';

const DARK_QUERY = '(prefers-color-scheme: dark)';

/** The device's current preference. Defaults to dark when unknowable (SSR / no
 *  matchMedia). Kept in lockstep with the index.html inline script's query. */
function systemTheme(): Theme {
    if (typeof window === 'undefined' || !window.matchMedia) return 'dark';
    return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light';
}

/** The persisted mode: an explicit 'light'/'dark', else 'system' (the default —
 *  also the fallback when storage is blocked, e.g. a third-party iframe). */
function readInitialMode(): ThemeMode {
    if (typeof localStorage !== 'undefined') {
        try {
            const v = localStorage.getItem(LS_KEYS.THEME);
            if (v === 'light' || v === 'dark') return v;
        } catch {
            /* storage blocked — fall through to system */
        }
    }
    return 'system';
}

function resolve(mode: ThemeMode): Theme {
    return mode === 'system' ? systemTheme() : mode;
}

class ThemeStore {
    /** The user's choice. 'system' follows the device's prefers-color-scheme live. */
    mode = $state<ThemeMode>(readInitialMode());
    /** The resolved theme applied to the DOM (what visual consumers read). */
    current = $state<Theme>(resolve(readInitialMode()));

    constructor() {
        if (typeof window !== 'undefined' && window.matchMedia) {
            const mq = window.matchMedia(DARK_QUERY);
            const onSystem = (): void => {
                if (this.mode === 'system') this.applyResolved(systemTheme());
            };
            // addEventListener is the modern API; pre-14 Safari only has addListener.
            if (mq.addEventListener) mq.addEventListener('change', onSystem);
            else if (mq.addListener) mq.addListener(onSystem);
        }
    }

    get isLight(): boolean {
        return this.current === 'light';
    }

    /** Apply a resolved theme to the DOM + notify listeners, if it changed. */
    private applyResolved(theme: Theme): void {
        if (theme === this.current) return;
        this.current = theme;
        if (typeof document !== 'undefined') {
            document.documentElement.setAttribute('data-theme', theme);
        }
        if (typeof window !== 'undefined') {
            // Let the canvas-theme resolver drop its cache before listeners redraw.
            window.dispatchEvent(new CustomEvent(THEME_CHANGE_EVENT, { detail: theme }));
        }
    }

    /** Choose a mode: an explicit theme, or 'system' to follow the device. */
    setMode(mode: ThemeMode): void {
        this.mode = mode;
        try {
            // Persist explicit choices; 'system' clears the key so it stays the
            // default (and degrades cleanly where storage is blocked/partitioned).
            if (mode === 'system') localStorage.removeItem(LS_KEYS.THEME);
            else localStorage.setItem(LS_KEYS.THEME, mode);
        } catch {
            /* storage blocked — the choice still applies for this session */
        }
        this.applyResolved(resolve(mode));
    }

    /** Cycle System → Light → Dark → System (the header toggle). */
    cycle(): void {
        this.setMode(this.mode === 'system' ? 'light' : this.mode === 'light' ? 'dark' : 'system');
    }

    /** Explicit light/dark flip (escapes system). Kept for programmatic callers. */
    toggle(): void {
        this.setMode(this.current === 'light' ? 'dark' : 'light');
    }
}

export const themeStore = new ThemeStore();

/**
 * A `svelte/store` readable mirror of the resolved theme, for legacy
 * ($:/auto-subscribe) components that can't reactively read the runes
 * `themeStore.current`. Updates on the same `themechange` event (fired for both
 * explicit changes and live system flips). Runes components should read
 * `themeStore` directly; this exists only for the legacy bridge (`$theme$`).
 */
export const theme$ = readable<Theme>(themeStore.current, (set) => {
    if (typeof window === 'undefined') return;
    const handler = (e: Event): void => {
        const detail = (e as CustomEvent<Theme>).detail;
        set(detail === 'light' || detail === 'dark' ? detail : themeStore.current);
    };
    window.addEventListener(THEME_CHANGE_EVENT, handler);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, handler);
});
