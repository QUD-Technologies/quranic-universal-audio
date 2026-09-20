/**
 * Dashboard-tab AudioPort instance — shared by the Dashboard AND Timestamps
 * tabs via `BottomPlayer` (one element + transport so tab-switching cleanly
 * pauses one surface without touching the other).
 *
 * Dashboard plays full chapter MP3s (no VBR clip routing); the port is here
 * for transport uniformity and for App.svelte's tab-switch pause path. It
 * also supports `adoptElement` for the Timestamps gapless-shuffle swap.
 *
 * Kill-switch is intentionally disabled. Chapter URLs go through
 * `/api/seg/audio-proxy/...`, whose CDN tier is a SAME-ORIGIN 200/206 stream
 * with `Access-Control-Allow-Origin: *` (no 302 redirect) — so proxied URLs
 * would route through `MediaElementAudioSourceNode` fine. But some dashboard
 * sources are raw cross-origin links served directly rather than through the
 * proxy, and constructing a `MediaElementAudioSourceNode`
 * on a cross-origin element silences playback per the Web Audio spec. Full-
 * chapter playback doesn't need sample-accurate end-of-region cutoff, so we
 * keep the element on its default audio sink across the board.
 */

import { AudioPort } from './audio-port';

export const dashPort: AudioPort = new AudioPort({ disableKillSwitch: true });

if (import.meta.env.DEV) (globalThis as Record<string, unknown>).__dashPort = dashPort;
