import { fireEvent, render, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, type MockInstance, vi } from 'vitest';

import { currentUser } from '../../../lib/stores/current-user';
import DeliveryAlignControl from './DeliveryAlignControl.svelte';

const SLUG = 'some_reciter_mp3quran';

function user(capabilities: string[]) {
    return {
        login: 'me',
        hf_user_id: 'u-1',
        role: 'maintainer' as const,
        active_claim: null,
        active_claims: [],
        dev_mode: false,
        capabilities,
        guides_read: [],
        notifications_unread: 0,
    };
}

function run(status: string, stage = 'align') {
    return {
        run_id: 'r1',
        slug: SLUG,
        stage,
        status,
        chapters_total: 114,
        chapters_done: 12,
        started_at: '2026-10-18T00:00:00Z',
        updated_at: '2026-10-18T00:00:00Z',
    };
}

function json(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
    });
}

let fetchSpy: MockInstance<typeof fetch>;

beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch');
});

afterEach(() => {
    fetchSpy.mockRestore();
    currentUser.set(user([]) as never);
});

describe('DeliveryAlignControl', () => {
    it('renders nothing and never asks for status without intake.align', async () => {
        currentUser.set(user([]) as never);
        const { container } = render(DeliveryAlignControl, { props: { slug: SLUG } });
        await new Promise((r) => setTimeout(r, 0));
        expect(container.querySelector('button')).toBeNull();
        expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('starts a CPU run for the slug and shows its live summary', async () => {
        currentUser.set(user(['intake.align']) as never);
        fetchSpy.mockImplementation(async (input, init) => {
            const url = String(input);
            if (init?.method === 'POST') return json(run('running', 'acquire'), 202);
            if (url.endsWith('/align/status')) return json({ run: null });
            throw new Error(`unexpected ${url}`);
        });
        const { getByText, findByText } = render(DeliveryAlignControl, { props: { slug: SLUG } });

        await fireEvent.click(await findByText('Align CPU'));

        const post = fetchSpy.mock.calls.find(([, init]) => init?.method === 'POST');
        expect(String(post?.[0])).toBe(`/api/admin/reciter/${SLUG}/align`);
        expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ device: 'CPU' });
        await waitFor(() => expect(getByText('Aligning · fetching audio')).toBeTruthy());
    });

    it('shows chapter progress instead of buttons while a run is live', async () => {
        currentUser.set(user(['intake.align']) as never);
        fetchSpy.mockResolvedValue(json(run('running')));
        const { findByText, container } = render(DeliveryAlignControl, { props: { slug: SLUG } });
        expect(await findByText('Aligning · aligning chapters 12/114')).toBeTruthy();
        expect(container.querySelector('button')).toBeNull();
    });

    it('offers Retry for a failed run', async () => {
        currentUser.set(user(['intake.align']) as never);
        fetchSpy.mockResolvedValue(json(run('failed')));
        const { findByText, queryByText } = render(DeliveryAlignControl, { props: { slug: SLUG } });
        expect(await findByText('Retry')).toBeTruthy();
        expect(queryByText('Align GPU')).toBeNull();
    });
});
