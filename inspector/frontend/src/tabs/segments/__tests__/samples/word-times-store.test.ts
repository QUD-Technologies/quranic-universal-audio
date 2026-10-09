import { get } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
    clearWordTimes,
    ensureWordTimes,
    storedWordTimes,
    wordTimes,
} from '../../stores/word-times';

const WORDS = [{ location: '112:1:1', start_ms: 1000, end_ms: 1400 }];

function serve(segments: Record<string, typeof WORDS>) {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ segments })));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
}

async function settle() {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('stored word times', () => {
    afterEach(() => {
        clearWordTimes();
        vi.unstubAllGlobals();
        vi.useRealTimers();
    });

    it('loads a chapter once however many cards ask', async () => {
        const fetchMock = serve({ a: WORDS });
        ensureWordTimes('r', 112);
        ensureWordTimes('r', '112');
        await settle();
        ensureWordTimes('r', 112);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/seg/word-times/r/112');
        expect(storedWordTimes(112, 'a')).toEqual(WORDS);
        expect(storedWordTimes(112, 'missing')).toEqual([]);
    });

    it('refetches a chapter on request only after the throttle', async () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        const fetchMock = serve({});
        ensureWordTimes('r', 1);
        await settle();
        ensureWordTimes('r', 1, true);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        vi.setSystemTime(Date.now() + 16_000);
        ensureWordTimes('r', 1, true);
        await settle();
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('drops every chapter on a reciter switch', async () => {
        serve({ a: WORDS });
        ensureWordTimes('r', 112);
        await settle();
        ensureWordTimes('other', 2);
        expect(get(wordTimes)['112']).toBeUndefined();
    });
});
