/**
 * Waqf-mark render calibration for the packaged QPC edition faces.
 *
 * `@quranic-phonemizer/cells` ships `waqfRenderStyle()` tuned against
 * DigitalKhatt (Hafs). The QPC faces (Warsh / Qalun / Shu'bah) draw a lone
 * combining mark far higher in the em — and at their own size and horizontal
 * offset, which differ per mark AND per face — so the same `--waqf-*` values
 * put the ink outside the pause tile entirely.
 *
 * The per-face numbers are measured off the shipped fonts by
 * `scripts/codegen/regen_waqf_render.py`; see the generated table for the
 * targets. A face that has no glyph for a mark is deliberately absent from the
 * table: the browser falls through the `--font-quran` stack to DigitalKhatt, so
 * the package's own calibration is the correct one there.
 */

import { waqfRenderStyle } from '@quranic-phonemizer/cells';

import { QPC_WAQF_RENDER } from './waqf-render-table';

/**
 * The `--waqf-*` custom properties the cells `.pause-waqf` rule reads.
 *
 * `sdkRiwayah` is the **SDK** slug (`warsh` / `qalun` / `shuba`); Hafs and any
 * unknown slug fall to the package's DigitalKhatt calibration.
 */
export function qpcWaqfRenderStyle(mark: string, sdkRiwayah: string | null | undefined): string {
    const cp = mark.codePointAt(0)?.toString(16);
    const render = sdkRiwayah && cp ? QPC_WAQF_RENDER[sdkRiwayah]?.[cp] : undefined;
    if (!render) return waqfRenderStyle(mark);
    return `--waqf-scale:${render.scale};--waqf-shift:${render.shiftEm}em;--waqf-raise:${render.raiseEm}em`;
}
