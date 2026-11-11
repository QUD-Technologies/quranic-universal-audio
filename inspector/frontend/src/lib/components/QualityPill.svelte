<script lang="ts">
    /**
     * Small coloured pill for a delivery's recitation quality status.
     * Traffic-light tokens: clean = ok, almost clean = warn, needs work = bad.
     * Renders nothing for an unrated delivery.
     */
    import { i18n } from '../i18n/locale.svelte';
    import type { QualityStatus } from '../types/generated/schemas';
    import { qualityHint, qualityLabel } from '../utils/quality-status';

    let { status }: { status: QualityStatus | null | undefined } = $props();

    // Read the ambient locale so labels re-render on a locale switch.
    const label = $derived(status ? (i18n.locale, qualityLabel(status)) : '');
    const hint = $derived(status ? (i18n.locale, qualityHint(status)) : '');
</script>

{#if status}
    <span class="quality-pill {status}" title={hint}>{label}</span>
{/if}

<style>
    .quality-pill {
        display: inline-flex;
        align-items: center;
        padding: 1px 7px;
        border-radius: var(--r-pill);
        border: 1px solid currentColor;
        font-size: 10px;
        line-height: 1.5;
        white-space: nowrap;
        flex-shrink: 0;
    }

    .clean {
        color: var(--ok-fg);
        background: var(--ok-tint);
    }

    .almost_clean {
        color: var(--warn-fg);
        background: var(--warn-tint);
    }

    .needs_work {
        color: var(--bad-fg);
        background: var(--bad-tint);
    }
</style>
