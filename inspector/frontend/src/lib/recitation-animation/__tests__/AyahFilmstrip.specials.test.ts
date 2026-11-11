import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { ChapterCoverage } from '../../recitation-data/coverage';
import AyahFilmstrip from '../AyahFilmstrip.svelte';
import { DEFAULT_RECITATION_CONFIG } from '../config';
import { buildFilmstripModel } from '../filmstrip-model';
import type { AnimUnit } from '../types';

/**
 * A recited opener (Basmala on `0:2`) gets its own named cell ahead of ayah 1.
 * Its `ayah` is 2, so the chapter's real ayah 2 would collide with it on any
 * ayah-keyed path (the old `{#each}` key crashed on the duplicate).
 */

function unit(location: string, start: number, end: number, special?: 'Basmala'): AnimUnit {
    const [s, a, w] = location.split(':').map(Number) as [number, number, number];
    return {
        location,
        ayahKey: `${s}:${a}`,
        surah: s,
        ayah: a,
        word: w,
        text: location,
        start,
        end,
        intervals: [{ start, end }],
        letters: [],
        ...(special ? { special } : {}),
    };
}

const units = [
    unit('0:2:1', 1, 1.5, 'Basmala'),
    unit('0:2:2', 1.5, 2, 'Basmala'),
    unit('2:1:1', 3, 4),
    unit('2:2:1', 4.5, 5.5),
];
const coverage: ChapterCoverage = {
    numVerses: 3,
    status: new Map([[3, 'full']]),
    missingWords: new Map(),
};

function renderStrip() {
    const model = buildFilmstripModel(units, 'duration', coverage);
    return render(AyahFilmstrip, {
        units, model, durationMs: 6000, getTimeMs: () => 0, playing: false,
        config: DEFAULT_RECITATION_CONFIG, onSeek: () => {},
    });
}

describe('AyahFilmstrip recited openers', () => {
    it('labels the opener cell by name ahead of the numbered verses', () => {
        const { container } = renderStrip();
        const labels = [...container.querySelectorAll('.cell .cell-num')].map((el) => el.textContent);
        expect(labels).toEqual(['Basmala', '1', '2', '3']);
        const opener = container.querySelector('.cell.special')!;
        expect(opener.querySelector('.cell-num')!.classList).toContain('cell-name');
        expect(opener.getAttribute('title')).toBe('Basmala');
    });

    it('floors the opener cell wide enough for its name', () => {
        const { container } = renderStrip();
        const [opener, ayah1] = container.querySelectorAll<HTMLElement>('.cell');
        // 1 s at the 12 px/s ruler would be 12 px; the name needs its own floor,
        // wider than a verse number's.
        expect(parseFloat(opener!.style.width)).toBeGreaterThan(
            DEFAULT_RECITATION_CONFIG.filmstripMinCellPx,
        );
        expect(parseFloat(ayah1!.style.width)).toBe(DEFAULT_RECITATION_CONFIG.filmstripMinCellPx);
    });

    it('announces the opener by name as the slider value before ayah 1', () => {
        const { container } = renderStrip();
        const slider = container.querySelector('[role="slider"]')!;
        expect(slider.getAttribute('aria-valuemin')).toBe('0');
        expect(slider.getAttribute('aria-valuemax')).toBe('3');
        expect(slider.getAttribute('aria-valuenow')).toBe('0');
        expect(slider.getAttribute('aria-valuetext')).toBe('Basmala');
    });
});
