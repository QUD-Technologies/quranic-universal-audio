import { describe, expect, it } from 'vitest';

import { QUALITY_ORDER, QUALITY_UNRATED, qualityTag, qualityTagLabel } from '../quality-status';

describe('quality-status', () => {
    it('orders best to worst', () => {
        expect(QUALITY_ORDER).toEqual(['clean', 'almost_clean', 'needs_work']);
    });

    it('tags an unrated delivery as unrated', () => {
        expect(qualityTag(null)).toBe(QUALITY_UNRATED);
        expect(qualityTag(undefined)).toBe(QUALITY_UNRATED);
        expect(qualityTag('almost_clean')).toBe('almost_clean');
    });

    it('labels every rail tag', () => {
        expect(qualityTagLabel('clean')).toBe('Clean');
        expect(qualityTagLabel('needs_work')).toBe('Needs work');
        expect(qualityTagLabel(QUALITY_UNRATED)).toBe('Unrated');
    });
});
