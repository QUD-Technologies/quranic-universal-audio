import { render } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { describe, expect, it } from 'vitest';

import type { ReciterTask } from '../../api/reciter-task';
import { claimConfirmModal, closeClaimConfirm } from '../../stores/claim-confirm-modal';
import { currentUser } from '../../stores/current-user';
import ClaimButton from '../ClaimButton.svelte';

function makeTask(canClaim: boolean): ReciterTask {
    return {
        row: {
            slug: 'other-reciter',
            name: 'Other',
            state: 'awaiting_review',
            state_since: '2026-01-01T00:00:00Z',
            assignee_hf_id: null,
            assignee_login: null,
            assignee_since: null,
            marked_ready: false,
            visibility: 'public',
        },
        predicates: {
            can_claim: canClaim,
            can_edit: false,
            can_edit_as_admin: false,
            can_edit_as_owner: false,
            can_mark_ready: false,
            can_skip_mark_ready_gates: false,
            can_unmark_ready: false,
            can_release: false,
            can_set_quality_status: false,
            can_clear_quality_status: false,
        },
    } as ReciterTask;
}

describe('ClaimButton', () => {
    it('renders nothing when a contributor holds another active claim, even if can_claim is true', () => {
        currentUser.set({
            login: 'me',
            hf_user_id: 'u-1',
            role: 'contributor',
            active_claim: 'some-other-slug',
            active_claims: ['some-other-slug'],
            dev_mode: false,
            capabilities: [],
            guides_read: [],
            notifications_unread: 0,
        });

        const { container } = render(ClaimButton, {
            props: {
                slug: 'this-reciter',
                task: makeTask(true),
                onClaimed: null,
            },
        });

        expect(container.querySelector('button')).toBeNull();
        expect(container.textContent ?? '').not.toMatch(/Already claiming/);
    });

    it('renders the Claim button for an owner with an existing active claim (multi-claim exempt)', () => {
        currentUser.set({
            login: 'me',
            hf_user_id: 'u-1',
            role: 'owner',
            active_claim: 'some-other-slug',
            active_claims: ['some-other-slug'],
            dev_mode: false,
            capabilities: [],
            guides_read: [],
            notifications_unread: 0,
        });

        const { container } = render(ClaimButton, {
            props: {
                slug: 'second-target',
                task: makeTask(true),
                onClaimed: null,
            },
        });

        expect(container.querySelector('button')).not.toBeNull();
    });

    it('renders the Claim button when can_claim is true and no foreign active claim', () => {
        currentUser.set({
            login: 'me',
            hf_user_id: 'u-1',
            role: 'contributor',
            active_claim: null,
            active_claims: [],
            dev_mode: false,
            capabilities: [],
            guides_read: [],
            notifications_unread: 0,
        });

        const { container } = render(ClaimButton, {
            props: {
                slug: 'target',
                task: makeTask(true),
                onClaimed: null,
            },
        });

        const btn = container.querySelector('button');
        expect(btn).not.toBeNull();
        expect(btn!.classList.contains('seg-btn')).toBe(true);
        expect(btn!.classList.contains('primary')).toBe(true);
        expect(btn!.classList.contains('lg')).toBe(false);
    });

    it('renders the Claim button once the held claim is marked ready (active_claim null, active_claims non-empty)', () => {
        // Marking ready clears the blocking `active_claim` but leaves the
        // reciter in `active_claims` — the contributor is now free to claim a
        // different one, so the button must surface for the new slug.
        currentUser.set({
            login: 'me',
            hf_user_id: 'u-1',
            role: 'contributor',
            active_claim: null,
            active_claims: ['marked-ready-slug'],
            dev_mode: false,
            capabilities: [],
            guides_read: [],
            notifications_unread: 0,
        });

        const { container } = render(ClaimButton, {
            props: { slug: 'different-reciter', task: makeTask(true), onClaimed: null },
        });

        expect(container.querySelector('button')).not.toBeNull();
    });

    it('is labelled "Claim review" and opens the confirm modal on click (no immediate claim)', () => {
        closeClaimConfirm();
        currentUser.set({
            login: 'me',
            hf_user_id: 'u-1',
            role: 'contributor',
            active_claim: null,
            active_claims: [],
            dev_mode: false,
            capabilities: [],
            guides_read: [],
            notifications_unread: 0,
        });

        const { container } = render(ClaimButton, {
            props: { slug: 'target', task: makeTask(true), onClaimed: null },
        });

        const btn = container.querySelector('button')!;
        expect(btn.textContent?.trim()).toBe('Claim review');
        expect(get(claimConfirmModal).open).toBe(false);

        btn.click();
        const state = get(claimConfirmModal);
        expect(state.open).toBe(true);
        expect(state.slug).toBe('target');
    });
});
