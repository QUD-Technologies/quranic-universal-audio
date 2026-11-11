<script lang="ts">
    /**
     * Secondary facet rail rendered to the left of the reciter list.
     * Axes come from the schema descriptor; no taxonomy literals here.
     * Counts come from a parent-computed perFacetCounts map (the same
     * shape `facets.ts::recomputeFacets` returns).
     */
    import { createEventDispatcher } from 'svelte';

    import { localeStore, tr } from '$lib/i18n/locale-store';
    import { vocabLabel, type VocabKind } from '$lib/i18n/vocab';
    import * as m from '$lib/paraglide/messages';

    import type { Axis, AxisOption } from '../../catalog/schema-descriptor';
    import { PUBLIC_BUCKET_LABELS, type PublicBucket } from '../../types/public-bucket';
    import { qualityTagLabel } from '../../utils/quality-status';
    import { reviewTagLabel } from '../../utils/review-signals';
    import FilterPill from '../FilterPill.svelte';

    export let axes: Axis[];
    export let activeFilters: Record<string, Set<string>>;
    export let perFacetCounts: Record<string, Record<string, number>>;

    const dispatch = createEventDispatcher<{ toggle: { axis: string; tag: string } }>();

    // Axis header + pill labels are resolved at render time (not from the baked
    // descriptor) so they re-translate on a locale switch.
    const AXIS_LABEL: Record<string, () => string> = {
        status: m.dashboard_facet_axis_status,
        riwayah: m.dashboard_facet_axis_riwayah,
        style: m.dashboard_facet_axis_style,
        coverage: m.dashboard_facet_axis_coverage,
        recording_context: m.dashboard_facet_axis_recording_context,
        channel: m.dashboard_facet_axis_channel,
        quality: m.common_quality_label,
        review: m.common_review_label,
    };
    const VOCAB_AXIS: Record<string, VocabKind> = {
        riwayah: 'riwayah',
        style: 'style',
        recording_context: 'context',
        coverage: 'coverage',
    };

    function axisLabelOf(axis: Axis): string {
        return AXIS_LABEL[axis.key]?.() ?? axis.label;
    }
    // Riwayah/style/context/coverage → vocab translation; status → bucket labels;
    // channel keeps its Latin channel_name (baked option.label).
    function pillLabelOf(axis: Axis, option: AxisOption): string {
        const kind = VOCAB_AXIS[axis.key];
        if (kind) return vocabLabel(kind, option.key);
        if (axis.key === 'quality') return qualityTagLabel(option.key);
        if (axis.key === 'review') return reviewTagLabel(option.key);
        if (axis.key === 'status') return PUBLIC_BUCKET_LABELS[option.key as PublicBucket]?.() ?? option.label;
        return option.label;
    }

    $: filtersAriaLabel = tr($localeStore, m.common_picker_filters_aria_label());
</script>

<aside class="rail" aria-label={filtersAriaLabel}>
    {#each axes as axis (axis.key)}
        {@const counts = perFacetCounts[axis.key] ?? {}}
        {@const active = activeFilters[axis.key]}
        <div class="facet">
            <span class="facet-label">{tr($localeStore, axisLabelOf(axis))}</span>
            <div class="pills">
                {#each axis.options as option (option.key)}
                    {@const count = counts[option.key] ?? 0}
                    <FilterPill
                        label={tr($localeStore, pillLabelOf(axis, option))}
                        count={count}
                        active={active?.has(option.key) ?? false}
                        empty={count === 0}
                        on:click={() => dispatch('toggle', { axis: axis.key, tag: option.key })}
                    />
                {/each}
            </div>
        </div>
    {/each}
</aside>

<style>
    .rail {
        padding: var(--s-5) var(--s-4);
        border-inline-end: 1px solid var(--border-quiet);
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: var(--s-5);
    }
    .facet { display: flex; flex-direction: column; gap: var(--s-2); }
    .facet-label {
        font-size: var(--fs-meta);
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: var(--s-1);
    }
    .pills {
        display: flex;
        flex-wrap: wrap;
        gap: var(--s-1);
    }
</style>
