/**
 * Cross-tab deep-link channel.
 *
 * `pendingTsNavigation` is set by callers outside the Timestamps tab (e.g. the
 * Bookmarks sidebar, or a flag notification in the rail) to request that the
 * Timestamps tab load a specific verse and autoplay. TimestampsTab consumes and
 * clears it. When `slug` is set the tab loads that exact reciter (flag
 * deep-link); otherwise it picks the first TS-capable reciter that has the
 * verse (bookmark deep-link).
 */

import { writable } from 'svelte/store';

export interface PendingTsNavigation {
    surah: number;
    ayah: number;
    autoplay: boolean;
    /** Target a specific reciter (flag-notification redirect). */
    slug?: string;
    /** Chapter-audio ms inside the verse: lands on the rendition playing there
     *  when the verse is recited more than once. */
    timeMs?: number;
}

export const pendingTsNavigation = writable<PendingTsNavigation | null>(null);
