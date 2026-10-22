/**
 * Legacy-facing Svelte store mirror of the ambient Paraglide locale.
 *
 * Legacy Svelte-4 files (e.g. App.svelte, SignInModal.svelte) cannot use runes,
 * so they re-render off store subscriptions. Referencing `$localeStore` in a
 * `$:` reactive block re-runs the `m.*()` reads when the locale switches —
 * mirroring the `$activeTabStore` idiom already used in App.svelte.
 *
 * The runes mirror lives in `./locale.svelte.ts`. Both are driven by
 * `switchLocale()` below so runes and legacy files stay in lockstep; the rune
 * cell is updated lazily via a registered setter to avoid importing a
 * `.svelte.ts` module from this plain `.ts` file.
 */
import { writable } from 'svelte/store';

import { getLocale, setLocale } from '$lib/paraglide/runtime';

export type Locale = ReturnType<typeof getLocale>;

export const localeStore = writable<Locale>(getLocale());

// RTL locales flip `<html dir="rtl">`; chrome mirrors via logical CSS while
// time-axis surfaces (waveforms, filmstrip, scrubber, editor rows, history
// diffs) stay pinned `dir="ltr"` islands. Keep in lockstep with locale.svelte.ts.
const RTL: ReadonlySet<string> = new Set<string>(['ar', 'ckb']);

function applyDocument(loc: Locale): void {
    const el = document.documentElement;
    el.lang = loc;
    el.dir = RTL.has(loc) ? 'rtl' : 'ltr';
}

// The runes cell setter, registered by locale.svelte.ts at import time so a
// single switch updates both reactive mirrors without a .ts→.svelte.ts import.
let runeSetter: ((loc: Locale) => void) | null = null;

/** Registered once by the runes module so `switchLocale` can trip both mirrors. */
export function registerRuneSetter(fn: (loc: Locale) => void): void {
    runeSetter = fn;
}

/**
 * Reactivity bridge for legacy Svelte-4 `$:` blocks. Reading `$localeStore`
 * makes a reactive statement depend on the locale; passing it through `_dep`
 * (instead of the `($localeStore, m())` comma-operator trick, which `tsc`
 * rejects as an unused expression) keeps that dependency while returning the
 * message string. Usage: `$: label = tr($localeStore, m.some_key());`.
 */
export function tr<T>(_dep: unknown, value: T): T {
    return value;
}

/**
 * Switch locale in-place (no reload). The single entry point for every locale
 * switcher — updates Paraglide's ambient locale, the legacy store, the runes
 * cell, and <html dir/lang>.
 */
export function switchLocale(next: Locale): void {
    setLocale(next, { reload: false });
    localeStore.set(next);
    runeSetter?.(next);
    applyDocument(next);
}
