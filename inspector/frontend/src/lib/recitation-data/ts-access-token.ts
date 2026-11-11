/**
 * Short-lived access token for full-chapter shard fetches.
 *
 * `/api/ts/shard/*` refuses requests without a fresh `X-QUA-Access` token
 * (see `inspector/services/auth/ts_access.py`). The page fetches one from
 * `/api/ts/access-token`, reuses it until shortly before it expires, and
 * refetches on a 401 (expired, or the reader's address changed: a token is
 * only good from the address it was issued to).
 *
 * When the server asks for a Turnstile challenge it answers the first request
 * with 403 and the widget's site key; the page solves it and asks again, and
 * solves one up front for every later token.
 */

import { turnstileToken } from './turnstile';

const TOKEN_URL = '/api/ts/access-token';
/** Refresh this many seconds before the server-side expiry, to absorb clock skew. */
const REFRESH_MARGIN_S = 30;
const FORBIDDEN = 403;

interface AccessToken {
    token: string;
    expires_at: number;
    header: string;
}

let _token: Promise<AccessToken> | null = null;
/** The Turnstile widget the server asked us to pass, once it has. */
let _siteKey: string | null = null;

function _fresh(t: AccessToken): boolean {
    return t.expires_at - REFRESH_MARGIN_S > Date.now() / 1000;
}

function _post(turnstile: string | null): Promise<Response> {
    return fetch(TOKEN_URL, {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(turnstile ? { turnstile } : {}),
    });
}

/** The site key a 403 names when the server wants a challenge passed, else null. */
async function _challengeAsked(res: Response): Promise<string | null> {
    if (res.status !== FORBIDDEN) return null;
    const body = (await res.json().catch(() => null)) as { turnstile_site_key?: unknown } | null;
    const key = body?.turnstile_site_key;
    return typeof key === 'string' && key ? key : null;
}

async function _request(): Promise<AccessToken> {
    let res = await _post(_siteKey ? await turnstileToken(_siteKey) : null);
    const asked = await _challengeAsked(res.clone());
    if (asked) {
        _siteKey = asked;
        res = await _post(await turnstileToken(asked));
    }
    if (!res.ok) throw new Error(`access token request failed: ${res.status}`);
    return (await res.json()) as AccessToken;
}

async function _current(): Promise<AccessToken> {
    if (_token) {
        const t = await _token.catch(() => null);
        if (t && _fresh(t)) return t;
    }
    const p = _request();
    _token = p;
    p.catch(() => { if (_token === p) _token = null; });
    return p;
}

/** Headers to send with a shard request. */
export async function accessHeaders(): Promise<Record<string, string>> {
    const t = await _current();
    return { [t.header]: t.token };
}

/** Drop the cached token (after a 401) so the next call fetches a new one. */
export function invalidateAccessToken(): void {
    _token = null;
}

/** GET `url` with the access token; on a 401 refresh the token and retry once. */
export async function fetchWithAccess(url: string): Promise<Response> {
    const res = await fetch(url, { headers: await accessHeaders() });
    if (res.status !== 401) return res;
    invalidateAccessToken();
    return fetch(url, { headers: await accessHeaders() });
}

export function _resetAccessTokenForTests(): void {
    _token = null;
    _siteKey = null;
}
