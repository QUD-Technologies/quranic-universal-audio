<script lang="ts">
    /**
     * Vertical activity rail rendered on the right column of the
     * Dashboard list view. Polls every 30 seconds while the tab is
     * visible (Page Visibility API via visible-poll); discards
     * in-flight responses that resolve after the tab hides.
     *
     * Capability-gated affordances (default owner-only, but owner-toggleable
     * via the Permissions tab):
     * - Actor login (``@alice · 2h ago``) — ``identity.see_actor``.
     * - Trash icon to permanently delete a card from the public feed
     *   (writes a tombstone; reason ≥10 chars required) — ``activity.delete``.
     */
    import { onDestroy, onMount } from 'svelte';

    import { localeStore, tr } from '$lib/i18n/locale-store';
    import { vocabLabel } from '$lib/i18n/vocab';
    import * as m from '$lib/paraglide/messages';
    import {
        fetchPublicActivity,
        type PublicActivityCard,
        type PublicEventKind,
    } from '../../../lib/api/public-activity';
    import { deletePublicActivity } from '../../../lib/api/public-activity-admin';
    import { can } from '../../../lib/stores/capabilities';
    import { relativeTime } from '../../../lib/utils/relative-time';
    import { visiblePoll } from '../../../lib/utils/visible-poll';
    import NotificationsRail from './NotificationsRail.svelte';
    import AdminDashboardButton from './admin/AdminDashboardButton.svelte';

    $: lang = $localeStore;
    $: railAriaLabel = tr(lang, m.dashboard_activity_rail_aria_label());
    $: heading = tr(lang, m.dashboard_activity_heading());
    $: eventCount = tr(lang, m.dashboard_activity_event_count({ count: cards.length }));
    $: loadingLabel = tr(lang, m.common_state_loading());
    $: emptyLabel = tr(lang, m.dashboard_activity_empty());
    $: deleteAriaLabel = tr(lang, m.dashboard_activity_delete_aria_label());
    $: deleteTitle = tr(lang, m.dashboard_activity_delete_title());

    // Capability-gated affordances (both default owner-only, so this matches
    // the prior `$isOwner` behavior — but now reflects an owner's toggle).
    const canDelete = can('activity.delete');
    const canSeeActor = can('identity.see_actor');

    let cards: PublicActivityCard[] = [];
    let loading = true;
    let error: string | null = null;
    let teardown: (() => void) | null = null;

    onMount(() => {
        teardown = visiblePoll<{ cards: PublicActivityCard[] }>({
            intervalMs: 30_000,
            fetcher: (signal) => fetchPublicActivity({ limit: 50, signal }),
            onResult: (page) => {
                cards = page.cards;
                loading = false;
                error = null;
            },
            onError: (e) => {
                loading = false;
                error = (e as Error).message ?? m.dashboard_activity_load_error_fallback();
            },
        });
    });

    onDestroy(() => teardown?.());

    function dotClass(kind: PublicActivityCard['kind']): string {
        return `marker marker-${kind.replace(/_/g, '-')}`;
    }

    const ACTION: Record<PublicEventKind, () => string> = {
        added: m.dashboard_activity_action_added,
        requested: m.dashboard_activity_action_requested,
        available_for_review: m.dashboard_activity_action_available_for_review,
        under_review: m.dashboard_activity_action_under_review,
        published: m.dashboard_activity_action_published,
    };

    function formatLine(card: PublicActivityCard): string {
        const displayName = ['ar', 'ckb'].includes(lang) && card.name_ar ? card.name_ar : card.name;
        if (card.riwayah && card.style) {
            return `${displayName} (${vocabLabel('riwayah', card.riwayah)}) (${vocabLabel('style', card.style)}) ${ACTION[card.kind]()}`;
        }
        return card.text;
    }
    // Re-run formatLine in markup when the locale switches.
    $: formatLineForLang = (card: PublicActivityCard): string => tr(lang, formatLine(card));

    async function onDelete(card: PublicActivityCard): Promise<void> {
        if (!card.audit_id) return;
        const reason = window.prompt(m.dashboard_activity_delete_prompt(), '');
        if (reason === null) return; // cancelled
        const trimmed = reason.trim();
        if (trimmed.length < 10) {
            window.alert(m.dashboard_activity_delete_reason_too_short());
            return;
        }
        const idx = cards.findIndex((c) => c.audit_id === card.audit_id);
        const removed = cards[idx];
        if (idx === -1 || removed === undefined) return;
        cards = [...cards.slice(0, idx), ...cards.slice(idx + 1)];
        try {
            await deletePublicActivity(card.audit_id, trimmed);
        } catch (e) {
            cards = [...cards.slice(0, idx), removed, ...cards.slice(idx)];
            error = (e as Error).message ?? m.dashboard_activity_delete_error_fallback();
        }
    }
