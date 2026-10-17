<script lang="ts">
    import StatePill from '../../../lib/components/StatePill.svelte';
    import DeliveryAlignControl from './DeliveryAlignControl.svelte';
    import type { PublicDelivery } from '../../../lib/types/generated/schemas';

    export let delivery: PublicDelivery;
    export let isAdmin = false;
    export let isOwner = false;
    export let busy = false;
    export let requestLabel: string;
    export let reviewRequestTitle: string;
    export let claimReviewTitle: string;
    export let claimReviewButtonLabel: string;
    export let discardLabel: string;
    export let discardTitle: string;
    export let onRequest: (delivery: PublicDelivery) => void;
    export let onReview: (delivery: PublicDelivery) => void;
    export let onClaimReview: (delivery: PublicDelivery) => void;
    export let onDiscard: (delivery: PublicDelivery) => void;

    $: canDiscard = isOwner && [
        'available_for_review',
        'under_review',
        'published',
    ].includes(delivery.bucket);
</script>

<div class="status-actions">
    {#if delivery.bucket === 'available_for_request'}
        <button
            type="button"
            class="request-btn"
            disabled={busy}
            on:click|stopPropagation={() => onRequest(delivery)}
        >{requestLabel}</button>
    {:else if delivery.bucket === 'requested' && isAdmin}
        <button
            type="button"
            class="pill-as-btn"
            title={reviewRequestTitle}
            disabled={busy}
            on:click|stopPropagation={() => onReview(delivery)}
        ><StatePill state={delivery.bucket} size="sm" /></button>
    {:else if delivery.bucket === 'available_for_review'}
        <button
            type="button"
            class="request-btn"
            title={claimReviewTitle}
            disabled={busy}
            on:click|stopPropagation={() => onClaimReview(delivery)}
        >{claimReviewButtonLabel}</button>
    {:else}
        <StatePill state={delivery.bucket} size="sm" />
    {/if}

    {#if delivery.bucket === 'requested'}
        <DeliveryAlignControl slug={delivery.slug} />
    {/if}

    {#if canDiscard}
        <button
            type="button"
            class="discard-btn"
            title={discardTitle}
            aria-label={discardTitle}
            disabled={busy}
            on:click|stopPropagation={() => onDiscard(delivery)}
        >{discardLabel}</button>
    {/if}
</div>

<style>
    .status-actions {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        flex-wrap: wrap;
    }
    .discard-btn {
        background: var(--btn-discard-bg);
        border: 1px solid var(--btn-discard-border);
        color: var(--btn-discard-fg);
        border-radius: var(--r-2);
        padding: 3px 8px;
        font-size: 11px;
        cursor: pointer;
    }
    .request-btn {
        background: var(--state-available-request-bg);
        color: var(--state-available-request-fg);
        border: 1px solid var(--state-available-request-fg);
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 11px;
        cursor: pointer;
        transition: background var(--t-fast), color var(--t-fast);
    }
    .request-btn:hover:not(:disabled) {
        background: var(--state-available-request-fg);
        color: var(--canvas);
    }
    .request-btn:disabled {
        opacity: 0.55;
        cursor: wait;
    }
    .pill-as-btn {
        background: transparent;
        border: 0;
        padding: 0;
        cursor: pointer;
    }
    .pill-as-btn:disabled {
        opacity: 0.55;
        cursor: wait;
    }
    .discard-btn:hover:not(:disabled) {
        background: var(--btn-discard-bg-hover);
    }
    .discard-btn:disabled {
        opacity: 0.55;
        cursor: wait;
    }
</style>
