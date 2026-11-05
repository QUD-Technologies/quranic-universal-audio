/**
 * Recitation-animation config.
 *
 * Tunable surface (timing, easing, effects, typography, behaviour, markers).
 * Components read the locked `DEFAULT_RECITATION_CONFIG` statically; the
 * five user-tunable values flow through `recitationConfigStore`. Values
 * project to CSS custom properties via `cssVars()` so the hot path is pure
 * CSS transitions (no per-frame JS style writes beyond opacity).
 *
 * Defaults are grounded in `styles/tokens.css` (`--accent`, `--ease-out-*`,
 * `--t-*`) so the section blends with the live dark theme.
 */

import { inkFor, legibleAccent, legibleAccentCfg } from '../utils/color-derive';
import type { Theme } from '../utils/highlight-model';

// Light-theme legible band for the line-animation accent — darker than the
// dark-on-page band so the active word reads as ink on near-white paper.
const LIGHT_BAND_MIN_L = 0.46;
const LIGHT_BAND_MAX_L = 0.70;
// The non-active (reached/unreached) line text on light paper — a calm cool
// dark grey, dimmed further by reached/unreached opacity. On dark it stays the
// configured light grey (cfg.baseColor).
const LIGHT_BASE_COLOR = '#3e4350';

export type Granularity = 'word' | 'char';
export type FilmstripMotion = 'hybrid' | 'tuner' | 'snap';

export interface RecitationAnimConfig {
    // ---- timing (ms) ----
    /** Opacity reveal duration for a word in word granularity. */
    wordRevealMs: number;
    /** Opacity reveal duration for a character in char granularity. */
    charRevealMs: number;
    /** Fade-out duration when a page clears (overflow or ayah end). */
    clearFadeMs: number;
    /** Light a unit this many ms before its true start (negative = lag). */
    leadMs: number;

    // ---- easing ----
    /** CSS easing function applied to reveal + emphasis transitions. */
    easing: string;

    // ---- effects ----
    /** Color of the currently-active word/char. */
    highlightColor: string;
    /** Color of non-active text (reached + unreached words, the ۝ marker). */
    baseColor: string;
    /** Opacity of already-recited units (0..1). */
    reachedOpacity: number;
    /** Opacity of not-yet-reached units (0..1). */
    unreachedOpacity: number;
    /** Active-highlight transition duration (color/scale/glow/stroke), split by
     *  granularity — chars are smaller + stream faster than words, so they
     *  usually want a quicker emphasis. The active granularity's value drives
     *  `--ra-active-emphasis`. */
    wordActiveEmphasisMs: number;
    charActiveEmphasisMs: number;
    /** Scale transform on the active unit (1 = none). Word granularity only —
     *  inline Arabic chars can't transform without breaking cursive joining. */
    activeScale: number;
    /** Glow (text-shadow blur px) on the active unit (0 = none). */
    activeGlowPx: number;
    /** Outline (text-stroke) on the ACTIVE unit — makes the current word/char
     *  pop. Width px + color; 0 = off. Painted behind the fill (paint-order). */
    activeStrokePx: number;
    activeStrokeColor: string;
    /** Outline (text-stroke) on ALL line text — aids legibility + separation of
     *  crowded short ayahs against the background. Width px + color; 0 = off. */
    baseStrokePx: number;
    baseStrokeColor: string;

    // ---- typography ----
    fontFamily: string;
    fontSizePx: number;
    lineHeight: number;
    wordSpacingPx: number;
    letterSpacingPx: number;

    // ---- behaviour ----
    granularity: Granularity;
    /** Clear + re-page when the active word would overflow the line. */
    clearOnOverflow: boolean;
    /** Clear + restart from word 1 when the ayah changes. */
    clearOnAyahEnd: boolean;
    /** Center the fitted line (vs right-aligned RTL fill). */
    centerLine: boolean;
    /** Append the ۝ end-of-ayah marker (Arabic-Indic numeral) after each ayah. */
    showAyahMarker: boolean;

    // ---- section chrome ----
    autoExpandOnPlay: boolean;
    collapsedByDefault: boolean;

