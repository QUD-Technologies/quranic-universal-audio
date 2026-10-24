import { describe, expect, it } from 'vitest';

import { buildChapterRecitation } from '../../recitation-animation/chapter-words';
import { buildFilmstripModel } from '../../recitation-animation/filmstrip-model';
import type { TsWordShardResponse } from '../../types/ts-client';
import { decodeTimestampShard } from '../compact-shards';
import { computeChapterCoverage } from '../coverage';
import { chapterOccasions } from '../occasions';
import { isSpecialRef, isVerseRef, openerSourceRef } from '../specials';
import { basmalaShard, BASMALA_FIXTURE_CHAPTER } from '../test-native-fixture';
import { WORD_SHARD } from '../test-word-fixture';
import { assembleOccasion, chapterVerseRefs, shardOccasions } from '../ts-source';

const BY_SURAH = { audio_category: 'by_surah' as const };

function recitation() {
    const shard = basmalaShard();
    const occasions = shardOccasions(shard).map((occ) => ({
        verseRef: occ.ref,
        data: assembleOccasion('r', occ, {}, {}, BY_SURAH, 'chapter.mp3'),
        waslOutTo: occ.waslOutTo,
        special: occ.special,
    }));
    return buildChapterRecitation('r', BASMALA_FIXTURE_CHAPTER, occasions);
}

describe('special refs', () => {
    it('treats a chapter-0 ref or location as an opener, never a verse', () => {
        expect(isSpecialRef('0:2')).toBe(true);
        expect(isSpecialRef('0:1:3')).toBe(true);
        expect(isVerseRef('0:2')).toBe(false);
        expect(isVerseRef('2:1')).toBe(true);
        expect(isVerseRef('10:2')).toBe(true);
    });
});

describe('opener source words', () => {
    it('reads each opener word as the verse word it is analysed as', () => {
        expect(['0:2:1', '0:2:4'].map(openerSourceRef)).toEqual(['1:1:1', '1:1:4']);
        expect(['0:1:1', '0:1:2', '0:1:5'].map(openerSourceRef)).toEqual(['2:67:15', '16:98:5', '16:98:8']);
        expect(openerSourceRef('2:1:1')).toBeNull();
        expect(openerSourceRef('0:2:5')).toBeNull();
    });

    it("gives an opener word its source word's QPC and DigitalKhatt text", () => {
        const [opener] = shardOccasions(basmalaShard());
        const qpc = { '1:1:2': { text: 'qpc-allah' } };
        const dk = { '1:1:2': { text: 'dk-allah' } };
        const data = assembleOccasion('r', opener!, qpc, dk, BY_SURAH, 'chapter.mp3');
        expect(data.words[1]).toMatchObject({ location: '0:2:2', text: 'qpc-allah', display_text: 'dk-allah' });
    });
});

describe('special occasions', () => {
    it('keeps the Basmala its own occasion ahead of ayah 1, with the shard chapter', () => {
        const occasions = shardOccasions(basmalaShard());
        expect(occasions.map((one) => one.ref)).toEqual(['0:2', '2:1', '2:2']);
        expect(occasions.map((one) => one.special)).toEqual(['Basmala', null, null]);
        expect(occasions.every((one) => one.chapter === BASMALA_FIXTURE_CHAPTER)).toBe(true);
        // Its four word parts merge into one occasion, which never bridges into ayah 1.
        expect(occasions[0]!.parts).toHaveLength(4);
        expect(occasions[0]!.waslOutTo).toBeNull();
    });

    it('leaves openers out of the verse refs (random target, bookmark lookup)', () => {
        expect(chapterVerseRefs(basmalaShard())).toEqual(['2:1', '2:2']);
    });

    it('assembles an opener with the shard chapter, not the 0 of its ref', () => {
        const [opener, ayah1] = shardOccasions(basmalaShard());
        const data = assembleOccasion('r', opener!, {}, {}, BY_SURAH, 'chapter.mp3');
        expect(data.chapter).toBe(BASMALA_FIXTURE_CHAPTER);
        expect(data.verse_ref).toBe('0:2');
        expect(data.special).toBe('Basmala');
        expect(data.words.map((word) => word.location)).toEqual(['0:2:1', '0:2:2', '0:2:3', '0:2:4']);
        // The opener is a full native reading: its own cells and letter timings.
        expect(data.native).toHaveLength(1);
        expect(data.words[0]!.letters).toHaveLength(1);
        const verse = assembleOccasion('r', ayah1!, {}, {}, BY_SURAH, 'chapter.mp3');
        expect(verse.chapter).toBe(BASMALA_FIXTURE_CHAPTER);
        expect(verse.special).toBeNull();
    });
});

