<script lang="ts">
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import type { JoinVerdict } from '../../../../lib/types/generated/schemas';

    let { answers = [] }: { answers?: JoinVerdict[] | null } = $props();
    const wasl = $derived(tr($localeStore, m.segments_history_wasl_tag()));
    const waqf = $derived(tr($localeStore, m.segments_history_waqf_tag()));
</script>

{#if answers?.length}
    <div class="join-verdicts">
        {#each answers as answer (`${answer.at_ms}:${answer.after_ref}`)}
            <span><bdi>{answer.after_ref}</bdi> · {answer.verdict === 'wasl' ? wasl : waqf}</span>
        {/each}
    </div>
{/if}

<style>
    .join-verdicts {
        display: flex;
        flex-wrap: wrap;
        gap: var(--s-2);
        padding: var(--s-1) var(--s-3);
        color: var(--text-secondary);
        font-size: var(--fs-meta);
    }
</style>
