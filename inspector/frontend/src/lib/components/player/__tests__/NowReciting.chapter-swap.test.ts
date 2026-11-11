import { render } from '@testing-library/svelte';
import { tick } from 'svelte';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChapterRecitationData } from '../../../recitation-data/load-chapter';
import type { AnimUnit } from '../../../recitation-animation/types';
import { recitationAyahAt } from '../../../recitation-animation/recitation-settings';
import { playerContext } from '../../../stores/player-context';
import type { PublicDelivery, PublicReciter } from '../../../types/generated/schemas';
import NowReciting from '../NowReciting.svelte';

/**
 * Regression guard for the teleprompter reading the NEW chapter's clock
 * against the PREVIOUS chapter's units.
 *
 * A cross-chapter jump (random ayah / shuffle) switches `playerContext` — and
 * the audio — before NowReciting's own chapter load lands. The old units must
 * not stay live in that window (they lit a wrong ayah of the old surah while
 * the analysis grid showed the right one), and the new units must not wait on
 * the (large, optional) shaped-glyph fixture.
 */

const loads = new Map<number, (data: ChapterRecitationData) => void>();

vi.mock('../../../recitation-data/load-chapter', () => ({
    loadChapterRecitation: (_slug: string, chapter: number) =>
        new Promise<ChapterRecitationData>((resolve) => loads.set(chapter, resolve)),
}));

// The glyph fixture never lands: the units must not depend on it.
vi.mock('../../../recitation-animation/shaped-glyphs', async (importOriginal) => ({
    ...(await importOriginal<object>()),
    loadShapedGlyphs: () => new Promise(() => {}),
}));

function chapter(surah: number): ChapterRecitationData {
    const units: AnimUnit[] = [1, 2].map((ayah) => ({
        location: `${surah}:${ayah}:1`, ayahKey: `${surah}:${ayah}`, surah, ayah, word: 1,
        text: `w${surah}${ayah}`, start: ayah - 1, end: ayah,
        intervals: [{ start: ayah - 1, end: ayah }], letters: [],
    }));
    return {
        units,
        ayahs: units.map((u) => ({
            ayahKey: u.ayahKey, surah, ayah: u.ayah, startMs: u.start * 1000, endMs: u.end * 1000,
        })),
        contentEndMs: 2000,
        riwayah: 'hafs',
    };
}

async function flush(): Promise<void> {
    for (let i = 0; i < 5; i++) {
        await Promise.resolve();
        await tick();
    }
}

const reciter = { slug: 'r' } as unknown as PublicReciter;
const delivery = { slug: 'r', bucket: 'published' } as unknown as PublicDelivery;

describe('NowReciting chapter swap', () => {
    beforeEach(() => {
        loads.clear();
        vi.stubGlobal('requestAnimationFrame', () => 1);
        vi.stubGlobal('cancelAnimationFrame', () => {});
        vi.stubGlobal('ResizeObserver', class {
            observe(): void {}
            unobserve(): void {}
            disconnect(): void {}
        });
    });
    afterEach(() => {
        playerContext.update((s) => ({ ...s, reciter: null, delivery: null, surahNum: null }));
        vi.unstubAllGlobals();
    });

    it('drops the old chapter while the new one loads, without waiting on glyphs', async () => {
        playerContext.update((s) => ({ ...s, reciter, delivery, surahNum: 1, isPlaying: true }));
        render(NowReciting);
        await flush();
        loads.get(1)!(chapter(1));
        await flush();
        expect(get(recitationAyahAt)?.(500)).toBe('1:1');

        // Jump to surah 2: context (and audio) move on before its data lands.
        playerContext.update((s) => ({ ...s, surahNum: 2 }));
        await flush();
        expect(get(recitationAyahAt)).toBeNull();

        loads.get(2)!(chapter(2));
        await flush();
        expect(get(recitationAyahAt)?.(1500)).toBe('2:2');
    });
});
