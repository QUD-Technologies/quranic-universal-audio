<script lang="ts">
    /**
     * Filter rail for the Requests tab: a search box and one row of pills per
     * facet (riwayah, reciter standing, source kind, channel). Pills come from
     * the rows of the selected status tab and are faceted against each other.
     */
    import { REQUEST_FACETS, type FacetPill, type RequestFacetKey } from './requests-filters';

    interface Props {
        pills: Record<RequestFacetKey, FacetPill[]>;
        search: string;
        hasFilters: boolean;
        onSearch: (value: string) => void;
        onToggle: (key: RequestFacetKey, tag: string) => void;
        onClear: () => void;
    }

    let { pills, search, hasFilters, onSearch, onToggle, onClear }: Props = $props();
</script>

<div class="filters">
    <div class="search-row">
        <input
            class="search"
            type="search"
            placeholder="Search name, slug or channel"
            value={search}
            oninput={(e) => onSearch((e.currentTarget as HTMLInputElement).value)}
        />
        {#if hasFilters}
            <button class="clear" onclick={onClear}>Clear filters</button>
        {/if}
    </div>
    {#each REQUEST_FACETS as facet (facet.key)}
        {@const row = pills[facet.key] ?? []}
        {#if row.length > 0}
            <div class="facet">
                <span class="facet-label">{facet.label}</span>
                <div class="pills">
                    {#each row as p (p.tag)}
                        <button
                            class="pill"
                            class:active={p.active}
                            class:empty={p.count === 0}
                            aria-pressed={p.active}
                            onclick={() => onToggle(facet.key, p.tag)}
                        >
                            {p.label}
                            <span class="p-count">{p.count}</span>
                        </button>
                    {/each}
                </div>
            </div>
        {/if}
    {/each}
</div>

<style>
    .filters {
        display: flex; flex-direction: column; gap: var(--s-2);
        padding: var(--s-3) var(--s-5);
        border-bottom: 1px solid var(--border-quiet);
        flex-shrink: 0;
    }
    .search-row { display: flex; align-items: center; gap: var(--s-3); }
    .search {
        flex: 1; max-width: 360px; height: 28px; padding: 0 var(--s-3);
        background: var(--canvas-inset); color: var(--text-primary);
        border: 1px solid var(--border-quiet); border-radius: 6px; font-size: var(--fs-meta);
    }
    .search:focus { outline: none; border-color: var(--accent); }
    .clear {
        background: none; border: none; padding: 0; cursor: pointer;
        color: var(--text-muted); font-size: var(--fs-meta); text-decoration: underline;
    }
    .clear:hover { color: var(--text-primary); }
    .facet { display: flex; align-items: baseline; gap: var(--s-3); }
    .facet-label {
        flex: 0 0 64px; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em;
        color: var(--text-faint);
    }
    .pills { display: flex; flex-wrap: wrap; gap: var(--s-2); }
    .pill {
        display: inline-flex; align-items: center; gap: var(--s-2);
        height: 24px; padding: 0 var(--s-3);
        background: transparent; border: 1px solid var(--border-quiet);
        border-radius: 999px; color: var(--text-secondary); font-size: var(--fs-meta);
        cursor: pointer;
        transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
    }
    .pill:hover { border-color: var(--border-strong); color: var(--text-primary); }
    .pill .p-count { font-family: var(--font-mono); font-size: 10.5px; font-variant-numeric: tabular-nums; color: var(--text-faint); }
    .pill.active { background: var(--accent-tint); border-color: var(--accent); color: var(--accent); }
    .pill.active .p-count { color: var(--accent); }
    .pill.empty { opacity: 0.45; }
</style>
