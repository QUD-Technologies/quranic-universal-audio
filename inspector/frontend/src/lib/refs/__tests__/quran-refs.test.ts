import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { _resetQuranRefs, loadQuranRefs, quranRefs, type QuranRefs } from '../quran-refs';

function bundle(riwayah: string): QuranRefs {
    return {
        riwayah,
        dk_words: { '1:1:1': `${riwayah}-word` },
        verse_word_counts: { '1:1': 1 },
        verse_marker_prefix: riwayah === 'hafs' ? '۝' : '',
        special_texts: {},
    };
}

/** Serves a distinct bundle + version per `?riwayah=`, recording every URL. */
function stubFetch(urls: string[], deferred?: Map<string, () => void>) {
    return vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        urls.push(url);
        const riwayah = new URL(url, 'http://x').searchParams.get('riwayah') ?? 'hafs_an_asim';
        const sdk = riwayah.split('_')[0]!;
        const body = url.includes('/version')
            ? { version: `v-${sdk}` }
            : bundle(sdk === 'qalon' ? 'qalun' : sdk);
        if (deferred?.has(url)) {
            await new Promise<void>((resolve) => deferred.set(url, resolve));
        }
        return new Response(JSON.stringify(body), {
            status: 200,
            headers: { 'content-type': 'application/json' },
        });
    });
}

describe('loadQuranRefs', () => {
    let urls: string[];

    beforeEach(() => {
        urls = [];
        _resetQuranRefs();
        sessionStorage.clear();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('defaults to Hafs and asks for no riwayah-specific bundle', async () => {
        stubFetch(urls);
        await loadQuranRefs();
        expect(get(quranRefs)?.riwayah).toBe('hafs');
        expect(urls.every((u) => u.includes('riwayah=hafs_an_asim'))).toBe(true);
    });

    it('fetches the requested edition and carries its verse-marker prefix', async () => {
        stubFetch(urls);
        await loadQuranRefs('warsh_an_nafi');
        expect(get(quranRefs)).toEqual(bundle('warsh'));
        expect(get(quranRefs)?.verse_marker_prefix).toBe('');
    });

    it('clears the store before loading a different edition', async () => {
        stubFetch(urls);
        await loadQuranRefs('warsh_an_nafi');
        const seen: (string | undefined)[] = [];
        const unsub = quranRefs.subscribe((r) => seen.push(r?.riwayah));
        const pending = loadQuranRefs('qalon_an_nafi');
        // Serving Warsh coordinates while Qalun loads would render segments
        // against the wrong verse geometry.
        expect(get(quranRefs)).toBeNull();
        await pending;
        unsub();
        expect(seen).toEqual(['warsh', undefined, 'qalun']);
    });

    it('shares one in-flight request for repeated calls on the same edition', async () => {
        stubFetch(urls);
        await Promise.all([loadQuranRefs('warsh_an_nafi'), loadQuranRefs('warsh_an_nafi')]);
        expect(urls.filter((u) => u.includes('/version')).length).toBe(1);
    });

    it('serves a repeat load of the same edition from cache', async () => {
        stubFetch(urls);
        await loadQuranRefs('warsh_an_nafi');
        _resetQuranRefs();
        await loadQuranRefs('warsh_an_nafi');
        // Only the tiny version probe repeats; the ~3 MB body comes from cache.
        expect(urls.filter((u) => u.includes('quran-refs.json')).length).toBe(1);
        expect(get(quranRefs)?.riwayah).toBe('warsh');
    });

    it('refetches after a switch away, because only one bundle is retained', async () => {
        stubFetch(urls);
        await loadQuranRefs('warsh_an_nafi');
        await loadQuranRefs('qalon_an_nafi');
        await loadQuranRefs('warsh_an_nafi');
        // A bundle is ~3 MB and sessionStorage quota is 5-10 MB, so keeping two
        // would evict unpredictably mid-session. The tab shows one delivery at
        // a time, so paying one refetch on a cross-edition switch is the trade.
        expect(urls.filter((u) => u.includes('quran-refs.json')).length).toBe(3);
        expect(get(quranRefs)?.riwayah).toBe('warsh');
    });

    it('keeps only one cached bundle so a long session cannot accumulate them', async () => {
        stubFetch(urls);
        await loadQuranRefs('warsh_an_nafi');
        await loadQuranRefs('qalon_an_nafi');
        const keys = Object.keys(sessionStorage).filter((k) => k.startsWith('quran-refs:'));
        expect(keys).toEqual(['quran-refs:qalon_an_nafi:v-qalon']);
    });

    it('busts the cache on the edition, not just the hash', async () => {
        stubFetch(urls);
        await loadQuranRefs('warsh_an_nafi');
        // Two editions that happened to share a hash must still not collide.
        expect(sessionStorage.getItem('quran-refs:warsh_an_nafi:v-warsh')).toContain('warsh-word');
        expect(sessionStorage.getItem('quran-refs:qalon_an_nafi:v-warsh')).toBeNull();
    });

    it('leaves the store null when the fetch fails', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => {});
        vi.spyOn(window, 'fetch').mockRejectedValue(new Error('offline'));
        await loadQuranRefs('warsh_an_nafi');
        expect(get(quranRefs)).toBeNull();
    });
});
