/**
 * Reactive locale state for the SPA (Svelte 5 runes).
 *
 * Paraglide message functions read the *ambient* locale set by `setLocale()`
 * from `$lib/paraglide/runtime`. This module mirrors that ambient locale into a
 * `$state` cell so runes components re-render on switch: reading `i18n.locale`
 * inside a template / `$derived` registers a dependency, and the `_locale`
 * write on switch trips it.
 *
 * MODE mirrors the theme store (`stores/theme.svelte.ts`): 'auto' (the default —
 * resolve from the browser's `navigator.languages`) or an explicit locale
 * override. The override marker IS Paraglide's own `insp_locale` localStorage
 * key: present = pinned to that locale, absent = auto. So `setMode('auto')`
 * detects the browser language, applies it, then clears the key so the next load
 * re-detects — exactly how theme's 'system' mode clears `insp_theme`.
 *
 * Switching is funnelled through `switchLocale()` in `./locale-store.ts`, the
 * single entry point that updates Paraglide's ambient locale, the legacy store
 * mirror, this runes cell, and <html dir/lang> — keeping runes and legacy files
 * in lockstep.
 */
import { baseLocale, getLocale, locales } from '$lib/paraglide/runtime';

import { LS_KEYS } from '../utils/constants';

import { localizeDigits } from './format';
import { registerRuneSetter, switchLocale } from './locale-store';

export type Locale = (typeof locales)[number];
/** The user's choice: an explicit locale, or 'auto' (follow the browser). */
export type LocaleMode = 'auto' | Locale;

function isLocale(v: string | null | undefined): v is Locale {
    return v != null && (locales as readonly string[]).includes(v);
}

/** The browser's preferred UI language, mapped to a supported locale by primary
 *  subtag (e.g. 'ar-EG' → 'ar'); `baseLocale` when none match. Mirrors what
 *  Paraglide's `preferredLanguage` strategy resolves on a fresh load. */
function detectLocale(): Locale {
    if (typeof navigator !== 'undefined') {
        const prefs = navigator.languages ?? [navigator.language];
        for (const tag of prefs) {
            const base = tag?.toLowerCase().split('-')[0];
            if (isLocale(base)) return base;
        }
    }
    return baseLocale;
}

/** 'auto' unless the user pinned a locale (Paraglide's `insp_locale` key). */
function readInitialMode(): LocaleMode {
    if (typeof localStorage !== 'undefined') {
        try {
            const v = localStorage.getItem(LS_KEYS.LOCALE);
            if (isLocale(v)) return v;
        } catch {
            /* storage blocked — fall through to auto */
        }
    }
    return 'auto';
}

// Seed from Paraglide's own resolution (localStorage → preferredLanguage → baseLocale).
let _locale = $state<Locale>(getLocale());
let _mode = $state<LocaleMode>(readInitialMode());

// Let the shared switcher trip this runes cell without a .ts→.svelte.ts import.
registerRuneSetter((loc) => {
    _locale = loc as Locale;
});

// RTL locales flip `<html dir="rtl">`; chrome mirrors via logical CSS while
// time-axis surfaces (waveforms, filmstrip, scrubber, editor rows, history
// diffs) stay pinned `dir="ltr"` islands. Keep in lockstep with locale-store.ts.
const RTL: ReadonlySet<string> = new Set<string>(['ar', 'ckb']);

function applyDocument(loc: Locale): void {
    const el = document.documentElement;
    el.lang = loc;
    el.dir = RTL.has(loc) ? 'rtl' : 'ltr';
}

export const i18n = {
    /** Reactive current (resolved) locale — read in components / `$derived`. */
    get locale(): Locale {
        return _locale;
    },
    /** Reactive choice: 'auto' (follow the browser) or a pinned locale. */
    get mode(): LocaleMode {
        return _mode;
    },
    get locales(): readonly Locale[] {
        return locales;
    },
    get baseLocale(): Locale {
        return baseLocale;
    },
    /**
     * Choose a mode: an explicit locale, or 'auto' to follow the browser. Auto
     * detects the browser language, applies it, then clears the `insp_locale`
     * key so the next load re-detects (mirrors theme's 'system').
     */
    setMode(mode: LocaleMode): void {
        _mode = mode;
        const resolved = mode === 'auto' ? detectLocale() : mode;
        switchLocale(resolved);
        try {
            // Own the override marker explicitly: 'auto' clears it, an explicit
            // choice sets it — even when `switchLocale` no-ops because the
            // resolved locale already matches the ambient one.
            if (mode === 'auto') localStorage.removeItem(LS_KEYS.LOCALE);
            else localStorage.setItem(LS_KEYS.LOCALE, mode);
        } catch {
            /* storage blocked — the choice still applies for this session */
        }
    },
    /** Cycle Auto → each locale → Auto (the header toggle). */
    cycle(): void {
        const order: LocaleMode[] = ['auto', ...locales];
        const next = order[(order.indexOf(_mode) + 1) % order.length];
        this.setMode(next ?? 'auto');
    },
    /** Pin an explicit locale in-place (no reload). */
    set(loc: Locale): void {
        this.setMode(loc);
    },
};

/**
 * Reactive digit localization for runes scopes — reads the reactive `_locale`
 * cell so a `{fmtNum(n)}` in a template / `$derived` re-renders on locale
 * switch, with no per-call-site `i18n.locale === 'ar' ? …` conditional. The
 * ambient-locale core is `./format.ts::localizeDigits` (for pure `.ts` and
 * legacy files).
 */
export function fmtNum(value: string | number): string {
    return localizeDigits(value, _locale);
}

/** Call once at app boot (main.ts) to sync <html dir/lang> with the resolved locale. */
export function initLocale(): void {
    applyDocument(_locale);
}
