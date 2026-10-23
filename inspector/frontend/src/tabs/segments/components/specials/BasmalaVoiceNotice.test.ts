import { fireEvent, render, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { currentUser, resetCurrentUser } from '../../../../lib/stores/current-user';
import type { SegSpecialsResponse } from '../../../../lib/types/generated/schemas';
import { selectedChapter, selectedReciter } from '../../stores/chapter';
import BasmalaVoiceNotice from './BasmalaVoiceNotice.svelte';

const SPECIALS: SegSpecialsResponse = {
    chapters: {
        '1': [{
            type: 'Basmala', position: 'pre', start_ms: 1200, end_ms: 4800,
            source: 'fatiha_verse', voice_z: 2.0, voice_verdict: 'needs_confirmation',
        }],
    },
};

function signIn(capabilities: string[]): void {
    currentUser.set({
        login: 'maint', hf_user_id: 'u-1', role: 'maintainer', active_claim: null, active_claims: [],
        dev_mode: false, capabilities, guides_read: [], notifications_unread: 0,
    });
}

/** GET serves `SPECIALS`; POST answers with `postStatus`. */
function stubFetch(postStatus = 200) {
    return vi.spyOn(window, 'fetch').mockImplementation(async (_input, init) => {
        if (init?.method === 'POST') {
            const body = postStatus === 200 ? { ok: true } : { error: 'nope' };
            return new Response(JSON.stringify(body), { status: postStatus });
        }
        return new Response(JSON.stringify(SPECIALS), { status: 200 });
    });
}

describe('BasmalaVoiceNotice', () => {
    beforeEach(() => {
        selectedReciter.set('reciter_a');
        selectedChapter.set('1');
    });
    afterEach(() => {
        vi.restoreAllMocks();
        resetCurrentUser();
        selectedReciter.set('');
        selectedChapter.set('');
    });

    it('stays hidden, without fetching, for a user lacking the capability', async () => {
        signIn([]);
        const fetchSpy = stubFetch();
        const { container } = render(BasmalaVoiceNotice);
        await Promise.resolve();
        expect(container.querySelector('.bv-notice')).toBeNull();
        expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('stays hidden off chapter 1', async () => {
        signIn(['segments.confirm_basmala_voice']);
        selectedChapter.set('2');
        const fetchSpy = stubFetch();
        const { container } = render(BasmalaVoiceNotice);
        await Promise.resolve();
        expect(container.querySelector('.bv-notice')).toBeNull();
        expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('asks for a call on an unsure verdict and shows the saved call after it', async () => {
        signIn(['segments.confirm_basmala_voice']);
        const fetchSpy = stubFetch();
        const { container, getByText, findByText } = render(BasmalaVoiceNotice);
        await findByText(/couldn't tell whether the Basmala/);
        expect(fetchSpy.mock.calls[0]![0]).toBe('/api/seg/specials/reciter_a');
        expect(container.querySelector('.bv-notice.bv-pending')).not.toBeNull();

        await fireEvent.click(getByText('Different voice'));
        await findByText('Confirmed: the Basmala is in another voice.');
        const post = fetchSpy.mock.calls.find(([, init]) => init?.method === 'POST')!;
        expect(post[0]).toBe('/api/seg/specials/reciter_a/voice');
        expect(JSON.parse(String(post[1]!.body))).toEqual({ chapter: 1, start_ms: 1200, same_voice: false });
        // Settled: the two choices give way to a "Change" button.
        expect(container.querySelector('.bv-notice.bv-pending')).toBeNull();
        expect(getByText('Change')).toBeTruthy();
    });

    it('shows friendly copy when the call is refused, keeping the choices', async () => {
        signIn(['segments.confirm_basmala_voice']);
        vi.spyOn(console, 'error').mockImplementation(() => {});
        stubFetch(403);
        const { getByText, findByText, findByRole } = render(BasmalaVoiceNotice);
        await findByText('Same voice');
        await fireEvent.click(getByText('Same voice'));
        const alert = await findByRole('alert');
        expect(alert.textContent).toBe("You don't have permission for this action.");
        await waitFor(() => expect((getByText('Same voice') as HTMLButtonElement).disabled).toBe(false));
    });
});
