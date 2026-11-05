import { gzipSync } from 'node:zlib';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchArrayBuffer, fetchJson } from '../../api';
import { _resetForTests, loadDk } from '../ts-source';

vi.mock('../../api', () => ({
    ApiError: class extends Error {},
    fetchArrayBuffer: vi.fn(),
    fetchJson: vi.fn(),
}));

const BODIES: Record<string, unknown> = {
    '/manifest': {
        resources: {
            digital_khatt: '/api/ts/resource/digital_khatt',
            'digital_khatt_madani-1405': '/api/ts/resource/digital_khatt_madani-1405',
        },
    },
    '/api/ts/resource/digital_khatt': { '1:1:1': { text: 'default' } },
    '/api/ts/resource/digital_khatt_madani-1405': { '1:1:1': { text: '1405' } },
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

    it('serves a layout with its own stop edition from its own resource', async () => {
        expect((await loadDk('madani-1405'))['1:1:1']?.text).toBe('1405');
    });

    it('serves every other layout, and no layout, the default script once', async () => {
        const plain = await loadDk();
        expect(plain['1:1:1']?.text).toBe('default');
        expect(await loadDk('madani-1421')).toBe(plain);
        expect(await loadDk('madani-1441')).toBe(plain);
        const scriptFetches = vi.mocked(fetchArrayBuffer).mock.calls
            .filter(([url]) => url.startsWith('/api/ts/resource/'));
        expect(scriptFetches).toEqual([['/api/ts/resource/digital_khatt']]);
    });
});
