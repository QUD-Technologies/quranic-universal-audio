/**
 * Waqf-mark render calibration for the packaged QPC edition faces.
 *
 * `@quranic-phonemizer/cells` ships `waqfRenderStyle()` tuned against
 * DigitalKhatt (Hafs). The QPC faces (Warsh / Qalun / Shu'bah) set a lone
 * combining mark much higher in the em, so the same `--waqf-*` variables need
 * different values. These match the upstream package's native table (4.x,
 * `waqfRenderStyle(mark, native)`); drop this file once the pin moves there.
 */

import { waqfRenderStyle } from '@quranic-phonemizer/cells';

interface WaqfRender {
    scale: number;
    shiftEm: number;
    raiseEm: number;
}

const QPC_BY_CODEPOINT: Record<string, WaqfRender> = {
    '6d6': { scale: 1.2, shiftEm: -0.11, raiseEm: 0.67 },
    '6d7': { scale: 1.2, shiftEm: -0.17, raiseEm: 0.58 },
    '6d8': { scale: 1.2, shiftEm: -0.13, raiseEm: 0.42 },
    '6dc': { scale: 1.2, shiftEm: 0.04, raiseEm: 0.38 },
};

/** The `--waqf-*` custom properties the cells `.pause-waqf` rule reads. */
export function qpcWaqfRenderStyle(mark: string, qpcFace: boolean): string {
    const cp = mark.codePointAt(0)?.toString(16);
    const render = qpcFace && cp ? QPC_BY_CODEPOINT[cp] : undefined;
    if (!render) return waqfRenderStyle(mark);
    return `--waqf-scale:${render.scale};--waqf-shift:${render.shiftEm}em;--waqf-raise:${render.raiseEm}em`;
}
