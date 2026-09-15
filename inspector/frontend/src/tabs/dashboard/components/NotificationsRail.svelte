<script lang="ts">
    /**
     * "My Notifications" rail — sits above Recent activity, under the
     * admin-dashboard button. Two sources, rendered as one list:
     *
     * - Global announcements (public; shown to everyone incl. anonymous) — an
     *   owner broadcast. Dismiss is client-side (localStorage); no archive.
     * - Per-user notifications (signed-in only) — events that happened to the
     *   user (request sent back / discarded, reciter ready, assigned, claim
     *   force-released, segment-flag reply). Dismissable → archived.
     *
     * In the Active view both sources merge, newest-first, styled identically.
     * The Active/Archive toggle + archive list are signed-in-only (announcements
     * never archive). The whole rail shows for anonymous users whenever at least
     * one announcement is active. Polls every 30s while visible.
     */
    import { onDestroy, onMount } from 'svelte';

    import { i18n } from '../../../lib/i18n/locale.svelte';
    import * as m from '../../../lib/paraglide/messages';
    import type { UserNotification } from '../../../lib/api/notifications';
    import Icon from '../../../lib/icons/Icon.svelte';
    import { can } from '../../../lib/stores/capabilities';
    import { currentUser, isSignedIn } from '../../../lib/stores/current-user';
    import { gotoSegments } from '../../../lib/utils/goto-segments';
    import { gotoTimestamps } from '../../../lib/utils/goto-timestamps';
    import { relativeTime } from '../../../lib/utils/relative-time';
    import EmailPrefsModal from './EmailPrefsModal.svelte';
    import { announcements } from '../stores/announcements.svelte';
    import { resolveDeliverySlug } from '../stores/catalog-data';
    import { openDetail } from '../stores/dashboard-state';
    import { notifications } from '../stores/notifications.svelte';

    const signedIn = $derived(isSignedIn($currentUser));
    const canEmail = can('notify.email_subscriptions');
    const canSeeReporter = can('timestamps.see_reporter_identity');
    // The rail is the email-subscribe entry point for everyone (incl. anonymous),
    // so it shows whenever email is available — not only for signed-in users or
    // when an announcement is active.
    const visible = $derived(signedIn || announcements.active.length > 0 || $canEmail);

    let prefsOpen = $state(false);
    let manageToken = $state<string | null>(null);

    // An email "manage" / unsubscribe link deep-links back as `?manage=<token>`.
    // Open the modal seeded by that token, then strip the param so a refresh
    // doesn't reopen it.
    onMount(() => {
        try {
            const params = new URLSearchParams(window.location.search);
            const token = params.get('manage');
            if (token) {
                manageToken = token;
                prefsOpen = true;
                params.delete('manage');
                const qs = params.toString();
                window.history.replaceState(
                    {},
                    '',
                    window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash,
                );
            }
        } catch {
            /* no-op — deep-link is best-effort */
        }
    });

    onMount(() => announcements.start());
    onDestroy(() => {
        announcements.stop();
        notifications.stop();
    });

    // Start the per-user poll once we know the user is signed in. `$currentUser`
    // loads async (from /api/me), so `signedIn` is usually still false at mount —
    // gating start() on a mount-time snapshot would leave `loading` true forever
    // (the "infinite Loading…" with no notifications). `start()` is idempotent.
    $effect(() => {
        if (signedIn) notifications.start();
    });

    /** Unified card shape so announcements + notifications render identically. */
    interface RailCard {
        key: string;
        title: string;
        body: string | null;
        badge: string | null;
        created_at: string;
        unseen: boolean;
        nav: { label: string; go: () => void } | null;
        dismiss: () => void;
    }

    function openReciter(n: UserNotification): void {
        if (!n.slug) return;
        const resolved = resolveDeliverySlug(n.slug);
        if (resolved) openDetail(resolved.reciter.reciter_id, n.slug);
    }

    /**
     * Where clicking a notification should take the user, with an explicit
     * label so the destination is transparent before they click.
     */
    function navTarget(n: UserNotification): { label: string; go: () => void } | null {
        const slug = n.slug;
        switch (n.event) {
            case 'request.received':
                return null; // informational — owner reviews from the Requests tab
            case 'reciter.marked_ready':
                return slug
                    ? {
                          label: m.dashboard_notifs_nav_review_submission(),
                          go: () => gotoSegments(slug, { openMarkReadyReview: true }),
                      }
                    : null;
            case 'reciter.alignment_completed':
            case 'reciter.claimed':
                return slug
                    ? { label: m.dashboard_notifs_nav_review_in_segments(), go: () => gotoSegments(slug) }
                    : null;
            case 'flag.reply':
            case 'flag.created':
            case 'flag.replied': {
                if (!slug) return null;
                const uid =
                    typeof n.payload?.segment_uid === 'string' ? n.payload.segment_uid : undefined;
                return {
                    label: m.dashboard_notifs_nav_open_flagged_segment(),
                    go: () => gotoSegments(slug, { openFlagged: true, focusFlaggedUid: uid }),
                };
            }
            case 'ts_report.created':
            case 'ts_report.resolved':
            case 'ts_report.auto_resolved': {
                const verseKey =
                    typeof n.payload?.verse_key === 'string' ? n.payload.verse_key : '';
                if (!slug || !verseKey) return null;
                return {
                    label: m.dashboard_notifs_nav_view_flagged_verse(),
                    go: () => gotoTimestamps(slug, verseKey),
                };
            }
            default:
                return slug && resolveDeliverySlug(slug)
                    ? { label: m.dashboard_notifs_nav_view_reciter(), go: () => openReciter(n) }
                    : null;
        }
    }

    /** A short type pill for a notification (the request kind, a flag/reply
     *  marker, or a "has notes" marker). Announcements carry none. */
    function kindBadgeLabel(kind: unknown): string | null {
        switch (kind) {
            case 'existing_combo_edit':
                return m.dashboard_notifs_badge_edit_existing_combo();
            case 'existing_reciter_new_combo':
                return m.dashboard_notifs_badge_new_riwayah_style();
            case 'new_reciter':
                return m.dashboard_notifs_badge_new_reciter();
            default:
                return null;
        }
    }

    function cardBadge(n: UserNotification): string | null {
        switch (n.event) {
            case 'request.received':
                return kindBadgeLabel(n.payload?.kind);
            case 'reciter.marked_ready':
                return m.dashboard_notifs_badge_has_notes();
            case 'flag.created':
                return m.dashboard_notifs_badge_flag_comment();
            case 'flag.replied':
                return m.dashboard_notifs_badge_flag_reply();
            case 'ts_report.created':
                return m.dashboard_notifs_badge_timestamps_report();
            case 'ts_report.resolved':
            case 'ts_report.auto_resolved':
                return m.dashboard_notifs_badge_timestamps_resolved();
            case 'shard.missing':
                return m.dashboard_notifs_badge_shard_missing();
            default:
                return null;
        }
    }

    /** Card body — for a timestamps report, identity-capable owners also see who
     *  reported it (appended from the payload). Everyone else sees the verse +
     *  category only. */
    function cardBody(n: UserNotification, showReporter: boolean): string | null {
        if (n.event === 'ts_report.created' && showReporter) {
            const login =
                typeof n.payload?.author_login === 'string' ? n.payload.author_login : null;
            const who = login ?? m.dashboard_notifs_reporter_anonymous();
            return n.body
                ? m.dashboard_notifs_reported_by_with_body({ body: n.body, who })
                : m.dashboard_notifs_reported_by({ who });
        }
        return n.body;
    }

    /** Active view: announcements + personal notifications merged, newest-first. */
    const activeCards = $derived<RailCard[]>(
        // Reading i18n.locale re-derives card labels/badges/bodies on locale switch.
        (i18n.locale,
        [
            ...announcements.active.map(
                (a): RailCard => ({
                    key: `ann-${a.id}`,
                    title: a.title,
                    body: a.body ?? null,
                    badge: null,
                    created_at: a.created_at,
                    unseen: announcements.isNew(a.id),
                    nav: null,
                    dismiss: () => announcements.dismiss(a.id),
                }),
            ),
            ...notifications.active.map(
                (n): RailCard => ({
                    key: `notif-${n.id}`,
                    title: n.title,
                    body: cardBody(n, $canSeeReporter),
                    badge: cardBadge(n),
                    created_at: n.created_at,
                    unseen: !n.seen_at,
                    nav: navTarget(n),
                    dismiss: () => notifications.dismiss(n.id),
                }),
            ),
        ].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))),
    );

    const badge = $derived(announcements.unread + notifications.unread);
