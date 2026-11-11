import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { _resetAccessTokenForTests, fetchWithAccess } from '../ts-access-token';

const SHARD = '/api/ts/shard/r/1';
const HEADER = 'X-QUA-Access';

function tokenResponse(token: string, expiresInS: number): Response {
    return new Response(
        JSON.stringify({ token, expires_at: Date.now() / 1000 + expiresInS, header: HEADER }),
        { status: 200 },
    );
}

describe('fetchWithAccess', () => {
    let fetchMock: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        _resetAccessTokenForTests();
        fetchMock = vi.fn();
        vi.stubGlobal('fetch', fetchMock);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    function sentToken(call: unknown[]): string | undefined {
        const init = call[1] as RequestInit | undefined;
        return (init?.headers as Record<string, string> | undefined)?.[HEADER];
    }

    it('fetches a token once and reuses it while fresh', async () => {
        fetchMock
            .mockResolvedValueOnce(tokenResponse('t1', 300))
            .mockResolvedValue(new Response('{}', { status: 200 }));

        await fetchWithAccess(SHARD);
        await fetchWithAccess(SHARD);

        const tokenCalls = fetchMock.mock.calls.filter((c) => c[0] === '/api/ts/access-token');
        expect(tokenCalls).toHaveLength(1);
        const shardCalls = fetchMock.mock.calls.filter((c) => c[0] === SHARD);
        expect(shardCalls.map(sentToken)).toEqual(['t1', 't1']);
    });

    it('refreshes a token that is about to expire', async () => {
        fetchMock
            .mockResolvedValueOnce(tokenResponse('stale', 10))
            .mockResolvedValueOnce(new Response('{}', { status: 200 }))
            .mockResolvedValueOnce(tokenResponse('fresh', 300))
            .mockResolvedValueOnce(new Response('{}', { status: 200 }));

        await fetchWithAccess(SHARD);
        await fetchWithAccess(SHARD);

        const shardCalls = fetchMock.mock.calls.filter((c) => c[0] === SHARD);
        expect(shardCalls.map(sentToken)).toEqual(['stale', 'fresh']);
    });

    it('retries once with a new token after a 401', async () => {
        fetchMock
            .mockResolvedValueOnce(tokenResponse('old', 300))
            .mockResolvedValueOnce(new Response('{}', { status: 401 }))
            .mockResolvedValueOnce(tokenResponse('new', 300))
            .mockResolvedValueOnce(new Response('{"ok":1}', { status: 200 }));

        const res = await fetchWithAccess(SHARD);

        expect(res.status).toBe(200);
        const shardCalls = fetchMock.mock.calls.filter((c) => c[0] === SHARD);
        expect(shardCalls.map(sentToken)).toEqual(['old', 'new']);
    });

    it('passes the Turnstile challenge the server asks for, then solves one up front', async () => {
        const passes = ['pass-1', 'pass-2'];
        const rendered: string[] = [];
        vi.stubGlobal('turnstile', {
            render: (_el: HTMLElement, opts: { sitekey: string; callback: (t: string) => void }) => {
                rendered.push(opts.sitekey);
                opts.callback(passes.shift() ?? 'none-left');
                return 'widget';
            },
            remove: () => undefined,
        });
        const asked = new Response(JSON.stringify({ turnstile_site_key: 'site-key' }), {
            status: 403,
        });
        fetchMock
            .mockResolvedValueOnce(asked)
            .mockResolvedValueOnce(tokenResponse('t1', 10))
            .mockResolvedValueOnce(new Response('{}', { status: 200 }))
            .mockResolvedValueOnce(tokenResponse('t2', 300))
            .mockResolvedValueOnce(new Response('{}', { status: 200 }));

        await fetchWithAccess(SHARD);
        await fetchWithAccess(SHARD);

        const tokenBodies = fetchMock.mock.calls
            .filter((c) => c[0] === '/api/ts/access-token')
            .map((c) => JSON.parse((c[1] as RequestInit).body as string) as unknown);
        expect(tokenBodies).toEqual([{}, { turnstile: 'pass-1' }, { turnstile: 'pass-2' }]);
        expect(rendered).toEqual(['site-key', 'site-key']);
    });

    it('fails the fetch when the server refuses the token without naming a challenge', async () => {
        fetchMock.mockImplementation(() =>
            Promise.resolve(new Response('{"reason":"challenge_failed"}', { status: 403 })),
        );

        await expect(fetchWithAccess(SHARD)).rejects.toThrow('access token request failed: 403');
    });
});
