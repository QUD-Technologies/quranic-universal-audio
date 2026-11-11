import { get } from 'svelte/store';
import { afterEach, describe, expect, it } from 'vitest';

import type { SegValidateResponse, SegValLowConfidenceV2Item } from '../../../../lib/types/generated/schemas';
import { lowConfidenceWords, segValidation } from '../../stores/validation';
import { dkTextForRef, wordLocationsForRef } from '../../utils/data/references';
import {
    lowConfidenceAlpha,
    lowConfidenceWordMarks,
    MIN_HIGHLIGHT_ALPHA,
    UNSCORED_INTENSITY,
} from '../../utils/validation/low-confidence-words';

function item(uid: string, words: SegValLowConfidenceV2Item['words']): SegValLowConfidenceV2Item {
    return { ref: '2:5:1-2:5:6', chapter: 2, seg_index: 0, segment_uid: uid, words };
}

describe('lowConfidenceWordMarks', () => {
    it('takes intensity from the server severity, not from the items it is given', () => {
        const marks = lowConfidenceWordMarks([
            item('a', [{ ref: '2:5:3', fit: -4, severity: 0.2 }, { ref: '2:5:4', fit: -6, severity: 0.7 }]),
        ]);
        expect(marks.get('a')?.get('2:5:3')).toEqual({ intensity: 0.2, fit: -4, soundFit: null });
        expect(marks.get('a')?.get('2:5:4')?.intensity).toBe(0.7);
    });

    it('gives unscored words the middle intensity and skips items without words', () => {
        const marks = lowConfidenceWordMarks([
            item('a', [{ ref: '2:5:3' }]),
            item('b', []),
            { ...item('c', [{ ref: '1:1:1', fit: -5, severity: 1 }]), segment_uid: null },
        ]);
        expect(marks.get('a')?.get('2:5:3')).toEqual({ intensity: UNSCORED_INTENSITY, fit: null, soundFit: null });
        expect(marks.has('b')).toBe(false);
        expect(marks.size).toBe(1);
    });
});

describe('lowConfidenceAlpha', () => {
    it('floors the mildest word and clamps out-of-range intensity', () => {
        expect(lowConfidenceAlpha(0)).toBeCloseTo(MIN_HIGHLIGHT_ALPHA);
        expect(lowConfidenceAlpha(1)).toBe(1);
        expect(lowConfidenceAlpha(2)).toBe(1);
    });
});

describe('lowConfidenceWords store', () => {
    afterEach(() => segValidation.set(null));

    it('follows the validate payload', () => {
        expect(get(lowConfidenceWords).size).toBe(0);
        segValidation.set({ low_confidence_v2: [item('a', [{ ref: '2:5:3', fit: -6, severity: 1 }])] } as SegValidateResponse);
        expect([...get(lowConfidenceWords).get('a')!.keys()]).toEqual(['2:5:3']);
    });
});

describe('wordLocationsForRef', () => {
    const vwc = { '1:1': 4, '1:2': 4 };

    it('walks across verse ends', () => {
        expect(wordLocationsForRef('1:1:3-1:2:2', vwc)).toEqual(['1:1:3', '1:1:4', '1:2:1', '1:2:2']);
    });

    it('is empty for malformed refs', () => {
        expect(wordLocationsForRef('1:1', vwc)).toEqual([]);
        expect(wordLocationsForRef(null, vwc)).toEqual([]);
    });

    it('feeds dkTextForRef, skipping words the edition lacks', () => {
        expect(dkTextForRef('1:1:3-1:2:1', { '1:1:3': 'a', '1:2:1': 'c' }, vwc)).toBe('a c');
    });
});