describe('special word-profile readings', () => {
    const SHIFT_MS = 1000;
    const verse = WORD_SHARD.readings[0]!;
    const stored = {
        ...WORD_SHARD,
        readings: [
            {
                id: 'r0',
                parts: [['0:2', 0, 900, 0, 4]],
                words: [
                    ['0:2:1', 'بِسْمِ', 0, 200],
                    ['0:2:2', 'اِ۬للَّهِ', 200, 450],
                    ['0:2:3', 'اِ۬لرَّحْمَٰنِ', 450, 700],
                    ['0:2:4', 'اِ۬لرَّحِيمِ', 700, 900],
                ],
                boundaries: [[1, null], [1, null], [1, null], [3, null]],
                special: 'Basmala',
            },
            {
                ...verse,
                parts: verse.parts.map(([ref, start, end, first, count]) =>
                    [ref, Number(start) + SHIFT_MS, Number(end) + SHIFT_MS, first, count]),
                words: verse.words.map(([ref, text, start, end]) =>
                    [ref, text, Number(start) + SHIFT_MS, Number(end) + SHIFT_MS]),
            },
        ],
    };

    it('decodes the special field and keeps it out of the verse occasions', () => {
        const shard = decodeTimestampShard(stored) as TsWordShardResponse;
        expect(shard.readings[0]!.special).toBe('Basmala');
        expect(shard.readings[1]!.special).toBeUndefined();
        const occasions = chapterOccasions(shard.readings, shard._meta.chapter);
        expect(occasions.map((one) => [one.ref, one.special])).toEqual([['0:2', 'Basmala'], ['112:1', null]]);
        const data = assembleOccasion('r', occasions[0]!, {}, {}, BY_SURAH, 'chapter.mp3');
        expect(data.chapter).toBe(112);
        expect(data.special).toBe('Basmala');
        // The opener's last boundary ends no verse, so it carries no verse marker.
        expect(data.wordReadings[0]!.words.at(-1)!.boundary?.verseEnd).toBeNull();
    });
});

describe('special recitation units', () => {
    it('flags opener units, orders them before ayah 1, and never merges them with a verse', () => {
        const built = recitation();
        expect(built.units.map((unit) => unit.location)).toEqual([
            '0:2:1', '0:2:2', '0:2:3', '0:2:4', '2:1:1', '2:2:1', '2:2:2',
        ]);
        expect(built.units.slice(0, 4).every((unit) => unit.special === 'Basmala')).toBe(true);
        expect(built.units.slice(4).every((unit) => unit.special === undefined)).toBe(true);
        expect(built.ayahs.map((ayah) => ayah.ayahKey)).toEqual(['0:2', '2:1', '2:2']);
    });

    it('leaves openers out of chapter coverage', () => {
        const built = recitation();
        const coverage = computeChapterCoverage(
            built.units, BASMALA_FIXTURE_CHAPTER, new Map([[1, 1], [2, 2], [3, 3]]),
        );
        // Ayah 2 is complete; the Basmala's `0:2` words never count toward it.
        expect(coverage.status.get(2)).toBeUndefined();
        expect(coverage.status.get(3)).toBe('full');
    });

    it('puts the opener cell first in the filmstrip, distinct from ayah 2', () => {
        const built = recitation();
        const coverage = computeChapterCoverage(
            built.units, BASMALA_FIXTURE_CHAPTER, new Map([[1, 1], [2, 3], [3, 3]]),
        );
        const model = buildFilmstripModel(built.units, 'duration', coverage);
        expect(model.cells.map((cell) => [cell.ayahKey, cell.special, cell.missing])).toEqual([
            ['0:2', 'Basmala', 'none'],
            ['2:1', null, 'none'],
            ['2:2', null, 'words'],
            ['2:3', null, 'full'],
        ]);
        expect(model.indexByAyahKey.get('0:2')).toBe(0);
        expect(model.cellOfUnit[0]).toBe(0);
        expect(model.cellOfUnit[4]).toBe(1);
        // The opener's silence before ayah 1 is a between-cell gap (3.0 → 3.4 s).
        expect(model.cells[0]!.nextGapSec).toBeCloseTo(0.4, 6);
        expect(model.cells[0]!.waslNext).toBe(false);
    });
});
