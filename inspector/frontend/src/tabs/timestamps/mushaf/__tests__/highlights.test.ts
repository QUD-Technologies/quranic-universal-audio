import { describe, expect, it } from 'vitest';

import { readingHighlights, type ReadingState } from '../highlights';

// A toy Quran: every surah has 3 ayahs except 114 (6).
const ayahs = (s: number): number => (s === 114 ? 6 : 3);
const all = new Set(Array.from({ length: 114 }, (_, i) => i + 1));

function state(over: Partial<ReadingState>): ReadingState {
    return {
        pos: { active: '2:2:3', verseKey: '2:2', progress: '2:2:3', anchor: '2:2:3' },
        firstAnchor: '2:1:1',
        scope: 'spread',
        showUpcoming: true,
        spot: null,
        playable: all,
        ayahs,
        ...over,
    };
}

const byId = (st: ReadingState) => Object.fromEntries(readingHighlights(st).map((h) => [h.id, h]));

describe('readingHighlights', () => {
    it('bands the whole verse and fades the words not reached yet; the playhead draws the recited word', () => {
        const h = byId(state({}));
        expect(h.verse).toMatchObject({ target: '2:2', style: 'band' });
        expect(h.upcoming).toMatchObject({ target: { from: '2:2:4', to: '114:6' }, style: 'upcoming' });
        expect(Object.keys(h)).toEqual(['upcoming', 'verse']);
    });

    it('hides every other verse in verse scope', () => {
        const h = byId(state({ scope: 'verse' }));
        expect(h['hidden-0']!.target).toEqual({ from: '1:1', to: '2:1' });
        expect(h['hidden-1']!.target).toEqual({ from: '2:3', to: '114:6' });
    });

    it('crosses surahs at a first or last ayah', () => {
        const h = byId(state({ scope: 'verse', pos: { active: '', verseKey: '3:1', progress: '3:1:1', anchor: '3:1:1' } }));
        expect(h['hidden-0']!.target).toEqual({ from: '1:1', to: '2:3' });
        const last = byId(state({ scope: 'verse', pos: { active: '', verseKey: '3:3', progress: '3:3:1', anchor: '3:3:1' } }));
        expect(last['hidden-1']!.target).toEqual({ from: '4:1', to: '114:6' });
    });

    it('hides what is not reached yet and bands only the reached part of the verse', () => {
        const h = byId(state({ showUpcoming: false }));
        expect(h['hidden-0']!.target).toEqual({ from: '2:2:4', to: '114:6' });
        expect(h.verse!.target).toEqual({ from: '2:2', to: '2:2:3' });
    });

    it('hides from the first recited word before anything is reached, with no band', () => {
        const h = byId(state({ showUpcoming: false, pos: { active: '', verseKey: '2:1', progress: '', anchor: '2:1:1' } }));
        expect(h['hidden-0']!.target).toEqual({ from: '2:1:1', to: '114:6' });
        expect(h.verse).toBeUndefined();
    });

    it('bands and isolates nothing during an opener reading, which is not printed', () => {
        const opener = { active: '', verseKey: '0:1', progress: '', anchor: '2:1:1' };
        const shown = byId(state({ scope: 'verse', pos: opener }));
        expect(Object.keys(shown)).toEqual(['upcoming']);
        expect(shown.upcoming!.target).toEqual({ from: '2:1:1', to: '114:6' });
        const h = byId(state({ scope: 'verse', showUpcoming: false, pos: opener }));
        expect(Object.keys(h)).toEqual(['hidden-0']);
        expect(h['hidden-0']!.target).toEqual({ from: '2:1:1', to: '114:6' });
    });

    it('shows everything and lights the spotlight while picking', () => {
        const h = byId(state({ scope: 'verse', showUpcoming: false, spot: [{ surah: 2, ayah: 3 }, { surah: 2, ayah: 1 }] }));
        expect(Object.keys(h).filter((k) => k.startsWith('hidden'))).toEqual([]);
        expect(h.pick).toMatchObject({ target: { from: '2:3', to: '2:1' }, style: 'pick' });
    });

    it('marks runs of surahs the reciter lacks inert', () => {
        const playable = new Set([...all].filter((s) => s < 5 || s > 7));
        const h = byId(state({ playable, pos: null }));
        expect(h['inert-0']).toMatchObject({ target: { from: '5:1', to: '7:3' }, style: 'inert' });
        expect(Object.keys(h)).toEqual(['inert-0']);
    });
});
