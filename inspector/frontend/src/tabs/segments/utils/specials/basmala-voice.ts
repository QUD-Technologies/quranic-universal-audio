/**
 * Al-Fatiha's Basmala voice call — the specials data the Segments notice reads
 * and writes (`docs/reference/specials.md` § Voice verdict).
 *
 * The aligner scores whether Al-Fatiha's Basmala is in the reciter's own voice
 * (`voice_verdict`); a holder of `segments.confirm_basmala_voice` settles or
 * overrules it (`voice_confirmed`, which wins).
 */

import { fetchJsonOrNull } from '../../../../lib/api';
import { type ApiErrorBody, friendlyError } from '../../../../lib/errors/friendly';
import type {
    SegSpecialsResponse,
    SegSpecialsVoiceRequest,
    SpecialEntry,
} from '../../../../lib/types/generated/schemas';

/** Capability gating the voice call (registry: `qua_shared/schemas/config/capabilities.py`). */
export const BASMALA_VOICE_CAPABILITY = 'segments.confirm_basmala_voice';

/** Al-Fatiha — the only chapter whose Basmala carries a voice verdict. */
export const FATIHA_CHAPTER = 1;

/**
 * Al-Fatiha's Basmala to call, or null when there is none: the 1:1 verse entry
 * even when the voice check never ran (so a maintainer can still settle it), else
 * a chapter-1 Basmala that carries a verdict or a saved call.
 */
export function fatihaBasmalaEntry(doc: SegSpecialsResponse | null): SpecialEntry | null {
    const entries = (doc?.chapters?.[String(FATIHA_CHAPTER)] ?? []).filter((e) => e.type === 'Basmala');
    return entries.find((entry) => entry.source === 'fatiha_verse')
        ?? entries.find((entry) => entry.voice_verdict != null || entry.voice_confirmed != null)
        ?? null;
}

/** The reciter's specials; null when unavailable (the notice then stays hidden). */
export function loadSpecials(reciter: string): Promise<SegSpecialsResponse | null> {
    return fetchJsonOrNull<SegSpecialsResponse>(`/api/seg/specials/${encodeURIComponent(reciter)}`);
}

/** A rejected voice call, carrying user-facing copy (never the raw server error). */
export class BasmalaVoiceError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'BasmalaVoiceError';
    }
}

/** Record the maintainer's call. Throws `BasmalaVoiceError` with friendly copy. */
export async function confirmBasmalaVoice(
    reciter: string,
    startMs: number,
    sameVoice: boolean,
): Promise<void> {
    const body: SegSpecialsVoiceRequest = { chapter: FATIHA_CHAPTER, start_ms: startMs, same_voice: sameVoice };
    let res: Response;
    try {
        res = await fetch(`/api/seg/specials/${encodeURIComponent(reciter)}/voice`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    } catch (err) {
        console.error('[basmala-voice] request failed', err);
        throw new BasmalaVoiceError(friendlyError(undefined, 0));
    }
    if (res.ok) return;
    let errorBody: ApiErrorBody | undefined;
    try {
        errorBody = (await res.json()) as ApiErrorBody;
    } catch {
        errorBody = undefined; // non-JSON body — fall back to the status copy
    }
    console.error('[basmala-voice] rejected', res.status, errorBody);
    throw new BasmalaVoiceError(friendlyError(errorBody, res.status));
}
