import { render } from '@testing-library/svelte';
import { tick } from 'svelte';
import { describe, expect, it } from 'vitest';

import chapterTwoShapedJson from '../../../../public/generated/shaped-glyphs-v13/2.json?raw';
import shapedPathsJson from '../../../../public/generated/shaped-glyphs-v13/paths.json?raw';

import { DEFAULT_RECITATION_CONFIG } from '../config';
import LineAnimation from '../LineAnimation.svelte';
import type { ShapedGlyphFixture } from '../shaped-glyphs';
import type { AnimUnit } from '../types';

/** "ٱللَّهِ" — the Basmala's 1:1:2, whose DK text is also a shaped chapter-2 verse word. */
const ALLAH = 'ٱللَّهِ';

const shapedGlyphs = {
    ...JSON.parse(chapterTwoShapedJson),
    paths: JSON.parse(shapedPathsJson).paths,
} as ShapedGlyphFixture;

function unit(location: string, start: number, end: number, opts: { special?: boolean; text?: string } = {}): AnimUnit {
    const [s, a, w] = location.split(':').map(Number) as [number, number, number];
    return {
        location, ayahKey: `${s}:${a}`, surah: s, ayah: a, word: w,
        text: opts.text ?? location, start, end, intervals: [{ start, end }],
        letters: [{ char: 'x', start, end }],
        ...(opts.special ? { special: 'Basmala' as const } : {}),
    };
}

describe('LineAnimation — recited openers', () => {
    it('ends an opener with no ۝ marker while the verse after it keeps one', async () => {
        const units = [
            unit('0:2:1', 0, 1, { special: true }),
            unit('0:2:2', 1, 2, { special: true }),
            unit('2:1:1', 3, 4),
        ];
        const { container } = render(LineAnimation, {
            units,
            config: {
                ...DEFAULT_RECITATION_CONFIG,
                granularity: 'word' as const,
                clearOnAyahEnd: false,
                clearOnOverflow: false,
                showAyahMarker: true,
            },
            getTimeMs: () => 0,
            playing: false,
        });
        await tick();
        expect(container.querySelectorAll('.ra-word').length).toBe(3);
        const markers = [...container.querySelectorAll<HTMLElement>('.ra-ayah-marker')];
        expect(markers.map((marker) => marker.dataset.afterWord)).toEqual(['2']);
    });

    it("draws an opener's word with its source verse word's shaped outline, like a verse", async () => {
        const config = {
            ...DEFAULT_RECITATION_CONFIG,
            granularity: 'char' as const,
            clearOnAyahEnd: false,
            clearOnOverflow: false,
            showAyahMarker: false,
        };
        expect(shapedGlyphs.words[ALLAH]).toBeDefined();
        const opener = render(LineAnimation, {
            units: [unit('0:2:2', 0, 1, { special: true, text: ALLAH })],
            config, getTimeMs: () => 0, playing: false, shapedGlyphs,
        });
        await tick();
        expect(opener.container.querySelector('.ra-shaped-svg')).not.toBeNull();
        expect(opener.container.querySelector('.ra-word-ink')).toBeNull();
    });
});
