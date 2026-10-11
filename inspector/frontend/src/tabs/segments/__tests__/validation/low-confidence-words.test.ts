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
    wordSeverity,
} from '../../utils/validation/low-confidence-words';

function item(uid: string, words: SegValLowConfidenceV2Item['words']): SegValLowConfidenceV2Item {
    return { ref: '2:5:1-2:5:6', chapter: 2, seg_index: 0, segment_uid: uid, words };
}

describe('wordSeverity', () => {
    it('takes the worse of the word and sound deficits, each over its floor', () => {
        expect(wordSeverity({ ref: '2:5:3', fit: -6, sound_fit: -9 })).toBeCloseTo(1.5);
        expect(wordSeverity({ ref: '2:5:4', fit: -0.2, sound_fit: -11 })).toBeCloseTo(1.1);
    });

    it('ignores a missing score and is null without any', () => {
        expect(wordSeverity({ ref: '2:5:3', fit: -8 })).toBeCloseTo(2);
        expect(wordSeverity({ ref: '2:5:3', sound_fit: -20, fit: null })).toBeCloseTo(2);
        expect(wordSeverity({ ref: '2:5:3' })).toBeNull();
    });
});

describe('lowConfidenceWordMarks', () => {
    it('scales intensity between the reciter-wide least and most severe words', () => {
        const marks = lowConfidenceWordMarks([
            item('a', [{ ref: '2:5:3', fit: -4 }, { ref: '2:5:4', fit: -6 }]),
            item('b', [{ ref: '3:1:1', fit: -8, sound_fit: -12 }]),
        ]);
        expect(marks.get('a')?.get('2:5:3')?.intensity).toBeCloseTo(0);
        expect(marks.get('a')?.get('2:5:4')?.intensity).toBeCloseTo(0.5);
        expect(marks.get('b')?.get('3:1:1')).toEqual({ intensity: 1, fit: -8, soundFit: -12 });
    });

    it('gives unscored words the middle intensity and skips items without words', () => {
        const marks = lowConfidenceWordMarks([
            item('a', [{ ref: '2:5:3' }]),
            item('b', []),
            { ...item('c', [{ ref: '1:1:1', fit: -5 }]), segment_uid: null },
        ]);
        expect(marks.get('a')?.get('2:5:3')).toEqual({ intensity: UNSCORED_INTENSITY, fit: null, soundFit: null });
        expect(marks.has('b')).toBe(false);
        expect(marks.size).toBe(1);
    });

    it('marks a lone scored word at full intensity', () => {
        const marks = lowConfidenceWordMarks([item('a', [{ ref: '2:5:3', fit: -5 }])]);
        expect(marks.get('a')?.get('2:5:3')?.intensity).toBe(1);
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
        segValidation.set({ low_confidence_v2: [item('a', [{ ref: '2:5:3', fit: -6 }])] } as SegValidateResponse);
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
