<script lang="ts">
    /**
     * Review-status chip for a combination-picker row.
     *
     * Two facts for an ``under_review`` recitation: whether it's marked ready
     * for review (amber badge — public, shown to everyone) and who holds the
     * open claim (initials avatar + login — only handed in for callers who hold
     * ``reviews.view``; ``login`` is null for everyone else).
     *
     * Renders nothing when there's neither a claimer nor a ready flag.
     */
    import { i18n } from '../../i18n/locale.svelte';
    import * as m from '../../paraglide/messages';
    import { initials } from '../../utils/initials';

    let { login, markedReady }: { login: string | null; markedReady: boolean } = $props();

    const hasReviewer = $derived(login !== null && login !== '');
    // Read the ambient locale so the chrome below re-renders on switch.
    const claimedByTitle = $derived((i18n.locale, m.common_picker_review_claimed_by_title({ login: login ?? '' })));
</script>

{#if hasReviewer || markedReady}
    <div class="review-status">
        {#if markedReady}
            <span class="ready-badge" title={m.common_picker_review_ready_title()}>{m.common_picker_review_ready_badge()}</span>
        {/if}
        {#if hasReviewer}
            <span class="reviewer" title={claimedByTitle}>
                <span class="avatar">{initials(login)}</span>
                <span class="who">{login}</span>
            </span>
        {/if}
    </div>
{/if}

<style>
    .review-status {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        min-width: 0;
        flex-shrink: 0;
    }

    /* Amber accent mirrors the Reviews tab's marked-ready section mark. */
    .ready-badge {
        display: inline-flex;
        align-items: center;
        padding: 1px 7px;
        border-radius: 999px;
        background: oklch(0.84 0.130 70 / 0.16);
        color: oklch(0.84 0.130 70);
        border: 1px solid oklch(0.84 0.130 70 / 0.45);
        font-size: 10px;
        font-family: var(--font-mono);
        letter-spacing: 0.04em;
        text-transform: uppercase;
        white-space: nowrap;
    }

    .reviewer {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        min-width: 0;
        color: var(--text-secondary);
        font-size: var(--fs-meta);
    }
    .reviewer .avatar {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: var(--accent-tint);
        color: var(--accent-strong);
        font-size: 9px;
        font-weight: 600;
        flex-shrink: 0;
    }
    .reviewer .who {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 120px;
    }
</style>
