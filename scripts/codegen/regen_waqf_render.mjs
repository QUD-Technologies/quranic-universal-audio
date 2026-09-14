#!/usr/bin/env node
/**
 * Regenerate the per-face waqf (stop mark) render calibration table.
 *
 * A lone waqf mark is a zero-advance combining glyph: the browser draws it at
 * the offset the face designed for a *base letter*, which is nowhere near the
 * centre of the pause tile that carries it in the Timestamps analysis view. So
 * centring one needs a per-(face, mark) scale + nudge, and the three packaged
 * QPC faces disagree with DigitalKhatt — and with each other — about all three.
 *
 * Those numbers used to be hand-tuned, which only ever covered the marks
 * someone happened to look at (Shu'bah's U+06DA was missing, so it rendered
 * with DigitalKhatt's offsets, an em above the tile, clipped to invisible).
 * They are measurable instead, and measuring them needs a real browser: the
 * glyph is shaped and mark-positioned against its word-joiner base, so a font's
 * own outline box does not predict where the ink lands. This renders each mark
 * in each face on a canvas, scans the painted pixels, and solves for the values
 * that put that ink in the middle of the tile:
 *
 *   scale = TARGET_INK_HEIGHT / ink height   (clamped — a small mark is grown to
 *                                             match the others, never past the
 *                                             point where it reads as a heavier
 *                                             weight than the word text)
 *   shift = -ink centre x                    (the span is zero-width, so its box
 *                                             centre is the text origin)
 *   raise = ink centre y - (ascent - descent) / 2
 *                                            (`.pause-waqf` is a line-height-1
 *                                             box centred in the tile, so its
 *                                             baseline sits (A-D)/2 below the
 *                                             tile's centre)
 *
 * A face with NO glyph for a mark is left out of its table on purpose: the
 * browser falls through the `--font-quran` stack to DigitalKhatt, so the cells
 * package's own DigitalKhatt calibration is the correct one there (Warsh and
 * Qalun ship no U+06DA / U+06DB).
 *
 * Run FROM inspector/frontend (playwright resolves from its node_modules):
 *   node ../../scripts/codegen/regen_waqf_render.mjs [--api https://…hf.space]
 */
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const requireFromCwd = createRequire(resolve(process.cwd(), 'package.json'));
const playwright = await import(pathToFileURL(requireFromCwd.resolve('playwright')));
const chromium = playwright.chromium ?? playwright.default?.chromium;

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const OUTPUT = resolve(REPO, 'inspector/frontend/src/lib/utils/waqf-render-table.ts');

/** Inspector slug (the font route's key) → SDK slug (the table's key). */
const FACES = {
    warsh_an_nafi: 'warsh',
    qalon_an_nafi: 'qalun',
    shubah_an_asim: 'shuba',
};

/**
 * Marks a pause tile can carry (`lib/utils/waqf.ts` STOP_MARKS) plus the
 * saktah, which rides the same `.pause-waqf` rule.
 */
const MARKS = ['06D6', '06D7', '06D8', '06DA', '06DB', '06DC'];

/**
 * Ink height every mark is scaled to, in em of the word font — the height the
 * DigitalKhatt (Hafs) tiles already render their marks at.
 */
const TARGET_INK_HEIGHT = 0.32;
const MIN_SCALE = 1.0;
const MAX_SCALE = 1.8;

/**
 * Font size the measurement renders at: big enough that one pixel of ink is
 * well under a thousandth of an em.
 */
const PROBE_PX = 200;

const { values } = parseArgs({
    options: { api: { type: 'string', default: 'https://hetchyy-quranic-universal-audio.hf.space' } },
});

async function fontDataUri(slug) {
    const url = `${values.api}/api/static/edition/${slug}/font`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
    const bytes = Buffer.from(await response.arrayBuffer());
    return `data:font/ttf;base64,${bytes.toString('base64')}`;
}

