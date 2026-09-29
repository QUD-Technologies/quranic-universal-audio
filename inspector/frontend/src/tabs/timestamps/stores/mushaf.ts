/**
 * Timestamps tab — Mushaf view state.
 *
 * `mushafMode` swaps the tab's waveform + analysis row (and the shared
 * NowReciting bar) for a two-page Madani mushaf. Only Hafs has page layouts,
 * so the view is available only while the loaded shard is Hafs (`mushafAvailable`).
 *
 * Display prefs are deliberately few: the print year, whether other verses
 * stay visible, and whether not-yet-recited words are shown. Together the two
 * visibility prefs give a memorisation mode (current verse only + hide
 * upcoming = only the words already recited).
 *
 * Everything persists to localStorage (per-viewer convenience).
 */
import { derived, writable, type Writable } from 'svelte/store';

import { isHafs } from '../../../lib/riwayat';
import { playerContext } from '../../../lib/stores/player-context';

export type MushafYear = '1405' | '1421' | '1441';
export const MUSHAF_YEARS: readonly MushafYear[] = ['1421', '1405', '1441'];
export const DEFAULT_MUSHAF_YEAR: MushafYear = '1421';

/** Which verses stay on the page: the whole spread, or only the one being recited. */
export type MushafScope = 'spread' | 'verse';

const LS_MODE = 'ts_mushaf_mode';
const LS_YEAR = 'ts_mushaf_year';
const LS_SCOPE = 'ts_mushaf_scope';
const LS_UPCOMING = 'ts_mushaf_show_upcoming';

function read(key: string): string | null {
    try {
        return localStorage.getItem(key);
    } catch {
        return null;
    }
}

function persisted<T>(key: string, parse: (raw: string | null) => T, serialize: (v: T) => string): Writable<T> {
    const store = writable<T>(parse(read(key)));
    store.subscribe((v) => {
        try {
            localStorage.setItem(key, serialize(v));
        } catch {
            /* storage unavailable — the pref just doesn't survive a reload */
        }
    });
    return store;
}

export const mushafMode = persisted(LS_MODE, (r) => r === 'true', String);

export const mushafYear = persisted<MushafYear>(
    LS_YEAR,
    (r) => (MUSHAF_YEARS.includes(r as MushafYear) ? (r as MushafYear) : DEFAULT_MUSHAF_YEAR),
    (v) => v,
);

export const mushafScope = persisted<MushafScope>(
    LS_SCOPE,
    (r) => (r === 'verse' ? 'verse' : 'spread'),
    (v) => v,
);

export const mushafShowUpcoming = persisted(LS_UPCOMING, (r) => r !== 'false', String);

/** The playing delivery has a page layout (Hafs only). Read off the catalog
 *  delivery, which is known at once — the shard's edition lands later and
 *  defaults to Hafs meanwhile, which would flash the mushaf for other editions. */
export const mushafAvailable = derived(playerContext, ($p) => isHafs($p.delivery?.riwayah));

/** The mushaf is what the tab is showing right now. */
export const mushafActive = derived(
    [mushafMode, mushafAvailable],
    ([$mode, $available]) => $mode && $available,
);
