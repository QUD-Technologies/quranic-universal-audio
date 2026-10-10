import { describe, expect, it } from 'vitest';

import type { TsReadingVariant } from '../../../../lib/types/generated/schemas';
import { buildProfile, type RawChoiceShard, shardChoices } from '../reading-profile';

function variant(id: string, chosen: string, words: number[], by: TsReadingVariant['by'] = 'scored'): TsReadingVariant {
    return {
        id, chosen, words: words as TsReadingVariant['words'], targets: words as TsReadingVariant['targets'],
        anchor: words.length > 1 ? 'boundary' : 'word', boundary: words.length > 1 ? 1 : null,
        by, score: 1, affected: {},
    };
}

function reading(words: Array<[string, string]>, variants: TsReadingVariant[]): NonNullable<RawChoiceShard['readings']>[number] {
    return { render: { w: words }, variants };
}

const SHARD_10: RawChoiceShard = {
    _meta: { schema_version: 15 },
    readings: [
        reading([['10:51:7', 'ءَآلْـَٔـٰنَ']], [variant('istifham_article', 'tashil', [0])]),
        reading([['10:59:13', 'قُلْ'], ['10:59:14', 'ءَآللَّهُ']], [variant('istifham_article', 'tashil', [1])]),
        reading([['10:91:1', 'ءَآلْـَٔـٰنَ']], [variant('istifham_article', 'tashil', [0])]),
    ],
};

const SHARD_OTHERS: RawChoiceShard = {
    _meta: { schema_version: 15 },
    readings: [
        reading([['27:59:9', 'ءَآللَّهُ']], [variant('istifham_article', 'ibdal', [0])]),
        reading([['6:143:10', 'ءَآلذَّكَرَيْنِ']], [variant('istifham_article', 'ibdal', [0])]),
        reading([['18:1:11', 'عِوَجَاۜ'], ['18:2:1', 'قَيِّمًا']], [variant('iwaja_qayyima', 'sakt', [0, 1])]),
        reading([['11:42:14', 'ٱرْكَب'], ['11:42:15', 'مَّعَنَا']], [variant('irkab_maana', 'idgham', [0, 1])]),
        reading([['11:42:14', 'ٱرْكَب'], ['11:42:15', 'مَّعَنَا']], [variant('irkab_maana', 'idgham', [0, 1])]),
        reading([['26:63:11', 'فِرْقٍ']], [variant('raa_firq', 'light', [0])]),
        reading([['2:245:14', 'وَيَبْصُۜطُ']], [variant('yabsut', 'seen', [0], 'default')]),
    ],
};

describe('shardChoices', () => {
    it('keeps shown selectors with their word refs and text', () => {
        expect(shardChoices(SHARD_10)[1]).toEqual({
            selector: 'istifham_article', option: 'tashil', wordRefs: ['10:59:14'], texts: ['ءَآللَّهُ'],
        });
    });

    it('drops hidden selectors and picks made without evidence', () => {
        const selectors = shardChoices(SHARD_OTHERS).map((hit) => hit.selector);
        expect(selectors).not.toContain('raa_firq');
        expect(selectors).not.toContain('yabsut');
    });
});

describe('buildProfile', () => {
    const groups = buildProfile([...shardChoices(SHARD_10), ...shardChoices(SHARD_OTHERS)]);

    it('groups in catalogue order and splits istifham per word in mushaf order', () => {
        expect(groups.map((group) => group.group)).toEqual(['hamza', 'joining', 'sakt']);
        const words = groups[0]!.families[0]!.words;
        expect(words.map((word) => word.texts[0])).toEqual(['ءَآلذَّكَرَيْنِ', 'ءَآلْـَٔـٰنَ', 'ءَآللَّهُ']);
    });

    it('folds selectors of one family into one block of word rows', () => {
        const extra = buildProfile([
            { selector: 'bastah', option: 'saad', wordRefs: ['7:69:22'], texts: ['بَصْۜطَةً'] },
            { selector: 'almusaytirun', option: 'saad', wordRefs: ['52:37:7'], texts: ['ٱلْمُصَۣيْطِرُونَ'] },
        ]);
        expect(extra).toHaveLength(1);
        expect(extra[0]!.families.map((f) => [f.family, f.words.length])).toEqual([['seen_saad', 2]]);
        expect(extra[0]!.families[0]!.words[1]!.options.map((o) => o.option)).toEqual(['seen', 'saad']);
    });

    it('lists every option with the verses read that way, both where occurrences differ', () => {
        const allah = groups[0]!.families[0]!.words[2]!;
        expect(allah.options).toEqual([
            { option: 'ibdal', verses: [{ surah: 27, ayah: 59, label: '27:59' }] },
            { option: 'tashil', verses: [{ surah: 10, ayah: 59, label: '10:59' }] },
        ]);
        const alaan = groups[0]!.families[0]!.words[1]!;
        expect(alaan.options.map((o) => [o.option, o.verses.map((v) => v.label)])).toEqual([
            ['ibdal', []], ['tashil', ['10:51', '10:91']],
        ]);
    });

    it('labels a cross-verse occurrence by its verse span and dedupes repeats', () => {
        const sakt = groups[2]!.families[0]!.words[0]!;
        expect(sakt.texts).toEqual(['عِوَجَاۜ', 'قَيِّمًا']);
        expect(sakt.options[0]!.verses).toEqual([{ surah: 18, ayah: 1, label: '18:1–2' }]);
        const irkab = groups[1]!.families[0]!.words[0]!;
        expect(irkab.options[0]!.verses).toHaveLength(1);
    });
});