/** Ink box + vertical font metrics for one mark, in em, measured on a canvas. */
async function measure(page, family, codepoint) {
    return page.evaluate(
        ({ family, codepoint, probePx }) => {
            const mark = String.fromCodePoint(parseInt(codepoint, 16));
            // The DOM anchors the mark on a word joiner (a combining mark has no
            // base of its own to attach to); shape it the same way here.
            const text = `\u2060${mark}`;
            const size = probePx * 4;
            const origin = { x: size / 2, y: size * 0.7 };

            const inkOf = (font) => {
                const canvas = document.createElement('canvas');
                canvas.width = size;
                canvas.height = size;
                const ctx = canvas.getContext('2d', { willReadFrequently: true });
                ctx.font = font;
                ctx.textBaseline = 'alphabetic';
                ctx.fillStyle = '#000';
                ctx.fillText(text, origin.x, origin.y);
                const { data } = ctx.getImageData(0, 0, size, size);
                let x0 = Infinity;
                let y0 = Infinity;
                let x1 = -Infinity;
                let y1 = -Infinity;
                for (let y = 0; y < size; y += 1) {
                    for (let x = 0; x < size; x += 1) {
                        if (data[(y * size + x) * 4 + 3] > 24) {
                            if (x < x0) x0 = x;
                            if (x > x1) x1 = x;
                            if (y < y0) y0 = y;
                            if (y > y1) y1 = y;
                        }
                    }
                }
                return x1 < x0 ? null : { x0, y0, x1, y1 };
            };

            const faceFont = `${probePx}px '${family}'`;
            const ink = inkOf(faceFont);
            // No glyph in the face means the browser silently drew the fallback.
            // Compare against a family that cannot exist — same fallback, same
            // pixels — rather than trusting the paint.
            const fallback = inkOf(`${probePx}px '__qua_absent__'`);
            const same = ink && fallback
                && ink.x0 === fallback.x0 && ink.y0 === fallback.y0
                && ink.x1 === fallback.x1 && ink.y1 === fallback.y1;
            if (!ink || same) return null;

            const ctx = document.createElement('canvas').getContext('2d');
            ctx.font = faceFont;
            const metrics = ctx.measureText(text);
            return {
                cx: ((ink.x0 + ink.x1) / 2 - origin.x) / probePx,
                cy: (origin.y - (ink.y0 + ink.y1) / 2) / probePx,
                height: (ink.y1 - ink.y0 + 1) / probePx,
                ascent: metrics.fontBoundingBoxAscent / probePx,
                descent: metrics.fontBoundingBoxDescent / probePx,
            };
        },
        { family, codepoint, probePx: PROBE_PX },
    );
}

const round = (value) => Number(value.toFixed(3));

function calibrate({ cx, cy, height, ascent, descent }) {
    const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, TARGET_INK_HEIGHT / height));
    return {
        scale: round(scale),
        shiftEm: round(-cx),
        raiseEm: round(cy - (ascent - descent) / 2),
    };
}

function render(tables) {
    const lines = [
        '/* AUTOGENERATED by scripts/codegen/regen_waqf_render.mjs — do not edit. */',
        '',
        "/** Scale + nudge that centres one waqf mark's ink in a pause tile. */",
        'export interface WaqfRender {',
        '    /** Glyph font-size as a multiple of the word font. */',
        '    scale: number;',
        '    /** Horizontal nudge, em of the scaled glyph (negative is left). */',
        '    shiftEm: number;',
        '    /** Vertical nudge, em of the scaled glyph (negative is up). */',
        '    raiseEm: number;',
        '}',
        '',
        '/**',
        " * Per-QPC-face calibration, keyed by SDK riwayah slug then by the mark's",
        ' * lowercase hex codepoint.',
        ' *',
        " * A mark absent from a face's entry has no glyph in that face: the browser",
        " * falls through the font stack to DigitalKhatt, so the cells package's own",
        ' * DigitalKhatt calibration is the right one and the caller must defer to it.',
        ' * Hafs is absent entirely for the same reason.',
        ' */',
        'export const QPC_WAQF_RENDER: Record<string, Record<string, WaqfRender>> = {',
    ];
    for (const [face, table] of Object.entries(tables)) {
        lines.push(`    ${face}: {`);
        for (const [codepoint, r] of Object.entries(table)) {
            lines.push(
                `        '${codepoint}': { scale: ${r.scale}, `
                + `shiftEm: ${r.shiftEm}, raiseEm: ${r.raiseEm} },`,
            );
        }
        lines.push('    },');
    }
    lines.push('};', '');
    return lines.join('\n');
}

const faces = await Promise.all(
    Object.entries(FACES).map(async ([inspectorSlug, sdkSlug]) => ({
        sdkSlug,
        family: `QUAEdition-${inspectorSlug}`,
        uri: await fontDataUri(inspectorSlug),
    })),
);

const browser = await chromium.launch();
const page = await browser.newPage();
const faceRules = faces
    .map((face) => `@font-face{font-family:'${face.family}';src:url(${face.uri});}`)
    .join('');
await page.setContent(`<style>${faceRules}</style><body>.</body>`);
await page.evaluate(
    (families) => Promise.all(families.map((family) => document.fonts.load(`200px '${family}'`))),
    faces.map((face) => face.family),
);

const tables = {};
for (const face of faces) {
    const table = {};
    for (const codepoint of MARKS) {
        const measured = await measure(page, face.family, codepoint);
        // Keyed the way the DOM asks for it: `codePointAt().toString(16)`.
        if (measured) table[parseInt(codepoint, 16).toString(16)] = calibrate(measured);
    }
    if (!Object.keys(table).length) throw new Error(`no waqf glyphs measured for ${face.sdkSlug}`);
    tables[face.sdkSlug] = table;
}
await browser.close();

writeFileSync(OUTPUT, render(tables), 'utf8');
const counts = Object.entries(tables)
    .map(([face, table]) => `${face}=${Object.keys(table).length}`)
    .join(', ');
console.log(`Generated ${OUTPUT} (${counts})`);
