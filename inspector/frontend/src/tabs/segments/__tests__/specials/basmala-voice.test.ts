import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SegSpecialsResponse, SpecialEntry } from '../../../../lib/types/generated/schemas';
import {
    BasmalaVoiceError,
    confirmBasmalaVoice,
    fatihaBasmalaEntry,
} from '../../utils/specials/basmala-voice';

function entry(over: Partial<SpecialEntry>): SpecialEntry {
    return { type: 'Basmala', start_ms: 1200, end_ms: 4800, source: 'fatiha_verse', ...over };
}

describe('fatihaBasmalaEntry', () => {
    it("picks Al-Fatiha's voice-scored Basmala over its other openers", () => {
        const doc: SegSpecialsResponse = {
            chapters: {
                '1': [
                    entry({ type: "Isti'adha", start_ms: 0, end_ms: 1100, source: 'align', voice_verdict: null }),
                    entry({ voice_verdict: 'needs_confirmation', voice_z: 2.0 }),
                ],
            },
        };
        expect(fatihaBasmalaEntry(doc)).toMatchObject({ type: 'Basmala', start_ms: 1200 });
    });

    it('keeps a maintainer-confirmed Basmala whose verdict is absent', () => {
        const doc: SegSpecialsResponse = { chapters: { '1': [entry({ voice_confirmed: false })] } };
        expect(fatihaBasmalaEntry(doc)?.voice_confirmed).toBe(false);
    });

    it('keeps the 1:1 Basmala the voice check never scored, so it can still be called', () => {
        const doc: SegSpecialsResponse = { chapters: { '1': [entry({ voice_verdict: null })] } };
        expect(fatihaBasmalaEntry(doc)).toMatchObject({ source: 'fatiha_verse', start_ms: 1200 });
    });

    it('returns null without an Al-Fatiha Basmala', () => {
        expect(fatihaBasmalaEntry(null)).toBeNull();
        expect(fatihaBasmalaEntry({ chapters: {} })).toBeNull();
        // An unscored recorded opener and other chapters don't count.
        expect(fatihaBasmalaEntry({
            chapters: { '1': [entry({ source: 'align', voice_verdict: null })] },
        })).toBeNull();
        expect(fatihaBasmalaEntry({
            chapters: { '2': [entry({ source: 'align', voice_verdict: 'same' })] },
        })).toBeNull();
    });
});

describe('confirmBasmalaVoice', () => {
    afterEach(() => vi.restoreAllMocks());

    it("POSTs the chapter-1 call for the Basmala's start", async () => {
        const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue(
            new Response(JSON.stringify({ ok: true }), { status: 200 }),
        );
        await confirmBasmalaVoice('reciter a', 1200, false);
        const [url, init] = fetchSpy.mock.calls[0]!;
        expect(url).toBe('/api/seg/specials/reciter%20a/voice');
        expect(init?.method).toBe('POST');
        expect(JSON.parse(String(init?.body))).toEqual({ chapter: 1, start_ms: 1200, same_voice: false });
    });

    it('rejects with friendly copy, never the raw server error', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => {});
        vi.spyOn(window, 'fetch').mockResolvedValue(
            new Response(JSON.stringify({ error: 'capability segments.confirm_basmala_voice required' }), { status: 403 }),
        );
        const call = confirmBasmalaVoice('r', 1200, true);
        await expect(call).rejects.toBeInstanceOf(BasmalaVoiceError);
        await expect(call).rejects.toThrow("You don't have permission for this action.");
    });
});
