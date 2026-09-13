/**
 * Shared constants: localStorage keys and placeholder strings.
 */

export const LS_KEYS = {
    ACTIVE_TAB:      'insp_active_tab',
    TS_RECITER:      'insp_ts_reciter',
    TS_SPEED:        'insp_ts_speed',
    TS_VIEW_MODE:    'insp_ts_view_mode',
    TS_SHOW_LETTERS: 'insp_ts_show_letters',
    TS_SHOW_PHONEMES:'insp_ts_show_phonemes',
    TS_GRANULARITY:  'insp_ts_granularity',
    TS_SHOW_TRANSLATIONS: 'insp_ts_show_translations',
    TS_TRANSLATION_LANG:  'insp_ts_translation_lang',
    TS_TAJWEED:           'insp_ts_tajweed',
    SEG_RECITER:     'insp_seg_reciter',
    SEG_SPEED:       'insp_seg_speed',
    SEG_AUTOPLAY:    'insp_seg_autoplay',
    SEG_AUTOSCROLL:  'insp_seg_autoscroll',
    SEG_END_CHIME:   'insp_seg_end_chime',
    SEG_AUTOSAVE:    'insp_seg_autosave',
    SEG_SHORTCUTS:   'insp_seg_shortcuts',
    SEG_VAL_SORTS:   'insp_seg_val_sorts',
    SEG_VAL_BOUNDARY: 'insp_seg_val_boundary',
    DASH_RECITER:    'insp_dash_reciter',
    DISMISSED_ANNOUNCEMENTS: 'insp_dismissed_announcements',
    SEEN_ANNOUNCEMENTS:      'insp_seen_announcements',
    ANON_FLAG_TOKEN: 'insp_anon_flag_token',
    // NOTE: the literal 'insp_theme' is ALSO duplicated in index.html's inline
    // flash-free init script (which can't import this module) — keep in sync.
    THEME:           'insp_theme',
    // Owned by Paraglide's localStorage strategy (`localStorageKey` in
    // vite.config.ts) — keep in sync. Present = explicit locale override; absent
    // = auto-detect from the browser (the `preferredLanguage` strategy).
    LOCALE:          'insp_locale',
} as const;

export const PLACEHOLDER_SELECT = '-- select --';
export const PLACEHOLDER_DASH   = '--';
export const PLACEHOLDER_RECITER = '-- Select reciter --';

/** Segments-tab auto-scroll animation modes. Not a user preference — the
 *  active mode is served by /api/seg/config (SEG_SCROLL_ANIM_MODE in
 *  inspector/config.py) and consumed via segConfig.scrollAnimMode. These
 *  constants define the valid value set shared by server and client.
 *   - NONE   — instant jump, no animation.
 *   - SMOOTH — native CSS smooth scroll on every target change.
 *   - HYBRID — smooth when the target is more than one viewport away,
 *              instant otherwise (default).
 */
export const SCROLL_ANIM_MODES = {
    NONE:   'none',
    SMOOTH: 'smooth',
    HYBRID: 'hybrid',
} as const;
export type ScrollAnimMode = typeof SCROLL_ANIM_MODES[keyof typeof SCROLL_ANIM_MODES];
export const SCROLL_ANIM_DEFAULT: ScrollAnimMode = SCROLL_ANIM_MODES.HYBRID;

export const TAB_NAMES = {
    DASHBOARD: 'dashboard',
    TIMESTAMPS: 'timestamps',
    SEGMENTS: 'segments',
} as const;
export type TabName = typeof TAB_NAMES[keyof typeof TAB_NAMES];

// Playhead color used in the edit-mode preview overlay (trim / split / play-range)
export const PREVIEW_PLAYHEAD_COLOR = '#f72585';

// Waveform canvas colors shared across all waveform drawing modules
export const WAVEFORM_BG_COLOR     = '#0f0f23';
export const WAVEFORM_FILL_COLOR   = 'rgba(67, 97, 238, 0.3)';
export const WAVEFORM_STROKE_COLOR = '#4361ee';
export const WAVEFORM_DIM_OVERLAY_COLOR = 'rgba(0, 0, 0, 0.45)';

/** Letter-level highlight color used by the Timestamps display + waveform. */
export const LETTER_HIGHLIGHT_COLOR = '#2ec4b6';

// Waveform per-segment normalization: segments below this peak amplitude are
// treated as silent — the display scale is not amplified to avoid noise blowup.
export const WAVEFORM_SILENCE_THRESHOLD = 0.05;
