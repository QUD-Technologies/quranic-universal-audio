/**
 * `loadCurrentUser` may only sign the UI out on an authoritative `/api/me`
 * answer. A paused/booting Space, a Cloudflare error page or a network error
 * (e.g. the first focus re-check after a laptop wakes) must keep the user and
 * retry — that transient-as-anonymous path is what looked like a daily logout.
 */

import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
    type CurrentUser,
    currentUser,
    loadCurrentUser,
    ME_RETRY_DELAYS_MS,
    resetCurrentUser,
} from '../current-user';

const ANON_BODY = {
    login: null,
    hf_user_id: null,
    role: null,
    active_claim: null,
    active_claims: [],
    dev_mode: false,
    capabilities: [],
    guides_read: [],
    notifications_unread: 0,
};

const ALICE: CurrentUser = { ...ANON_BODY, login: 'alice', hf_user_id: 'u-1', role: 'maintainer' };

function json(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
    });
}

function html(status: number): Response {
    return new Response('The space is paused', { status, headers: { 'content-type': 'text/html' } });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
    vi.useFakeTimers();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    resetCurrentUser();
});

afterEach(() => {
    resetCurrentUser();
    vi.unstubAllGlobals();
    vi.useRealTimers();
});

async function signInAlice(): Promise<void> {
    fetchMock.mockResolvedValueOnce(json(ALICE));
    await loadCurrentUser();
    expect(get(currentUser).hf_user_id).toBe('u-1');
}

describe('loadCurrentUser', () => {
    it('sets the user from a signed-in 200', async () => {
        await signInAlice();
    });

    it('signs out on an authoritative anonymous 200', async () => {
        await signInAlice();
        fetchMock.mockResolvedValueOnce(json(ANON_BODY));
        await loadCurrentUser();
        expect(get(currentUser).hf_user_id).toBeNull();
    });

    it('keeps the user on a 503 HTML page (paused / booting Space)', async () => {
        await signInAlice();
        fetchMock.mockResolvedValueOnce(html(503));
        const out = await loadCurrentUser();
        expect(out.hf_user_id).toBe('u-1');
        expect(get(currentUser).hf_user_id).toBe('u-1');
    });

    it('keeps the user on a 200 that is not JSON', async () => {
        await signInAlice();
        fetchMock.mockResolvedValueOnce(html(200));
        await loadCurrentUser();
        expect(get(currentUser).hf_user_id).toBe('u-1');
    });

    it('keeps the user when fetch throws (network down after wake)', async () => {
        await signInAlice();
        fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        await loadCurrentUser();
        expect(get(currentUser).hf_user_id).toBe('u-1');
    });

    it('retries after a transient failure and applies the answer', async () => {
        await signInAlice();
        fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        await loadCurrentUser();
        fetchMock.mockResolvedValueOnce(json(ANON_BODY));
        await vi.advanceTimersByTimeAsync(ME_RETRY_DELAYS_MS[0]);
        expect(fetchMock).toHaveBeenCalledTimes(3);
        expect(get(currentUser).hf_user_id).toBeNull();
    });

    it('stops retrying after the last backoff', async () => {
        fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
        await loadCurrentUser();
        const total = ME_RETRY_DELAYS_MS.reduce((a, b) => a + b, 0);
        await vi.advanceTimersByTimeAsync(total * 2);
        expect(fetchMock).toHaveBeenCalledTimes(1 + ME_RETRY_DELAYS_MS.length);
    });

    it('a newer call supersedes a pending retry (no pile-up)', async () => {
        await signInAlice();
        fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        await loadCurrentUser();
        fetchMock.mockResolvedValue(json(ALICE));
        await loadCurrentUser();
        await vi.advanceTimersByTimeAsync(ME_RETRY_DELAYS_MS[0] * 10);
        expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it('resetCurrentUser cancels a pending retry', async () => {
        await signInAlice();
        fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        await loadCurrentUser();
        resetCurrentUser();
        fetchMock.mockResolvedValue(json(ALICE));
        await vi.advanceTimersByTimeAsync(ME_RETRY_DELAYS_MS[0] * 10);
        expect(get(currentUser).hf_user_id).toBeNull();
    });
});
