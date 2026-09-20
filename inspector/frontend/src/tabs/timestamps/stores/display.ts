/**
 * Timestamps tab — display / view-mode state.
 *
 * Owns view mode, granularity, show-letters, and show-phonemes flags.
 * These persist to localStorage via a `<svelte:window>` effect in
 * `TimestampsTab.svelte`.
 */

import { derived, writable } from 'svelte/store';

import { DEFAULT_SDK_RIWAYAH } from '../../../lib/riwayat';
import type { TsConfigResponse } from '../../../lib/types/generated/schemas';

/** "analysis" = mega-blocks; "animation" = reveal-mode per-word fade-in. */
export const TS_VIEW_MODES = { ANALYSIS: 'analysis', ANIMATION: 'animation' } as const;
export type TsViewMode = typeof TS_VIEW_MODES[keyof typeof TS_VIEW_MODES];
export const TS_VIEW_MODE_DEFAULT: TsViewMode = TS_VIEW_MODES.ANALYSIS;

/** In animation mode, per-word or per-character highlighting. */
export const TS_GRANULARITIES = { WORDS: 'words', CHARACTERS: 'characters' } as const;
export type TsGranularity = typeof TS_GRANULARITIES[keyof typeof TS_GRANULARITIES];
export const TS_GRANULARITY_DEFAULT: TsGranularity = TS_GRANULARITIES.WORDS;

/** Current view mode. */
export const viewMode = writable<TsViewMode>(TS_VIEW_MODE_DEFAULT);

/** Animation-mode granularity. */
export const granularity = writable<TsGranularity>(TS_GRANULARITY_DEFAULT);

/** Analysis mode: toggle letter row visibility. */
export const showLetters = writable<boolean>(true);

/** Analysis mode: toggle phoneme row + cross-word bridge visibility. */
export const showPhonemes = writable<boolean>(true);

/** Analysis-mode highlight style. false = discrete fill (a cell crisply lights
 *  and fades), true = continuous karaoke wipe (the fill tracks the voice across
 *  each cell). Self-persisted here (the boolean toggles above are persisted from
 *  `TimestampsTab`; this one is loaded/saved inline to stay self-contained). */
const LS_HIGHLIGHT_WIPE = 'ts:highlightWipe';
function _wipeInitial(): boolean {
    try {
        return localStorage.getItem(LS_HIGHLIGHT_WIPE) === 'true';
    } catch {
        return false;
    }
}
export const highlightWipe = writable<boolean>(_wipeInitial());
highlightWipe.subscribe((v) => {
    try {
        localStorage.setItem(LS_HIGHLIGHT_WIPE, String(v));
    } catch {
        /* private mode / storage disabled — in-memory only */
    }
});

/** Timestamps /api/ts/config — load once, drive CSS variables. null = not loaded yet. */
export const tsConfig = writable<TsConfigResponse | null>(null);

// ---------------------------------------------------------------------------
// Word-profile mode
// ---------------------------------------------------------------------------

/**
 * True while the loaded chapter shard carries word timings only.
 *
 * Set from the SHARD's own `_meta.profile`, never from the delivery's riwayah:
 * the shard is what does or does not contain letter, phoneme and cell geometry,
 * and a re-timed delivery could in principle carry either. Gating on the
 * riwayah would blank a perfectly good native shard.
 *
 * A word-profile shard is produced by aligning against Hafs as a proxy and
 * projecting the result, so it has words and pause boundaries and nothing
 * below that: no letters, no phonemes, no per-cell tajweed tags, no sound
 * timings to interpolate a karaoke wipe from.
 */
export const wordProfile = writable<boolean>(false);

/**
 * SDK riwayah slug of the loaded chapter shard — the edition whose script,
 * font and verse-marker glyph the tab renders. Hafs until a shard says
 * otherwise, which is what every pre-multi-riwayah shard means.
 */
export const deliveryRiwayah = writable<string>(DEFAULT_SDK_RIWAYAH);

/**
 * EFFECTIVE display flags. The raw writables above stay the user's persisted
 * preference — a reader who likes letters on keeps that setting when they
 * navigate back to a Hafs reciter — while these are what the view renders.
 *
 * Every consumer that draws or measures should read these; only the footer
 * toggles (which show pressed state and write the preference) read the raw
 * stores.
 */
export const lettersVisible = derived(
    [showLetters, wordProfile],
    ([$showLetters, $wordProfile]) => $showLetters && !$wordProfile,
);

export const phonemesVisible = derived(
    [showPhonemes, wordProfile],
    ([$showPhonemes, $wordProfile]) => $showPhonemes && !$wordProfile,
);

/** The wipe interpolates within a cell from sound timings a word shard lacks. */
export const wipeActive = derived(
    [highlightWipe, wordProfile],
    ([$highlightWipe, $wordProfile]) => $highlightWipe && !$wordProfile,
);

/** Animation granularity, locked to whole words when there are no characters. */
export const effectiveGranularity = derived(
    [granularity, wordProfile],
    ([$granularity, $wordProfile]): TsGranularity =>
        $wordProfile ? TS_GRANULARITIES.WORDS : $granularity,
);

// ---------------------------------------------------------------------------
// Cross-component hover (blocks panel ↔ waveform)
// ---------------------------------------------------------------------------

/** The element currently hovered in TimedAnalysisRow (Analysis view). The waveform
 *  subscribes to paint a matching-color band at the [startSec, endSec] range. */
export interface TsHoveredElement {
    /** A word shard only ever publishes `'word'` — it has nothing finer. */
    kind: 'word' | 'letter' | 'phoneme';
    startSec: number;
    endSec: number;
}

/** null when nothing is hovered. */
export const tsHoveredElement = writable<TsHoveredElement | null>(null);

/** Slice-relative seconds when the pointer is on the waveform. null when off.
 *  Published by TimestampsWaveform; consumed by TimedAnalysisRow to drive entity
 *  highlights while audio is paused (so hover-scrubbing previews the position). */
export const tsWaveformHoverTime = writable<number | null>(null);