</script>

{#if visible}
    <!-- Re-render the whole rail on a locale switch: its labels are read via
         m.*() across many branches and helper returns (navTarget / cardBadge),
         so one locale dependency here is simpler and safer than threading one
         through each call site. -->
    {#key i18n.locale}
    <aside class="notifs" aria-label={m.dashboard_notifs_rail_aria_label()}>
        <header>
            <h2>{m.dashboard_notifs_rail_heading()}</h2>
            {#if $canEmail}
                <button
                    type="button"
                    class="email-btn"
                    title={m.dashboard_notifs_email_button_title()}
                    onclick={() => (prefsOpen = true)}>
                    <Icon name="mail" size={14} />
                    <span>{m.dashboard_notifs_email_button_label()}</span>
                </button>
            {/if}
            {#if badge > 0 && notifications.view === 'active'}
                <span class="badge" aria-label={m.dashboard_notifs_unread_aria({ count: badge })}
                    >{badge}</span>
            {/if}
            {#if signedIn}
                <div class="toggle" role="tablist" aria-label={m.dashboard_notifs_view_aria()}>
                    <button
                        type="button"
                        role="tab"
                        aria-selected={notifications.view === 'active'}
                        class:on={notifications.view === 'active'}
                        onclick={() => notifications.setView('active')}
                        >{m.dashboard_notifs_view_active()}</button>
                    <button
                        type="button"
                        role="tab"
                        aria-selected={notifications.view === 'archive'}
                        class:on={notifications.view === 'archive'}
                        onclick={() => notifications.setView('archive')}
                        >{m.dashboard_notifs_view_archive()}</button>
                </div>
            {/if}
        </header>

        {#if notifications.view === 'archive'}
            {#if notifications.error}
                <div class="state error">{notifications.error}</div>
            {:else if notifications.archived.length === 0}
                <div class="state">{m.dashboard_notifs_archive_empty()}</div>
            {:else}
                <ol class="list">
                    {#each notifications.archived as n (n.id)}
                        {@const target = navTarget(n)}
                        {@const bdg = cardBadge(n)}
                        {@const body = cardBody(n, $canSeeReporter)}
                        <li class="item">
                            <div class="body-wrap">
                                <p class="title">
                                    {n.title}{#if bdg}<span class="kind">{bdg}</span>{/if}
                                </p>
                                {#if body}
                                    <p class="body">{body}</p>
                                {/if}
                                <time class="time" datetime={n.created_at}
                                    >{relativeTime(n.created_at)}</time>
                            </div>
                            <div class="actions">
                                {#if target}
                                    <button
                                        class="act"
                                        type="button"
                                        aria-label={target.label}
                                        title={target.label}
                                        onclick={target.go}>↗</button>
                                {/if}
                                <button
                                    class="act"
                                    type="button"
                                    aria-label={m.dashboard_notifs_restore_aria()}
                                    title={m.dashboard_notifs_restore_title()}
                                    onclick={() => notifications.restore(n.id)}>↩</button>
                            </div>
                        </li>
                    {/each}
                </ol>
            {/if}
        {:else if notifications.error}
            <div class="state error">{notifications.error}</div>
        {:else if signedIn && notifications.loading && activeCards.length === 0}
            <div class="state">{m.common_state_loading()}</div>
        {:else if activeCards.length === 0}
            <div class="state">{m.dashboard_notifs_empty()}</div>
        {:else}
            <ol class="list">
                {#each activeCards as c (c.key)}
                    <li class="item" class:unseen={c.unseen}>
                        <div class="body-wrap">
                            <p class="title">
                                {c.title}{#if c.badge}<span class="kind">{c.badge}</span>{/if}
                            </p>
                            {#if c.body}
                                <p class="body">{c.body}</p>
                            {/if}
                            <time class="time" datetime={c.created_at}>{relativeTime(c.created_at)}</time>
                        </div>
                        <div class="actions">
                            {#if c.nav}
                                <button
                                    class="act"
                                    type="button"
                                    aria-label={c.nav.label}
                                    title={c.nav.label}
                                    onclick={c.nav.go}>↗</button>
                            {/if}
                            <button
                                class="act"
                                type="button"
                                aria-label={m.common_action_dismiss()}
                                title={m.common_action_dismiss()}
                                onclick={c.dismiss}>✕</button>
                        </div>
                    </li>
                {/each}
            </ol>
        {/if}
    </aside>
    {/key}
{/if}

<EmailPrefsModal {manageToken} open={prefsOpen} onclose={() => (prefsOpen = false)} />

<style>
    .notifs {
        padding: var(--s-5) var(--s-5);
        border-inline-start: 1px solid var(--border-quiet);
        border-bottom: 1px solid var(--border-quiet);
    }
    header {
        display: flex;
        align-items: center;
        gap: var(--s-2);
        margin-bottom: var(--s-4);
    }
    header h2 {
        /* One notch below the rail's other headings so the title + Email button
           + Active/Archive toggle fit the 320px column without overflowing. */
        font-size: var(--fs-row);
        color: var(--text-primary);
        font-weight: 500;
        margin: 0;
        white-space: nowrap;
    }
    .email-btn {
        margin-inline-start: auto;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 3px 10px;
        background: transparent;
        border: 1px solid var(--border-default);
        border-radius: var(--r-pill, 999px);
        color: var(--text-secondary);
        font-size: var(--fs-meta);
        cursor: pointer;
        white-space: nowrap;
        transition: color var(--t-fast), background var(--t-fast), border-color var(--t-fast);
    }
    .email-btn:hover {
        color: var(--text-primary);
        border-color: var(--border-strong);
        background: var(--panel-2);
    }
    .email-btn:focus-visible {
        outline: none;
        box-shadow: 0 0 0 2px var(--accent);
    }
    .badge {
        font-size: 10px;
        font-weight: 600;
        line-height: 1;
        padding: 2px 6px;
        border-radius: 999px;
        background: var(--state-requested-fg);
        color: var(--ink-on-color);
    }
    .toggle {
        margin-inline-start: auto;
        display: inline-flex;
        gap: 2px;
    }
    .toggle button {
        background: transparent;
        border: 0;
        font-size: var(--fs-meta);
        color: var(--text-muted);
        padding: 2px 6px;
        cursor: pointer;
        border-radius: var(--radius-sm, 4px);
    }
    .toggle button.on {
        color: var(--text-primary);
        background: var(--surface-raised, var(--border-quiet));
    }
    .state {
        padding: var(--s-6) 0;
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
        grid-template-columns: 1fr auto;
        gap: var(--s-2);
        padding: var(--s-3) 0;
        border-bottom: 1px solid var(--border-quiet);
        align-items: start;
    }
    .item:last-child { border-bottom: none; }
    .item.unseen .title { font-weight: 600; color: var(--text-primary); }

    .body-wrap {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
    }
    .title {
        margin: 0;
        font-size: var(--fs-body);
        color: var(--text-secondary);
        line-height: var(--lh-normal);
    }
    .kind {
        display: inline-block;
        margin-inline-start: 6px;
        font-size: 10px;
        font-weight: 500;
        line-height: 1.4;
        padding: 1px 7px;
        border-radius: 999px;
        background: var(--surface-raised, var(--border-quiet));
        color: var(--text-muted);
        vertical-align: middle;
        white-space: nowrap;
    }
    .body {
        margin: 0;
        font-size: var(--fs-meta);
        color: var(--text-muted);
        line-height: var(--lh-normal);
        white-space: pre-wrap;
    }
    .time {
        font-size: 10.5px;
        color: var(--text-faint);
        font-variant-numeric: tabular-nums;
    }
    .actions {
        display: flex;
        align-items: center;
        gap: 2px;
    }
    .act {
        background: transparent;
        border: 0;
        color: var(--text-muted);
        font-size: 13px;
        line-height: 1;
        padding: 2px 3px;
        cursor: pointer;
        border-radius: var(--radius-sm, 4px);
        transition: color var(--t-fast), background var(--t-fast);
    }
    .act:hover {
        color: var(--text-primary);
        background: var(--surface-raised, var(--border-quiet));
    }
</style>
