/**
 * Cloudflare Turnstile, invisible: the access-token endpoint issues a token only
 * to a page that has just passed it (`inspector/services/auth/turnstile.py`). Each pass is single-use, so
 * every token request runs a fresh challenge.
 */

const SCRIPT = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
/** Give up on a challenge that never answers, ms. */
const CHALLENGE_TIMEOUT_MS = 30_000;

interface TurnstileApi {
    render(
        el: HTMLElement,
        opts: {
            sitekey: string;
            callback: (token: string) => void;
            'error-callback': (code: string) => void;
            'timeout-callback': () => void;
        },
    ): string;
    remove(widgetId: string): void;
}

declare global {
    interface Window {
        turnstile?: TurnstileApi;
    }
}

let scriptLoad: Promise<TurnstileApi> | null = null;

function loadScript(): Promise<TurnstileApi> {
    scriptLoad ??= new Promise<TurnstileApi>((resolve, reject) => {
        if (window.turnstile) {
            resolve(window.turnstile);
            return;
        }
        const el = document.createElement('script');
        el.src = SCRIPT;
        el.async = true;
        el.onload = () => {
            if (window.turnstile) resolve(window.turnstile);
            else reject(new Error('turnstile: no API'));
        };
        el.onerror = () => reject(new Error('turnstile: script failed to load'));
        document.head.appendChild(el);
    }).catch((err: unknown) => {
        scriptLoad = null; // let the next request retry the load
        throw err;
    });
    return scriptLoad;
}

/** A fresh Turnstile pass for the widget `siteKey`. */
export async function turnstileToken(siteKey: string): Promise<string> {
    const api = await loadScript();
    const host = document.createElement('div');
    host.style.display = 'none';
    document.body.appendChild(host);
    let widgetId: string | undefined;
    try {
        return await new Promise<string>((resolve, reject) => {
            const timer = setTimeout(
                () => reject(new Error('turnstile: timed out')),
                CHALLENGE_TIMEOUT_MS,
            );
            const fail = (why: string): void => {
                clearTimeout(timer);
                reject(new Error(`turnstile: ${why}`));
            };
            widgetId = api.render(host, {
                sitekey: siteKey,
                callback: (token) => {
                    clearTimeout(timer);
                    resolve(token);
                },
                'error-callback': (code) => fail(`error ${code}`),
                'timeout-callback': () => fail('challenge expired'),
            });
        });
    } finally {
        if (widgetId !== undefined) api.remove(widgetId);
        host.remove();
    }
}
