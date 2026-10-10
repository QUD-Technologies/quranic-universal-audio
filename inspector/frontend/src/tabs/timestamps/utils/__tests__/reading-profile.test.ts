import { describe, expect, it } from 'vitest';

import type { TsReadingRow } from '../../../../lib/types/generated/schemas';
import { buildProfile } from '../reading-profile';

const v = (surah: number, ayah: number, label = `${surah}:${ayah}`) => ({ surah, ayah, label });

const ROWS: TsReadingRow[] = [
    {
        selector: 'yabsut', key: 'yabsut', texts: ['وَيَبْصُۜطُ'],
        options: [{ option: 'seen', verses: [] }, { option: 'saad', verses: [v(2, 245)] }],
    },
    {
        selector: 'istifham_article', key: 'istifham_article/allah', texts: ['ءَآللَّهُ'],
        options: [{ option: 'ibdal', verses: [v(27, 59)] }, { option: 'tashil', verses: [v(10, 59)] }],
    },
    {
        selector: 'almusaytirun', key: 'almusaytirun', texts: ['ٱلْمُصَۣيْطِرُونَ'],
        options: [{ option: 'saad', verses: [v(52, 37)] }, { option: 'seen', verses: [] }],
    },
    {
        selector: 'iwaja_qayyima', key: 'iwaja_qayyima', texts: ['عِوَجَاۜ', 'قَيِّمًا'],
        options: [{ option: 'sakt', verses: [v(18, 1, '18:1–2')] }],
    },
    { selector: 'raa_firq', key: 'raa_firq', texts: ['فِرْقٍ'], options: [{ option: 'light', verses: [v(26, 63)] }] },
];

describe('buildProfile', () => {
    const groups = buildProfile(ROWS);

    it('groups rows in catalogue order and drops selectors never shown', () => {
        expect(groups.map((g) => [g.group, g.words.map((w) => w.key)])).toEqual([
            ['hamza', ['istifham_article/allah']],
            ['letters', ['yabsut', 'almusaytirun']],
            ['sakt', ['iwaja_qayyima']],
        ]);
    });

    it('orders options by family and keeps both read options where occurrences differ', () => {
        const allah = groups[0]!.words[0]!;
        expect(allah.options.map((o) => [o.option, o.verses.map((x) => x.label)])).toEqual([
            ['ibdal', ['27:59']], ['tashil', ['10:59']],
        ]);
        const musaytirun = groups[1]!.words[1]!;
        expect(musaytirun.options.map((o) => o.option)).toEqual(['seen', 'saad']);
    });

    it('fills an option the summary left out with no verses', () => {
        const sakt = groups[2]!.words[0]!;
        expect(sakt.texts).toEqual(['عِوَجَاۜ', 'قَيِّمًا']);
        expect(sakt.options).toEqual([
            { option: 'sakt', verses: [v(18, 1, '18:1–2')] },
            { option: 'idraj', verses: [] },
        ]);
    });
});
