import { describe, expect, it } from 'vitest';

import { V15_CATALOGUE, V15_VARIANT } from '../../../../lib/recitation-data/test-v15-fixture';
import type { TsReadingVariant } from '../../../../lib/types/generated/schemas';
import {
    type VariantCatalogue,
    variantControlsFor,
    variantNote,
    variantTipLines,
} from '../variant-controls';

const SAKT: TsReadingVariant = {
    id: 'iwaja_qayyima', chosen: 'idraj', words: [0, 1], targets: [0, 1],
    anchor: 'boundary', boundary: 1, by: 'scored', score: 4.0,
    affected: { sakt: { c: [5, 6], s: [5, 6], b: [1] } },
};

const CATALOGUE: VariantCatalogue = {
    ...V15_CATALOGUE,
    iwaja_qayyima: { name: 'Iwaja', description: null, options: ['sakt', 'idraj'], default: 'sakt' },
};

describe('variantControlsFor', () => {
    it('maps a word variant onto read-only renderer controls', () => {
        const controls = variantControlsFor(CATALOGUE, { variants: [V15_VARIANT] })!;

        expect(controls.readOnly).toBe(true);
        expect(controls.onselect).toBeUndefined();
        expect(controls.definitions.istifham_article).toEqual({
            id: 'istifham_article',
            display_name: 'Istifham article',
            options: ['ibdal', 'tashil'],
            default: 'ibdal',
            description: 'Hamzat al-wasl after the question hamza.',
        });
        expect(controls.occurrences).toEqual([{
            variant_id: 'istifham_article',
            selected: 'tashil',
            word_ids: [1],
            target_word_ids: [1],
            anchor: 'word',
            anchor_word_id: 1,
            anchor_boundary_id: null,
            active: true,
            masked: false,
            affected: { ibdal: { column_ids: [101], sound_ids: [1], boundary_ids: [] } },
            note: 'long vowel 0.31 × lāzim p5',
        }]);
    });

    it('anchors a boundary variant on its native boundary id', () => {
        const [occurrence] = variantControlsFor(CATALOGUE, { variants: [SAKT] })!.occurrences;

        expect(occurrence).toMatchObject({
            anchor: 'boundary',
            anchor_word_id: 0,
            anchor_boundary_id: 1,
            affected: { sakt: { column_ids: [5, 6], sound_ids: [5, 6], boundary_ids: [1] } },
            note: 'acoustic margin 4.0',
        });
        expect(variantControlsFor(CATALOGUE, { variants: [SAKT] })!.definitions.iwaja_qayyima)
            .not.toHaveProperty('description');
    });

    it('masks a pick made without evidence', () => {
        const [occurrence] = variantControlsFor(CATALOGUE, {
            variants: [{ ...V15_VARIANT, chosen: 'ibdal', by: 'default', score: null,
                affected: { tashil: { c: [], s: [], b: [] } } }],
        })!.occurrences;

        expect(occurrence?.masked).toBe(true);
        expect(occurrence?.note).toBe('no evidence, default shown');
    });

    it('is undefined for a reading without variants or definitions', () => {
        expect(variantControlsFor(CATALOGUE, {})).toBeUndefined();
        expect(variantControlsFor(CATALOGUE, { variants: [] })).toBeUndefined();
        expect(variantControlsFor(undefined, { variants: [V15_VARIANT] })).toBeUndefined();
        expect(variantControlsFor({}, { variants: [V15_VARIANT] })).toBeUndefined();
    });
});

describe('variant notes and tips', () => {
    it.each([
        [{ by: 'scored', score: 4.04 }, 'acoustic margin 4.0'],
        [{ by: 'tie', score: 0 }, 'equal scores, default kept'],
        [{ by: 'length', score: 0.312 }, 'long vowel 0.31 × lāzim p5'],
        [{ by: 'majority', score: 1.2 }, "majority of this recording's places"],
        [{ by: 'pause', score: 240 }, 'pause 240 ms'],
        [{ by: 'default', score: null }, 'no evidence, default shown'],
    ] as const)('notes %o', (variant, note) => {
        expect(variantNote(variant)).toBe(note);
    });

    it('names the face and whose reading it is', () => {
        const controls = variantControlsFor(CATALOGUE, { variants: [V15_VARIANT] })!;
        const definition = controls.definitions.istifham_article!;
        const occurrence = controls.occurrences[0]!;

        expect(variantTipLines(definition, occurrence, 'tashil')).toEqual([
            'Istifham article: Tashil',
            'This recitation',
            'Hamzat al-wasl after the question hamza.',
            'long vowel 0.31 × lāzim p5',
        ]);
        expect(variantTipLines(definition, occurrence, 'ibdal')).toEqual([
            'Istifham article: Ibdal',
            'Other reading',
            'Hamzat al-wasl after the question hamza.',
        ]);
    });
});
