<script lang="ts">
    /**
     * Small warn pills on a reciter-picker row: open Low Confidence (under the
     * default threshold) and Low Confidence v2 items. Renders nothing when the
     * delivery has none (or its counts are not in yet).
     */
    import { localizeDigits } from '$lib/i18n/format';
    import * as m from '$lib/paraglide/messages';

    import { i18n } from '../i18n/locale.svelte';
    import type { SegReviewSignal } from '../types/generated/schemas';

    let { signal }: { signal: SegReviewSignal | undefined } = $props();

    const low = $derived(signal?.low_confidence ?? 0);
    const lowV2 = $derived(signal?.low_confidence_v2 ?? 0);
    // Read the ambient locale so labels re-render on a locale switch.
    const lowLabel = $derived((i18n.locale, m.common_review_pill_low({ count: localizeDigits(low) })));
    const lowHint = $derived((i18n.locale, m.common_review_pill_low_hint({ count: localizeDigits(low) })));
    const v2Label = $derived((i18n.locale, m.common_review_pill_low_v2({ count: localizeDigits(lowV2) })));
    const v2Hint = $derived((i18n.locale, m.common_review_pill_low_v2_hint({ count: localizeDigits(lowV2) })));
</script>

{#if low > 0}
    <span class="review-pill" title={lowHint}>{lowLabel}</span>
{/if}
{#if lowV2 > 0}
    <span class="review-pill" title={v2Hint}>{v2Label}</span>
{/if}

<style>
    .review-pill {
        display: inline-flex;
        align-items: center;
        padding: 1px 7px;
        border-radius: var(--r-pill);
        border: 1px solid currentColor;
        font-size: 10px;
        line-height: 1.5;
        white-space: nowrap;
        flex-shrink: 0;
        color: var(--warn-fg);
        background: var(--warn-tint);
    }
</style>