    // ---- ayah filmstrip (center-anchored nav strip above the bar) ----
    filmstripShow: boolean;
    /** Motion model: `hybrid` (continuous tuner-center, drag snaps to ayah),
     *  `tuner` (continuous, drag scrubs exact time), `snap` (center on ayah
     *  change only, drag = carousel snap). */
    filmstripMotion: FilmstripMotion;
    /** Global cursor velocity — px per second of audio. The strip is a true
     *  fixed-scale time-ruler: cell width = `canonDurSec × pxPerSec` and
     *  between-verse gap = `nextGapSec × pxPerSec`, so the cursor travels at this
     *  one velocity everywhere (within cells and across silences) and a given
     *  silence renders to the same px in every surah and for every reciter.
     *  Higher = more zoomed-in (wider cells, more prominent silences). */
    filmstripPxPerSec: number;
    /** Minimum recited-cell width (px) so a 2-digit ayah number stays legible
     *  when a verse is too short to earn it at `filmstripPxPerSec`. A floored cell
     *  keeps its time-true span (`aw`) centered and the cursor crosses only that
     *  span at the ruler velocity — the surplus margin is absorbed into the
     *  adjacent gap, so in-verse scrolling never speeds up. */
    filmstripMinCellPx: number;
    /** Minimum gap between cells (px) — the floor for a near-zero inter-verse
     *  silence so cells stay distinct. A real between-verse silence widens the gap
     *  at `filmstripPxPerSec`, crossed at the same velocity the cursor travels
     *  within a verse. */
    filmstripGapPx: number;
    /** Strip height (px). */
    filmstripHeightPx: number;
}

// Locked baseline tuned in the prototype. The dashboard surface reads this as
// its starting point. Five values stay user-tunable on the dashboard via
// CustomizePanel (motion, granularity, highlightColor, unreachedOpacity,
// fontSizePx); the rest are fixed.
export const DEFAULT_RECITATION_CONFIG: RecitationAnimConfig = {
    wordRevealMs: 600,
    charRevealMs: 80,
    clearFadeMs: 600,
    leadMs: 0,

    easing: 'cubic-bezier(0.25, 1, 0.5, 1)', // ease-out quart (never linear)

    highlightColor: '#4abad9',
    baseColor: '#e1e4e5',
    reachedOpacity: 0.8,
    unreachedOpacity: 0.2,
    wordActiveEmphasisMs: 210,
    charActiveEmphasisMs: 0,
    activeScale: 1.05,
    activeGlowPx: 5,
    activeStrokePx: 0,
    activeStrokeColor: '#000000',
    baseStrokePx: 1.5,
    baseStrokeColor: '#000000',

    // Matches the timestamps-tab animation: the dataset's display text is
    // DigitalKhatt-encoded, so it must render in the DigitalKhatt webfont
    // (@font-face in styles/base.css → /api/static/quran-data/DigitalKhattV2.otf). Fallbacks
    // are other naskh faces for when the font hasn't loaded.
    fontFamily: "'DigitalKhatt', 'Traditional Arabic', 'Scheherazade New', 'Amiri', serif",
    fontSizePx: 26,
    lineHeight: 2,
    wordSpacingPx: 8,
    letterSpacingPx: 0,

    granularity: 'word',
    clearOnOverflow: true,
    clearOnAyahEnd: true,
    centerLine: true,
    showAyahMarker: true,

    autoExpandOnPlay: true,
    collapsedByDefault: false,

    filmstripShow: true,
    filmstripMotion: 'hybrid',
    filmstripPxPerSec: 12,
    filmstripMinCellPx: 18,
    filmstripGapPx: 2,
    filmstripHeightPx: 36,
};

/** 8-direction text-shadow that outlines the rendered text *silhouette*. Unlike
 *  per-glyph `-webkit-text-stroke` (which traces each letter and shows seams
 *  where Arabic letters join), this outlines the word's outer border. '' when
 *  width ≤ 0. */
function outlineShadow(px: number, color: string): string {
    if (!px || px <= 0) return '';
    const o = `${px}px`;
    const n = `-${px}px`;
    return [
        `${o} 0 0 ${color}`, `${n} 0 0 ${color}`,
        `0 ${o} 0 ${color}`, `0 ${n} 0 ${color}`,
        `${o} ${o} 0 ${color}`, `${n} ${o} 0 ${color}`,
        `${o} ${n} 0 ${color}`, `${n} ${n} 0 ${color}`,
    ].join(', ');
}

/** SVG equivalent of the native word silhouette. Applied once to the complete
 * shaped word, never per token, so joined Arabic letters do not acquire seams. */
function outlineDropShadow(px: number, color: string): string {
    if (!px || px <= 0) return '';
    // One filter sees the composited word silhouette. Chaining directional
    // drop-shadows would feed each shadow into the next and balloon the outline.
    return `drop-shadow(0 0 ${px}px ${color})`;
}

