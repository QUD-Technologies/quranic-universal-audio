import { render } from '@testing-library/svelte';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChapterCoverage } from '../../recitation-data/coverage';
import AyahFilmstrip from '../AyahFilmstrip.svelte';
import { DEFAULT_RECITATION_CONFIG } from '../config';
import { type ActiveCellInfo, buildFilmstripModel } from '../filmstrip-model';
import type { AnimUnit, TimeSpan } from '../types';

/**
 * Component-level coverage markers: a fully-missing verse renders as a
 * red placeholder cell (no fill, `missing-full`), an incomplete verse rings
 * `missing-words`, and the active-cell callback reports the selected verse's
 * status so the parent can drive the contextual pill.
 */

function unit(location: string, intervals: Array<[number, number]>): AnimUnit {
    const [s, a, w] = location.split(':').map(Number) as [number, number, number];
    const spans: TimeSpan[] = intervals.map(([start, end]) => ({ start, end }));
    return {
        location,
        ayahKey: `${s}:${a}`,
        surah: s,
        ayah: a,
        word: w,
        text: location,
        start: spans[0]!.start,
        end: spans[spans.length - 1]!.end,
        intervals: spans,
        letters: [],
    };
}

// Verse 1 (words 1,2) + verse 3 (word 1) recited; verse 2 fully missing; verse 1
// missing word 3.
const units = [
    unit('1:1:1', [[0, 1]]),
    unit('1:1:2', [[1, 2]]),
    unit('1:3:1', [[5, 6]]),
];
const coverage: ChapterCoverage = {
    numVerses: 3,
    status: new Map([[1, 'words'], [2, 'full']]),
    missingWords: new Map([[1, [3]]]),
};

describe('AyahFilmstrip coverage markers', () => {
    it('renders a fully-missing verse as a non-filled placeholder cell', () => {
        const model = buildFilmstripModel(units, 'duration', coverage);
        const { container } = render(AyahFilmstrip, {
            units, model, durationMs: 6000, getTimeMs: () => 0, playing: false,
            config: DEFAULT_RECITATION_CONFIG, onSeek: () => {},
        });
        const full = container.querySelectorAll('.cell.missing-full');
        expect(full.length).toBe(1);
        expect(full[0]!.querySelector('.cell-num')!.textContent).toBe('2');
        // placeholder carries no progress fill element
        expect(full[0]!.querySelector('.cell-fill')).toBeNull();
        // and is tooltip-tagged "Not recited"
        expect(full[0]!.getAttribute('title')).toBe('Not recited');
    });

    it('rings an incomplete verse and tooltips its missing words', () => {
        const model = buildFilmstripModel(units, 'duration', coverage);
        const { container } = render(AyahFilmstrip, {
            units, model, durationMs: 6000, getTimeMs: () => 0, playing: false,
            config: DEFAULT_RECITATION_CONFIG, onSeek: () => {},
            missingWordsByAyah: coverage.missingWords,
        });
        const words = container.querySelectorAll('.cell.missing-words');
        expect(words.length).toBe(1);
        expect(words[0]!.getAttribute('title')).toBe('Missing words: 3');
    });
});

describe('AyahFilmstrip active-cell reporting', () => {
    let rafCbs: FrameRequestCallback[] = [];
    let nowMs = 0;

    beforeEach(() => {
        rafCbs = [];
        nowMs = 0;
        vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback): number => {
            rafCbs.push(cb);
            return rafCbs.length;
        });
        vi.stubGlobal('cancelAnimationFrame', () => {});
        vi.spyOn(performance, 'now').mockImplementation(() => nowMs);
    });
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    async function step(toMs: number): Promise<void> {
        nowMs = toMs;
        for (let i = 0; i < 2 && rafCbs.length; i++) {
            const batch = rafCbs;
            rafCbs = [];
            for (const cb of batch) cb(nowMs);
            await tick();
        }
    }

    it('reports the active verse + status to onActiveCell', async () => {
        const model = buildFilmstripModel(units, 'duration', coverage);
        const calls: Array<ActiveCellInfo | null> = [];
        render(AyahFilmstrip, {
            units, model, durationMs: 6000, getTimeMs: () => nowMs, playing: true,
            config: { ...DEFAULT_RECITATION_CONFIG, leadMs: 0 },
            onSeek: () => {},
            onActiveCell: (info: ActiveCellInfo | null) => calls.push(info),
        });
        await tick();
        // Playing verse 1 (incomplete) → the pill source must read 'words'.
        await step(500);
        expect(calls.at(-1)).toEqual({ ayah: 1, missing: 'words', special: null });
        // Travel to verse 3 (complete) → status clears to 'none'.
        await step(5500);
        expect(calls.at(-1)).toEqual({ ayah: 3, missing: 'none', special: null });
    });
});
