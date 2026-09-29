/**
 * Mushaf sizing — natural word spacing instead of kashida justification.
 *
 * Every line's natural width is measured once in `em` (sum of its word
 * advances + a minimum inter-word gap). The page's text column is a fixed
 * width per viewport, sized so a typical dense line fills it at the largest
 * font the height allows. A spread whose densest line is wider than that
 * shrinks its font just enough to fit — so full lines only ever stretch by a
 * small slack, and the text changes size only on unusually dense spreads.
 */

/** Minimum gap between words, in em — tight but never touching. */
export const MIN_WORD_GAP_EM = 0.2;
/** Line pitch ÷ font size. DK stacks marks high and low; below ~1.75 they collide. */
export const PITCH_MIN = 1.75;
/** Upper bound on the pitch ratio, so a wide-but-short window doesn't space lines out. */
export const PITCH_MAX = 2.3;
/** Page rows: 15 text lines + a header and a page-number row (≈0.8 line each). */
export const PAGE_ROWS = 15 + 1.6;
/** Page padding in em: inline each side, block each side. */
export const PAD_INLINE_EM = 1.1;
export const PAD_BLOCK_EM = 0.9;
/** Gap between the two pages at the spine, px. */
export const SPINE_PX = 2;
/** Outer margin around the spread, px. */
export const OUTER_PX = 16;
/** Percentile of line widths the column is sized for (denser spreads shrink the font). */
export const REF_PERCENTILE = 0.95;
/** Show two pages only if they can be at least this large relative to one… */
export const SPREAD_MIN_RATIO = 0.8;
/** …or at least this font size outright. */
export const SPREAD_MIN_FONT_PX = 24;

export interface Viewport {
    width: number;
    height: number;
}

export interface PageMetrics {
    spread: boolean;
    /** Base font size (px) before any per-spread shrink. */
    fontPx: number;
    /** Line pitch (px). */
    pitchPx: number;
    /** Text column width (px) — constant across spreads. */
    columnPx: number;
    /** Page box (px). */
    pageWidthPx: number;
    pageHeightPx: number;
}

/** Natural width of a justified line in em. */
export function lineEm(wordEms: number[]): number {
    if (!wordEms.length) return 0;
    return wordEms.reduce((s, w) => s + w, 0) + (wordEms.length - 1) * MIN_WORD_GAP_EM;
}

export function percentile(values: number[], p: number): number {
    if (!values.length) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.floor(p * (sorted.length - 1)))]!;
}

function fitFor(vp: Viewport, pages: number, refEm: number): PageMetrics {
    const heightFont = (vp.height - 2 * OUTER_PX) / (PAGE_ROWS * PITCH_MIN + 2 * PAD_BLOCK_EM);
    const perPage = (vp.width - 2 * OUTER_PX - (pages - 1) * SPINE_PX) / pages;
    const widthFont = perPage / (refEm + 2 * PAD_INLINE_EM);
    const fontPx = Math.max(1, Math.min(heightFont, widthFont));
    // Height-bound: the pitch fills the height at PITCH_MIN. Width-bound: the
    // spare height goes to line spacing, capped so lines don't drift apart.
    const pitchFromHeight = (vp.height - 2 * OUTER_PX - 2 * PAD_BLOCK_EM * fontPx) / PAGE_ROWS;
    const pitchPx = Math.min(pitchFromHeight, fontPx * PITCH_MAX);
    const columnPx = refEm * fontPx;
    return {
        spread: pages === 2,
        fontPx,
        pitchPx,
        columnPx,
        pageWidthPx: columnPx + 2 * PAD_INLINE_EM * fontPx,
        pageHeightPx: PAGE_ROWS * pitchPx + 2 * PAD_BLOCK_EM * fontPx,
    };
}

/** Pick one or two pages for the viewport and size them. */
export function fitPages(vp: Viewport, refEm: number): PageMetrics {
    const one = fitFor(vp, 1, refEm);
    const two = fitFor(vp, 2, refEm);
    const spreadOk = two.fontPx >= one.fontPx * SPREAD_MIN_RATIO || two.fontPx >= SPREAD_MIN_FONT_PX;
    return spreadOk ? two : one;
}

/** Font size for a spread whose densest justified line is `maxEm` wide. */
export function spreadFontPx(m: PageMetrics, maxEm: number): number {
    return maxEm > 0 ? Math.min(m.fontPx, m.columnPx / maxEm) : m.fontPx;
}
