/**
 * Lets a tab hide the shared NowReciting bar (teleprompter + filmstrip) while
 * it shows its own recitation surface — the Timestamps Mushaf view. The bar's
 * data keeps loading underneath (the footer's verse seek reads it); only its
 * markup and reserved height go away.
 */
import { writable } from 'svelte/store';

export const nowRecitingSuppressed = writable<boolean>(false);
