/**
 * Line measurement for the mushaf sizing (see `fit.ts`).
 *
 * Words are measured individually on a 2D canvas — the page renders each word
 * as its own flex item, so per-word advances are exactly what the line is made
 * of. Results are em (advance ÷ font size) and cached per font, so a spread is
 * a handful of Map lookups after the first visit.
 */
import { LINE_AYAH, type MushafLayout, type WordIndex } from './layout';
import { lineEm, percentile, REF_PERCENTILE } from './fit';

const MEASURE_PX = 100;
/** Pages sampled to size the column (every Nth page) — cheap and representative. */
const SAMPLE_STEP = 7;

export class LineMeasurer {
    private readonly ctx: CanvasRenderingContext2D | null;
    private readonly widths = new Map<string, number>();

    constructor(private readonly fontFamily: string) {
        this.ctx = document.createElement('canvas').getContext('2d');
        if (this.ctx) this.ctx.font = `${MEASURE_PX}px ${fontFamily}`;
    }

    wordEm(text: string): number {
        let w = this.widths.get(text);
        if (w === undefined) {
            w = this.ctx ? this.ctx.measureText(text).width / MEASURE_PX : text.length * 0.45;
            this.widths.set(text, w);
        }
        return w;
    }

    /** Natural em width of every justified (full, non-centred) ayah line on `page`. */
    justifiedLineEms(layout: MushafLayout, words: WordIndex, page: number): number[] {
        const out: number[] = [];
        for (const [kind, centered, a, b] of layout.pages[page - 1] ?? []) {
            if (kind !== LINE_AYAH || centered) continue;
            const ems: number[] = [];
            for (let id = a; id <= b; id++) ems.push(this.wordEm(words.text[id] ?? ''));
            out.push(lineEm(ems));
        }
        return out;
    }

    /** Widest justified line across `pages`. */
    maxEm(layout: MushafLayout, words: WordIndex, pages: number[]): number {
        let max = 0;
        for (const p of pages) for (const em of this.justifiedLineEms(layout, words, p)) max = Math.max(max, em);
        return max;
    }

    /** Column width (em) a typical dense line needs, sampled across the mushaf. */
    referenceEm(layout: MushafLayout, words: WordIndex): number {
        const ems: number[] = [];
        for (let p = 3; p <= layout.pages.length; p += SAMPLE_STEP) ems.push(...this.justifiedLineEms(layout, words, p));
        return percentile(ems, REF_PERCENTILE);
    }
}
