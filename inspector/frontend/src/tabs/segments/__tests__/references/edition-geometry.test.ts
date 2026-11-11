import { get } from 'svelte/store';
import { afterEach, describe, expect, it } from 'vitest';

import { _resetQuranRefs, quranRefs } from '../../../../lib/refs/quran-refs';
import {
    _advanceRefByOneWord,
    _clampRefWordOvershoot,
    _validateRefStructural,
    formatRef,
    verseMarkerPrefix,
} from '../../utils/data/references';

/**
 * Verse geometry is edition-specific, so every helper that walks or bounds a
 * ref has to read it from the loaded bundle rather than assume Hafs.
 *
 * 72:16 is the sharpest case in the corpus: Hafs has 7 words there, Warsh and
 * Qalun 8 (the projection splits Hafs's `72:16:1` in two). Under Hafs counts a
 * Warsh reviewer could not address the eighth word at all.
 */
const HAFS = { '72:16': 7, '72:17': 9 };
const WARSH = { '72:16': 8, '72:17': 9 };

function load(riwayah: string, counts: Record<string, number>) {
    // `quranRefs` is the store every helper reads; set it directly rather than
    // stubbing fetch, which `quran-refs.test.ts` already covers.
    _resetQuranRefs();
    const store = quranRefs as unknown as { set: (v: unknown) => void };
    store.set({
        riwayah,
        dk_words: {},
        verse_word_counts: counts,
        verse_marker_prefix: riwayah === 'hafs' ? '۝' : '',
        special_texts: {},
    });
}

describe('reference helpers under a non-Hafs edition', () => {
    afterEach(_resetQuranRefs);

    it('walks to the eighth word of a verse Hafs ends at seven', () => {
        expect(_advanceRefByOneWord({ surah: 72, ayah: 16, word: 7 }, WARSH))
            .toEqual({ surah: 72, ayah: 16, word: 8 });
        // Under Hafs counts the same step leaves the verse entirely.
        expect(_advanceRefByOneWord({ surah: 72, ayah: 16, word: 7 }, HAFS))
            .toEqual({ surah: 72, ayah: 17, word: 1 });
    });

    it('accepts a ref that only exists in the wider edition', () => {
        expect(_validateRefStructural('72:16:8-72:16:8', WARSH).ok).toBe(true);
        const hafs = _validateRefStructural('72:16:8-72:16:8', HAFS);
        expect(hafs.ok).toBe(false);
        expect(hafs.ok === false && hafs.reason).toBe('word_overshoot');
    });

    it('clamps an overshoot to the editioned verse length, not the Hafs one', () => {
        const overshoot = { surah: 72, ayah_from: 16, word_from: 1, ayah_to: 16, word_to: 9 };
        expect(_clampRefWordOvershoot(overshoot, WARSH)?.clampedRef).toBe('72:16:1-72:16:8');
        expect(_clampRefWordOvershoot(overshoot, HAFS)?.clampedRef).toBe('72:16:1-72:16:7');

        // The eighth word is a legitimate ref in Warsh and an overshoot in
        // Hafs — the whole reason the counts cannot be shared.
        const eighth = { surah: 72, ayah_from: 16, word_from: 1, ayah_to: 16, word_to: 8 };
        expect(_clampRefWordOvershoot(eighth, WARSH)?.clamped).toBe(false);
        expect(_clampRefWordOvershoot(eighth, HAFS)?.clamped).toBe(true);
    });

    it('collapses a whole-verse ref at the edition own length', () => {
        expect(formatRef('72:16:1-72:16:8', WARSH)).toBe('72:16');
        // Not a whole verse in Hafs — the 8th word is past the end.
        expect(formatRef('72:16:1-72:16:8', HAFS)).toBe('72:16:1-72:16:8');
    });

    it('drops the U+06DD ornament for the QPC-font editions', () => {
        // Those fonts decorate the digits themselves; sending both renders two
        // nested ornaments.
        load('warsh', WARSH);
        expect(verseMarkerPrefix()).toBe('');
        load('hafs', HAFS);
        expect(verseMarkerPrefix()).toBe('۝');
    });

    it('keeps the Hafs ornament before any bundle has loaded', () => {
        // Every pre-multi-riwayah render did this; showing no marker at all
        // reads as a rendering bug rather than as "still loading".
        expect(get(quranRefs)).toBeNull();
        expect(verseMarkerPrefix()).toBe('۝');
    });
});
