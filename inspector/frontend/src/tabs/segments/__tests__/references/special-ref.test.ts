/**
 * Edit Ref accepts a special's name (Basmala, Isti'adha …) as typed by a
 * reviewer and stores it canonically spelled.
 */

import { describe, expect, it } from 'vitest';

import type { Segment } from '../../../../lib/types/view-models';
import { isSpecialRef, specialRefName } from '../../utils/data/references';
import { commitRefEdit } from '../../utils/edit/reference';

describe('specialRefName', () => {
    it('reads loose spellings as the canonical name', () => {
        expect(['Basmala', 'bismillah', 'Istiatha', "isti'adha", 'ISTI-ADHA', 'ameen'].map(specialRefName))
            .toEqual(['Basmala', 'Basmala', "Isti'adha", "Isti'adha", "Isti'adha", 'Amin']);
    });

    it('leaves verse refs and unknown words alone', () => {
        expect(['1:1', '2:255:1-2:255:3', 'bas', ''].map(specialRefName)).toEqual([null, null, null, null]);
    });

    it('recognises only the stored spelling as a special ref', () => {
        expect([isSpecialRef("Isti'adha"), isSpecialRef('istiatha'), isSpecialRef('1:1:1-1:1:4')])
            .toEqual([true, false, false]);
    });
});

describe('commitRefEdit with a special', () => {
    it('stores the canonical name and no verse text', async () => {
        const seg = {
            chapter: 2, index: 0, time_start: 0, time_end: 4000,
            matched_ref: '2:1:1-2:1:1', matched_text: 'الٓمٓ', confidence: 0.4,
        } as Segment;
        expect(await commitRefEdit(seg, 'istiatha')).toEqual({ status: 'ok' });
        expect([seg.matched_ref, seg.matched_text, seg.confidence]).toEqual(["Isti'adha", '', 1]);
    });
});