</script>

<div class="rail-wrap">
    <AdminDashboardButton />

    <div class="rail-scroll">
        <NotificationsRail />

        <aside class="activity" aria-label={railAriaLabel}>
        <header>
            <h2>{heading}</h2>
            <span class="sub">{eventCount}</span>
        </header>

        {#if loading}
            <div class="state">{loadingLabel}</div>
        {:else if error}
            <div class="state error">{error}</div>
        {:else if cards.length === 0}
            <div class="state">{emptyLabel}</div>
        {:else}
            <ol class="list">
                {#each cards as card (card.audit_id ?? card.ts + card.kind + card.name)}
                    <li class="item" class:has-delete={$canDelete && card.audit_id}>
                        <span class={dotClass(card.kind)} aria-hidden="true"></span>
                        <div class="body">
                            <p class="text">{formatLineForLang(card)}</p>
                            <time class="time" datetime={card.ts}>
                                {#if $canSeeActor && card.actor_login}
                                    <span class="actor">@{card.actor_login}</span>
                                    <span class="time-sep">·</span>
                                {/if}
                                {relativeTime(card.ts)}
                            </time>
                        </div>
                        {#if $canDelete && card.audit_id}
                            <button
                                class="delete"
                                type="button"
                                aria-label={deleteAriaLabel}
                                title={deleteTitle}
                                on:click={() => onDelete(card)}
                            >
                                🗑
                            </button>
                        {/if}
                    </li>
                {/each}
            </ol>
        {/if}
        </aside>
    </div>
</div>

<style>
    /* Match the catalog's shared-height envelope so all three dashboard columns
       end on the same bottom line: the admin button stays pinned at the top, and
       the notifications rail + activity feed share a single scroll container
       below it (one scrollbar, no two regions fighting for height). Reverts to
       natural flow once the column drops out of the 3-up grid (≤1280px). */
    .rail-wrap {
        position: sticky;
        top: 0;
        align-self: start;
        height: var(--catalog-h);
        display: flex;
        flex-direction: column;
        min-height: 0;
    }
    .rail-scroll {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
    }
    .activity {
        padding: var(--s-5) var(--s-5);
        border-inline-start: 1px solid var(--border-quiet);
    }
    @media (max-width: 1280px) {
        .rail-wrap { height: auto; }
        .rail-scroll { overflow-y: visible; }
    }
    header {
        display: flex;
        align-items: baseline;
        gap: var(--s-2);
        margin-bottom: var(--s-4);
    }
    header h2 {
        font-size: var(--fs-h3);
        color: var(--text-primary);
        font-weight: 500;
        margin: 0;
    }
    .sub {
        font-size: var(--fs-meta);
        color: var(--text-muted);
    }
    .state {
        padding: var(--s-8) 0;
        text-align: center;
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }
    .state.error { color: var(--state-error-fg); }

    .list {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-direction: column;
    }
    .item {
        display: grid;
        grid-template-columns: 14px 1fr;
        gap: var(--s-3);
        padding: var(--s-3) 0;
        border-bottom: 1px solid var(--border-quiet);
    }
    .item.has-delete {
        grid-template-columns: 14px 1fr 24px;
    }
    .item:last-child { border-bottom: none; }
    .marker {
        width: 8px; height: 8px;
        border-radius: 50%;
        margin-top: 6px;
        background: var(--border-strong);
    }
    .marker-added             { background: var(--state-available-request-fg); }
    .marker-requested         { background: var(--state-requested-fg); }
    .marker-available-review  { background: var(--state-available-fg); }
    .marker-under-review      { background: var(--state-under-review-fg); }
    .marker-published         { background: var(--state-published-fg); }

    .body { min-width: 0; }
    .text {
        margin: 0;
        font-size: var(--fs-body);
        color: var(--text-secondary);
        line-height: var(--lh-normal);
    }
    .time {
        display: block;
        margin-top: 2px;
        font-size: 10.5px;
        color: var(--text-faint);
        font-variant-numeric: tabular-nums;
    }
    .actor {
        color: var(--text-muted);
        font-weight: 500;
    }
    .time-sep {
        margin: 0 4px;
        color: var(--text-faint);
    }
    .delete {
        background: transparent;
        border: 0;
        color: var(--text-muted);
        font-size: 14px;
        line-height: 1;
        padding: 0 2px;
        cursor: pointer;
        opacity: 0;
        transition: opacity var(--t-fast), color var(--t-fast);
        align-self: start;
        margin-top: 4px;
    }
    .item:hover .delete { opacity: 1; }
    .delete:hover { color: var(--state-error-fg); }
</style>
