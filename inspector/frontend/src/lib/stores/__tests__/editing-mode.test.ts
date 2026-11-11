/**
 * `syncEditingMode(currentUser, task)` is the load-bearing helper that
 * maps `(user, reciter-task)` to an `EditingMode`. Every branch matters
 * because the resulting popover copy is user-visible and the editGate
 * action gates real save/undo affordances.
 */

import { describe, expect, it } from 'vitest';

import type { ReciterTask, ReciterTaskState, Visibility } from '../../api/reciter-task';
import type { CurrentUser, Role } from '../current-user';
import { syncEditingMode } from '../editing-mode';

function _user(overrides: Partial<CurrentUser> = {}): CurrentUser {
    return {
        login: 'alice',
        hf_user_id: 'u-1',
        role: 'contributor' as Role,
        active_claim: null,
        active_claims: [],
        dev_mode: false,
        capabilities: [],
        guides_read: [],
        notifications_unread: 0,
        ...overrides,
    };
}

function _task(overrides: {
    state?: ReciterTaskState;
    marked_ready?: boolean;
    visibility?: Visibility;
    assignee_hf_id?: string | null;
} = {}): ReciterTask {
    return {
        row: {
            slug: 'test_slug',
            name: 'Test Reciter',
            state: overrides.state ?? 'awaiting_review',
            state_since: '2026-05-12T00:00:00Z',
            assignee_hf_id: overrides.assignee_hf_id ?? null,
            assignee_login: overrides.assignee_hf_id ? 'bob' : null,
            assignee_since: overrides.assignee_hf_id ? '2026-05-12T00:00:00Z' : null,
            marked_ready: overrides.marked_ready ?? false,
            visibility: overrides.visibility ?? 'public',
        },
        predicates: {
            can_claim: false,
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
    };
}

describe('syncEditingMode', () => {
    it('returns view/unauthenticated when user is null', () => {
        expect(syncEditingMode(null, _task())).toEqual({
            kind: 'view',
            viewReason: 'unauthenticated',
        });
    });

    it('returns view/unauthenticated when task is null', () => {
        expect(syncEditingMode(_user(), null)).toEqual({
            kind: 'view',
            viewReason: 'unauthenticated',
        });
    });

    it('returns view/discarded when visibility is discarded', () => {
        const task = _task({ state: 'under_review', visibility: 'discarded', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user(), task)).toEqual({
            kind: 'view',
            viewReason: 'discarded',
        });
    });

    it('returns view/published for a released reciter', () => {
        const task = _task({ state: 'released' });
        expect(syncEditingMode(_user(), task)).toEqual({
            kind: 'view',
            viewReason: 'published',
        });
    });

    it('returns view/claimable for an awaiting_review reciter (free to claim)', () => {
        const task = _task({ state: 'awaiting_review' });
        expect(syncEditingMode(_user(), task)).toEqual({
            kind: 'view',
            viewReason: 'claimable',
        });
    });

    it('returns view/holds-other-claim when the user already holds a different claim', () => {
        // One-at-a-time: an existing claim on another reciter takes precedence
        // over "claim this one" so the popover doesn't dangle a dead button.
        const task = _task({ state: 'awaiting_review' });
        expect(syncEditingMode(_user({ active_claim: 'some-other' }), task)).toEqual({
            kind: 'view',
            viewReason: 'holds-other-claim',
        });
    });

    it('still shows claimable when the held claim IS this reciter', () => {
        const task = _task({ state: 'awaiting_review' });
        expect(syncEditingMode(_user({ active_claim: 'test_slug' }), task)).toEqual({
            kind: 'view',
            viewReason: 'claimable',
        });
    });

    it('owner with another claim still gets owner (multi-claim exempt)', () => {
        const task = _task({ state: 'awaiting_review' });
        expect(
            syncEditingMode(_user({ role: 'owner', active_claim: 'some-other' }), task),
        ).toEqual({ kind: 'owner' });
    });

    it('returns view/marked_ready when assignee marked their own claim', () => {
        const task = _task({
            state: 'under_review',
            marked_ready: true,
            assignee_hf_id: 'u-1',
        });
        expect(syncEditingMode(_user(), task)).toEqual({
            kind: 'view',
            viewReason: 'marked_ready',
        });
    });

    it('returns editor for the active reviewer on an under_review row', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user(), task)).toEqual({ kind: 'editor' });
    });

    it('returns maintainer for a maintainer viewing someone else\'s active claim', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'other' });
        expect(syncEditingMode(_user({ role: 'maintainer' }), task)).toEqual({
            kind: 'maintainer',
        });
    });

    it('returns owner for an owner viewing someone else\'s active claim', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'other' });
        expect(syncEditingMode(_user({ role: 'owner' }), task)).toEqual({
            kind: 'owner',
        });
    });

    it('returns view/wrong-assignee for a contributor on someone else\'s under_review', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'other' });
        expect(syncEditingMode(_user(), task)).toEqual({
            kind: 'view',
            viewReason: 'wrong-assignee',
        });
    });

    it('admin sees marked_ready as wrong-assignee (frozen for non-assignee admin)', () => {
        const task = _task({
            state: 'under_review',
            marked_ready: true,
            assignee_hf_id: 'other',
        });
        // Admin override doesn't apply when row is marked_ready — that's the
        // contract: marked_ready is frozen until the reviewer unmarks or a
        // maintainer publishes via the dedicated admin endpoint (Phase 5).
        expect(syncEditingMode(_user({ role: 'maintainer' }), task)).toEqual({
            kind: 'view',
            viewReason: 'wrong-assignee',
        });
    });

    it('returns view/not-available for pre-review (catalogued / awaiting_alignment) rows', () => {
        for (const state of ['catalogued', 'awaiting_alignment'] as const) {
            const task = _task({ state });
            expect(syncEditingMode(_user(), task)).toEqual({
                kind: 'view',
                viewReason: 'not-available',
            });
        }
    });

    it('returns view/unauthenticated for hf_user_id=null signed-in shape', () => {
        // Anonymous shape — currentUser has all fields null when /api/me
        // returned anonymous. Treat as not-signed-in.
        const anon: CurrentUser = {
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
        expect(syncEditingMode(anon, _task())).toEqual({
            kind: 'view',
            viewReason: 'unauthenticated',
        });
    });

    // ------------------------------------------------------------------
    // Owner-only: edit any state
    // ------------------------------------------------------------------

    it('returns owner for owner on a released reciter', () => {
        const task = _task({ state: 'released' });
        expect(syncEditingMode(_user({ role: 'owner' }), task)).toEqual({ kind: 'owner' });
    });

    it('returns owner for owner on a catalogued reciter', () => {
        const task = _task({ state: 'catalogued' });
        expect(syncEditingMode(_user({ role: 'owner' }), task)).toEqual({ kind: 'owner' });
    });

    it('returns owner for owner on an awaiting_review row (no claim needed)', () => {
        const task = _task({ state: 'awaiting_review' });
        expect(syncEditingMode(_user({ role: 'owner' }), task)).toEqual({ kind: 'owner' });
    });

    it('returns owner for owner on a marked_ready row (override bypasses freeze)', () => {
        const task = _task({
            state: 'under_review',
            marked_ready: true,
            assignee_hf_id: 'other',
        });
        expect(syncEditingMode(_user({ role: 'owner' }), task)).toEqual({
            kind: 'owner',
        });
    });

    it('returns view/discarded for owner on a discarded reciter (visibility trumps role)', () => {
        const task = _task({ state: 'under_review', visibility: 'discarded' });
        expect(syncEditingMode(_user({ role: 'owner' }), task)).toEqual({
            kind: 'view',
            viewReason: 'discarded',
        });
    });

    // ------------------------------------------------------------------
    // First-edit onboarding gate (allGuidesRead, 3rd arg)
    // ------------------------------------------------------------------

    it('gates the would-be editor as view/guides_unread when guides are unread', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user(), task, false)).toEqual({
            kind: 'view',
            viewReason: 'guides_unread',
        });
    });

    it('lets the editor through once all guides are read', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user(), task, true)).toEqual({ kind: 'editor' });
    });

    it('defaults to ungated (allGuidesRead omitted ⇒ true)', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user(), task)).toEqual({ kind: 'editor' });
    });

    it('does NOT exempt dev-mode — the gate must be visible/testable locally', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user({ dev_mode: true }), task, false)).toEqual({
            kind: 'view',
            viewReason: 'guides_unread',
        });
    });

    it('gates maintainers too (all editing roles read once)', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'other' });
        expect(syncEditingMode(_user({ role: 'maintainer' }), task, false)).toEqual({
            kind: 'view',
            viewReason: 'guides_unread',
        });
    });

    it('lets a maintainer through once all guides are read', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'other' });
        expect(syncEditingMode(_user({ role: 'maintainer' }), task, true)).toEqual({
            kind: 'maintainer',
        });
    });

    it('gates owners too (the owner fast-path still honours the guide gate)', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user({ role: 'owner' }), task, false)).toEqual({
            kind: 'view',
            viewReason: 'guides_unread',
        });
    });

    it('lets an owner through once all guides are read', () => {
        const task = _task({ state: 'under_review', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user({ role: 'owner' }), task, true)).toEqual({
            kind: 'owner',
        });
    });

    // ------------------------------------------------------------------
    // Gate ORDER: sign-in > state/ownership denial > guides_unread
    // The guide gate must be LAST — a user who can't edit for a state/
    // ownership reason should see THAT, not "read the guides first".
    // ------------------------------------------------------------------

    it('shows the state denial (not guides) for a non-claimant with unread guides', () => {
        // Contributor on someone else's claim: wrong-assignee must win even
        // though guides are unread.
        const task = _task({ state: 'under_review', assignee_hf_id: 'other' });
        expect(syncEditingMode(_user(), task, false)).toEqual({
            kind: 'view',
            viewReason: 'wrong-assignee',
        });
    });

    it('shows claimable (not guides) on an unclaimed row with unread guides', () => {
        const task = _task({ state: 'awaiting_review' });
        expect(syncEditingMode(_user(), task, false)).toEqual({
            kind: 'view',
            viewReason: 'claimable',
        });
    });

    it('shows published (not guides) on a released row with unread guides', () => {
        const task = _task({ state: 'released' });
        expect(syncEditingMode(_user(), task, false)).toEqual({
            kind: 'view',
            viewReason: 'published',
        });
    });

    it('only reaches guides_unread once the user actually holds an editable position', () => {
        // Assignee on their own under_review claim → the one case that SHOULD
        // hit the guide gate.
        const task = _task({ state: 'under_review', assignee_hf_id: 'u-1' });
        expect(syncEditingMode(_user(), task, false)).toEqual({
            kind: 'view',
            viewReason: 'guides_unread',
        });
    });

});
