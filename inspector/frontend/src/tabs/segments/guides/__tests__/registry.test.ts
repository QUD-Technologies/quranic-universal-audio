/**
 * Read-tracking helpers on the guide registry: the alias collapse + the
 * unread / all-read predicates that drive the cyan border and the edit gate.
 */

import { describe, expect, it } from 'vitest';

import {
    allGuidesRead,
    guideViewKey,
    hasAccordionGuide,
    isGuideRead,
    ALL_GUIDE_KEYS,
    REQUIRED_GUIDE_KEYS,
} from '../registry';
import { OWNER_ONLY_CATEGORIES } from '../../domain/registry';

describe('guideViewKey', () => {
    it('collapses the low_confidence_v2 alias to low_confidence', () => {
        expect(guideViewKey('low_confidence_v2')).toBe('low_confidence');
    });

    it('is identity for every other category', () => {
        expect(guideViewKey('failed')).toBe('failed');
        expect(guideViewKey('qalqala')).toBe('qalqala');
    });
});

describe('REQUIRED_GUIDE_KEYS', () => {
    it('excludes owner-only categories but ALL_GUIDE_KEYS keeps them', () => {
        for (const cat of OWNER_ONLY_CATEGORIES) {
            expect(REQUIRED_GUIDE_KEYS).not.toContain(cat);
        }
        expect(ALL_GUIDE_KEYS).toContain('qalqala');
        expect(REQUIRED_GUIDE_KEYS).not.toContain('qalqala');
    });

    it('never contains the low_confidence_v2 alias', () => {
        expect(REQUIRED_GUIDE_KEYS).not.toContain('low_confidence_v2');
        expect(REQUIRED_GUIDE_KEYS).toContain('low_confidence');
    });

    it('only lists keys that have a registered guide', () => {
        for (const key of REQUIRED_GUIDE_KEYS) {
            expect(hasAccordionGuide(key)).toBe(true);
        }
    });
});

describe('isGuideRead', () => {
    it('treats low_confidence and low_confidence_v2 as one reading', () => {
        const read = ['low_confidence'];
        expect(isGuideRead(read, 'low_confidence')).toBe(true);
        expect(isGuideRead(read, 'low_confidence_v2')).toBe(true);
    });

    it('is false for an unopened guide', () => {
        expect(isGuideRead(['failed'], 'qalqala')).toBe(false);
    });
});

describe('allGuidesRead', () => {
    it('is false until every required guide is read', () => {
        expect(allGuidesRead([])).toBe(false);
        expect(allGuidesRead(REQUIRED_GUIDE_KEYS.slice(0, -1))).toBe(false);
    });

    it('is true once the full required set is present', () => {
        expect(allGuidesRead([...REQUIRED_GUIDE_KEYS])).toBe(true);
    });

    it('ignores extra/unknown keys beyond the required set', () => {
        expect(allGuidesRead([...REQUIRED_GUIDE_KEYS, 'audio_bleeding'])).toBe(true);
    });
});
