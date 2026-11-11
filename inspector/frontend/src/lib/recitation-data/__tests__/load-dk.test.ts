import { gzipSync } from 'node:zlib';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchArrayBuffer, fetchJson } from '../../api';
import { _resetForTests, loadDk, loadVerseWordCounts } from '../ts-source';

vi.mock('../../api', () => ({
    ApiError: class extends Error {},
    fetchArrayBuffer: vi.fn(),
    fetchJson: vi.fn(),
}));

const DK = {
    '1:1:1': { text: 'بِسْمِ' },
    '1:1:2': { text: 'ٱللَّهِ' },
    '1:1:3': { text: '۝١' },
    '1:2:1': { text: 'ٱلْحَمْدُ' },
    '1:2:2': { text: '۝٢' },
    '2:1:1': { text: 'الٓمٓ' },
};

const BODIES: Record<string, unknown> = {
    '/manifest': { resources: { digital_khatt: '/api/ts/resource/digital_khatt' } },
    '/api/ts/resource/digital_khatt': DK,
};

function gz(body: unknown): ArrayBuffer {
    const buf = gzipSync(Buffer.from(JSON.stringify(body)));
    return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
}

describe('loadDk', () => {
    beforeEach(() => {
        _resetForTests();
        vi.mocked(fetchJson).mockResolvedValue({ manifest_url: '/manifest' });
        vi.mocked(fetchArrayBuffer).mockImplementation(async (url: string) => gz(BODIES[url]));
    });
    afterEach(() => vi.clearAllMocks());

    it('fetches the Digital Khatt script once', async () => {
        const dk = await loadDk();
        expect(dk['1:1:1']?.text).toBe('بِسْمِ');
        expect(await loadDk()).toBe(dk);
        const scriptFetches = vi.mocked(fetchArrayBuffer).mock.calls
            .filter(([url]) => url.startsWith('/api/ts/resource/'));
        expect(scriptFetches).toEqual([['/api/ts/resource/digital_khatt']]);
    });

    it('counts each verse\'s words, not its ayah-end marker', async () => {
        const counts = await loadVerseWordCounts();
        expect([...counts.get(1)!]).toEqual([[1, 2], [2, 1]]);
        expect(counts.get(2)?.get(1)).toBe(1);
    });
});
