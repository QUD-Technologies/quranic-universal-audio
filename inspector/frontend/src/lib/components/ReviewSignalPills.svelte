<script lang="ts">
    /**
     * Pills on a reciter-picker row: the delivery's open Low Confidence items
     * (under the default threshold plus open Low Confidence v2 items, the
     * tooltip splitting the two) and its open flags. Each renders nothing when
     * its count is zero (or the counts are not in yet).
     */
    import { localizeDigits } from '$lib/i18n/format';
    import * as m from '$lib/paraglide/messages';

    import { i18n } from '../i18n/locale.svelte';
    import type { SegReviewSignal } from '../types/generated/schemas';
    import { lowConfidenceTotal } from '../utils/review-signals';

    let { signal }: { signal: SegReviewSignal | undefined } = $props();

    const total = $derived(lowConfidenceTotal(signal));
    const flagged = $derived(signal?.flagged ?? 0);
    const flagLabel = $derived(
        (i18n.locale, m.common_review_pill_flagged({ count: localizeDigits(flagged) })),
    );
    const flagHint = $derived((i18n.locale, m.common_review_pill_flagged_hint()));
    // Read the ambient locale so labels re-render on a locale switch.
    const label = $derived((i18n.locale, m.common_review_pill_low({ count: localizeDigits(total) })));
    const hint = $derived(
        (i18n.locale,
        m.common_review_pill_low_hint({
            under: localizeDigits(signal?.low_confidence ?? 0),
            v2: localizeDigits(signal?.low_confidence_v2 ?? 0),
        })),
    );
</script>

{#if total > 0}
    <span class="review-pill" title={hint}>{label}</span>
{/if}
{#if flagged > 0}
    <span class="review-pill flag" title={flagHint}>{flagLabel}</span>
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
    .review-pill.flag {
        color: var(--bad-fg);
        background: var(--bad-tint);
    }
</style>
