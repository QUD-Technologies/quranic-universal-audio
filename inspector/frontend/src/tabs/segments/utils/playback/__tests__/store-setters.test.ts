/**
 * The playback rAF calls `setPlayingSegment` / `setActiveWordCursor` every
 * frame. Svelte stores notify on every object write, so an unchanged value must
 * not be written — otherwise every row re-renders at 60fps.
 */
import { afterEach, describe, expect, it } from 'vitest';

import {
    activeWordCursor,
    playingSegmentIndex,
    setActiveWordCursor,
    setPlayingSegment,
} from '../../../stores/playback';

function notifications(store: { subscribe: (run: () => void) => () => void }, act: () => void): number {
    let calls = 0;
    const stop = store.subscribe(() => { calls += 1; });
    act();
    stop();
    return calls - 1; // the subscribe call itself
}

afterEach(() => {
    playingSegmentIndex.set(null);
    activeWordCursor.set(null);
});

describe('playback store setters', () => {
    it('an unchanged playing pair does not notify', () => {
        setPlayingSegment({ chapter: 2, index: 1, origin: 'main' });
        const count = notifications(playingSegmentIndex, () => {
            for (let i = 0; i < 5; i++) setPlayingSegment({ chapter: 2, index: 1 });
            setPlayingSegment({ chapter: 2, index: 2 });
            setPlayingSegment(null);
            setPlayingSegment(null);
        });
        expect(count).toBe(2);
    });

    it('keeps the origin when the next pair omits it', () => {
        setPlayingSegment({ chapter: 2, index: 1, origin: 'accordion' });
        setPlayingSegment({ chapter: 2, index: 2 });
        let pair: unknown;
        playingSegmentIndex.subscribe((value) => { pair = value; })();
        expect(pair).toEqual({ chapter: 2, index: 2, origin: 'accordion' });
    });

    it('an unchanged word cursor does not notify', () => {
        setActiveWordCursor({ chapter: 2, index: 1, wordIndex: 0 });
        const count = notifications(activeWordCursor, () => {
            for (let i = 0; i < 5; i++) setActiveWordCursor({ chapter: 2, index: 1, wordIndex: 0 });
            setActiveWordCursor({ chapter: 2, index: 1, wordIndex: 1 });
            setActiveWordCursor(null);
            setActiveWordCursor(null);
        });
        expect(count).toBe(2);
    });
});