/** Project the line-animation slice of config to CSS custom properties.
 *  The active-unit effects (emphasis/scale/glow) resolve to the *current*
 *  granularity's value, so the same CSS rules render word- or char-tuned. */
export function cssVars(cfg: RecitationAnimConfig, theme: Theme = 'dark'): Record<string, string> {
    const isChar = cfg.granularity === 'char';
    const isLight = theme === 'light';
    // On light paper the dark-on-light text needs no silhouette halo (it already
    // separates); the black halo only earns its place lifting light text off the
    // dark page.
    const baseColor = isLight ? LIGHT_BASE_COLOR : cfg.baseColor;
    const baseOutline = isLight ? '' : outlineShadow(cfg.baseStrokePx, cfg.baseStrokeColor);
    const svgBaseOutline = isLight ? '' : outlineDropShadow(cfg.baseStrokePx, cfg.baseStrokeColor);
    // The active unit is coloured with the SAME legible highlight the analysis
    // triad uses (`legibleAccent` == the analysis `triad.word`), so the line
    // animation and the analysis cells always read as one colour — no dark↔light
    // split between them. On light the accent is clamped into the darker band so
    // it reads as ink. Its outline auto-contrasts; an explicit activeStroke
    // (px > 0) is honoured verbatim, otherwise the base-width silhouette is
    // recoloured to the contrast halo (skipped on light — a dark word on paper
    // pops without one).
    const hl = isLight
        ? legibleAccentCfg(cfg.highlightColor, LIGHT_BAND_MIN_L, LIGHT_BAND_MAX_L)
        : legibleAccent(cfg.highlightColor);
    const activeOutline = cfg.activeStrokePx > 0
        ? outlineShadow(cfg.activeStrokePx, cfg.activeStrokeColor)
        : (isLight ? '' : outlineShadow(cfg.baseStrokePx, inkFor(hl)));
    const glow = cfg.activeGlowPx > 0 ? `0 0 ${cfg.activeGlowPx}px ${hl}` : '';
    // Vertical headroom so an active word scaled by `activeScale` (>1) isn't
    // clipped by the fixed-height, overflow-hidden line box. Round up to a
    // whole px; 0 when no scale.
    const scalePad = Math.ceil(Math.max(0, cfg.activeScale - 1) * cfg.fontSizePx);
    return {
        '--ra-word-reveal': `${cfg.wordRevealMs}ms`,
        '--ra-char-reveal': `${cfg.charRevealMs}ms`,
        '--ra-active-emphasis': `${isChar ? cfg.charActiveEmphasisMs : cfg.wordActiveEmphasisMs}ms`,
        '--ra-clear-fade': `${cfg.clearFadeMs}ms`,
        '--ra-easing': cfg.easing,
        '--ra-highlight': hl,
        '--ra-base-color': baseColor,
        '--ra-reached-opacity': String(cfg.reachedOpacity),
        '--ra-unreached-opacity': String(cfg.unreachedOpacity),
        '--ra-active-scale': String(cfg.activeScale),
        '--ra-scale-pad': `${scalePad}px`,
        // Word-silhouette outline (base) + active outline/glow, as text-shadow.
        '--ra-word-shadow': baseOutline || 'none',
        '--ra-svg-word-shadow': svgBaseOutline || 'none',
        '--ra-word-shadow-active': [glow, activeOutline].filter(Boolean).join(', ') || 'none',
        // Glow-only (no outline) — used by the active CHAR in char granularity so
        // the active letter glows without re-stroking each glyph (the legibility
        // stroke stays on the word silhouette in char mode).
        '--ra-glow': glow || 'none',
        '--ra-svg-glow': cfg.activeGlowPx > 0
            ? `drop-shadow(0 0 ${cfg.activeGlowPx}px ${hl})`
            : 'none',
        '--ra-font': cfg.fontFamily,
        '--ra-font-size': `${cfg.fontSizePx}px`,
        '--ra-line-height': String(cfg.lineHeight),
        '--ra-word-spacing': `${cfg.wordSpacingPx}px`,
        '--ra-letter-spacing': `${cfg.letterSpacingPx}px`,
        '--ra-align': cfg.centerLine ? 'center' : 'right',
    };
}

/** Serialize a config object into the `cssText` of inline custom properties. */
export function cssVarText(cfg: RecitationAnimConfig, theme: Theme = 'dark'): string {
    return Object.entries(cssVars(cfg, theme))
        .map(([k, v]) => `${k}: ${v}`)
        .join('; ');
}
