import { fireEvent, render, waitFor } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import * as m from '$lib/paraglide/messages';
import { fetchAdminReviews } from '../../../api/admin-reviews';
import { currentUser, resetCurrentUser } from '../../../stores/current-user';
import type { PublicDelivery, PublicReciter } from '../../../types/generated/schemas';
import type { PublicBucket } from '../../../types/public-bucket';
import type { CombinationSelection } from '../combination-picker-types';
import CombinationPicker from '../CombinationPicker.svelte';

function makeDelivery(
    slug: string,
    bucket: PublicBucket,
    riwayah = 'hafs',
    marked_ready = false,
): PublicDelivery {
    return {
        slug,
        riwayah,
        style: 'murattal',
        recording_context: null,
        recording_year: null,
        source: 'mp3quran',
        channel: 'mp3quran',
        channel_name: 'mp3quran',
        source_url: null,
        audio_category: 'studio',
        chapter_count: 114,
        coverage_kind: 'full',
        state_since: null,
        bitrate_kbps_nominal: 128,
        bitrate_mode: 'cbr',
        total_duration_sec: 3600,
        bucket,
        marked_ready,
    };
}

function makeReciter(name: string, deliveries: PublicDelivery[]): PublicReciter {
    return {
        reciter_id: name.toLowerCase().replace(/\s+/g, '_'),
        name,
        name_ar: null,
        country: null,
        primary_bucket: deliveries[0]?.bucket ?? 'available_for_review',
        buckets: deliveries.map((d) => d.bucket),
        deliveries,
        riwayat: [...new Set(deliveries.map((d) => d.riwayah))],
        styles: [...new Set(deliveries.map((d) => d.style))],
        recording_contexts: [],
        sources: [...new Set(deliveries.map((d) => d.source))],
        channels: [...new Set(deliveries.map((d) => d.channel))],
        deliveries_count: deliveries.length,
        chapter_count_total: 114,
        coverage_kind: 'full',
        last_activity: null,
    };
}

vi.mock('../../../api/public-reciters', () => ({
    fetchPublicReciters: vi.fn(() =>
        Promise.resolve({
            reciters: [
                makeReciter('Alpha', [makeDelivery('alpha-hafs', 'available_for_review')]),
                makeReciter('Beta', [makeDelivery('beta-hafs', 'published')]),
                makeReciter('Gamma', [makeDelivery('gamma-hafs', 'under_review', 'hafs', true)]),
            ],
            total: 3,
            next_cursor: null,
        }),
    ),
    fetchPublicStats: vi.fn(() =>
        Promise.resolve({
            available_for_request: 0,
            requested: 0,
            available_for_review: 1,
            under_review: 1,
            published: 1,
        }),
    ),
}));

// Admin reviews overlay — the picker fetches this only when the caller holds
// `reviews.view`. The Gamma row (under_review) is claimed by `reviewer-x` and
// marked ready.
vi.mock('../../../api/admin-reviews', () => ({
    fetchAdminReviews: vi.fn(() =>
        Promise.resolve({
            rows: [
                {
                    slug: 'gamma-hafs',
                    state: 'under_review',
                    state_since: null,
                    reciter_id: 'gamma',
                    name_ar: null,
                    name_en: 'Gamma',
                    riwayah: 'hafs',
                    style: 'murattal',
                    channel: 'mp3quran',
                    open_claim: {
                        assignee_id: 'u-1',
                        login: 'reviewer-x',
                        claimed_at: '2026-01-01T00:00:00Z',
                        marked_ready_at: '2026-01-02T00:00:00Z',
                    },
                },
            ],
        }),
    ),
}));

afterEach(() => {
    resetCurrentUser();
    vi.mocked(fetchAdminReviews).mockClear();
});

describe('CombinationPicker', () => {
    // CombinationPicker renders inside Modal, whose backdrop portals to
    // `document.body` — so the rows live in `baseElement`, not `container`.
    it('emits a non-null delivery on first row click', async () => {
        const selected: CombinationSelection[] = [];
        const { baseElement } = render(CombinationPicker, {
            props: { open: true, title: m.common_picker_switch_reciter_title() },
            events: {
                select: (e: CustomEvent<CombinationSelection>) => {
                    selected.push(e.detail);
                },
            },
        });

        // Wait for load() to flush the rows in.
        const firstRow = await waitFor(() => {
            const row = baseElement.querySelector<HTMLElement>('.combo-row');
            expect(row).not.toBeNull();
            return row!;
        });

        await fireEvent.click(firstRow);

        expect(selected.length).toBe(1);
        const picked = selected[0]!;
        expect(picked.kind).toBe('combination');
        expect(picked.delivery).not.toBeNull();
        expect(picked.delivery.slug).toBeTruthy();
    });

    it('states the bucket once per group head, not on every grouped row', async () => {
        const { baseElement } = render(CombinationPicker, {
            props: { open: true, title: m.common_picker_switch_reciter_title() },
        });

        await waitFor(() => {
            expect(baseElement.querySelector('.combo-row')).not.toBeNull();
        });

        // Three distinct buckets in the mock (available_for_review,
        // under_review, published) → one labelled StatePill in each group head.
        const headPills = baseElement.querySelectorAll('.bucket-head .pill');
        expect(headPills.length).toBe(3);
        const headLabels = [...headPills].map((p) => p.textContent?.trim());
        expect(headLabels).toContain('Available for review');
        expect(headLabels).toContain('Under review');
        expect(headLabels).toContain('Published');

        // The state pill must NOT be repeated on the grouped rows — the head
        // already conveys it. (No active claim in this fixture, so every row
        // is grouped.)
        expect(baseElement.querySelectorAll('.combo-row .pill').length).toBe(0);
    });

    it('shows claimer + ready badge on under_review rows when the caller holds reviews.view', async () => {
        currentUser.set({
            login: 'admin',
            hf_user_id: 'admin-1',
            role: 'maintainer',
            active_claim: null,
            active_claims: [],
            dev_mode: false,
            capabilities: ['reviews.view'],
            guides_read: [],
            notifications_unread: 0,
        });

        const { baseElement } = render(CombinationPicker, {
            props: { open: true, title: m.common_picker_switch_reciter_title() },
        });

        const status = await waitFor(() => {
            const el = baseElement.querySelector<HTMLElement>('.review-status');
            expect(el).not.toBeNull();
            return el!;
        });

        expect(fetchAdminReviews).toHaveBeenCalled();
        expect(status.textContent).toContain('reviewer-x');
        expect(status.querySelector('.ready-badge')).not.toBeNull();
        // Only the under_review (Gamma) row carries the overlay.
        expect(baseElement.querySelectorAll('.review-status').length).toBe(1);
    });

    it('shows the ready badge but no claimer, and skips the admin fetch, without reviews.view', async () => {
        // currentUser stays anonymous (no capabilities) via afterEach reset.
        const { baseElement } = render(CombinationPicker, {
            props: { open: true, title: m.common_picker_switch_reciter_title() },
        });

        const status = await waitFor(() => {
            const el = baseElement.querySelector<HTMLElement>('.review-status');
            expect(el).not.toBeNull();
            return el!;
        });

        // READY is public (delivery.marked_ready); the claimer login is not.
        expect(status.querySelector('.ready-badge')).not.toBeNull();
        expect(status.querySelector('.reviewer')).toBeNull();
        expect(status.textContent).not.toContain('reviewer-x');
        expect(baseElement.querySelectorAll('.review-status').length).toBe(1);
        expect(fetchAdminReviews).not.toHaveBeenCalled();
    });
});
