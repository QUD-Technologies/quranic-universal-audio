<script lang="ts">
    /**
     * The catalog identity the mint writes: slug, reciter, channel, source and
     * year. A source slug outside the catalog options is a new source, which
     * then needs a display name (and optionally a URL).
     */
    import { untrack } from 'svelte';
    import type { PlanIdentity, PlanOption } from '../../../../../lib/types/generated/schemas';

    interface Props {
        identity: PlanIdentity;
        kind: string;
        channelOptions: PlanOption[];
        sourceOptions: PlanOption[];
        disabled?: boolean;
    }
    let {
        identity = $bindable(),
        kind,
        channelOptions,
        sourceOptions,
        disabled = false,
    }: Props = $props();

    const NEW_SOURCE = '__new__';
    const isKnownSource = (s: string | undefined): boolean =>
        !!s && sourceOptions.some((o) => o.slug === s);

    // Seeded once per plan version (the panel re-keys this form on reload).
    let newSource = $state(untrack(() => !!identity.source && !isKnownSource(identity.source)));
    const sourceSelect = $derived(newSource ? NEW_SOURCE : (identity.source ?? ''));
    const channelKnown = $derived(channelOptions.some((o) => o.slug === identity.channel));

    function pickSource(value: string): void {
        if (value === NEW_SOURCE) {
            newSource = true;
            identity.source = '';
            return;
        }
        newSource = false;
        identity.source = value;
        identity.new_source_name = null;
        identity.new_source_url = null;
    }
</script>

<fieldset class="identity" {disabled}>
    <legend>Catalog identity</legend>
    <div class="grid">
        <label>
            <span>Slug</span>
            <input type="text" class="mono" bind:value={identity.slug} spellcheck="false" />
        </label>
        <label>
            <span>Reciter id</span>
            <input type="text" class="mono" bind:value={identity.reciter_id} spellcheck="false" />
        </label>

        {#if kind === 'new_reciter'}
            <label>
                <span>Name (English)</span>
                <input type="text" bind:value={identity.name_en} />
            </label>
            <label>
                <span>Name (Arabic)</span>
                <input type="text" dir="rtl" bind:value={identity.name_ar} />
            </label>
            <label>
                <span>Country</span>
                <input type="text" bind:value={identity.country} />
            </label>
        {/if}

        <label>
            <span>Channel</span>
            <select bind:value={identity.channel}>
                {#if !channelKnown}
                    <option value={identity.channel ?? ''}>{identity.channel || '— choose —'}</option>
                {/if}
                {#each channelOptions as o (o.slug)}
                    <option value={o.slug}>{o.label}</option>
                {/each}
            </select>
        </label>

        <label>
            <span>Source</span>
            <select value={sourceSelect} onchange={(e) => pickSource(e.currentTarget.value)}>
                {#if !newSource && !isKnownSource(identity.source)}
                    <option value="">— choose —</option>
                {/if}
                {#each sourceOptions as o (o.slug)}
                    <option value={o.slug}>{o.label}</option>
                {/each}
                <option value={NEW_SOURCE}>New source…</option>
            </select>
        </label>

        <label>
            <span>Recording year</span>
            <input type="number" min="1900" max="2100" placeholder="optional" bind:value={identity.recording_year} />
        </label>
    </div>

    {#if newSource}
        <div class="grid new-source">
            <label>
                <span>New source slug</span>
                <input type="text" class="mono" bind:value={identity.source} spellcheck="false" />
            </label>
            <label>
                <span>Source name <em>required</em></span>
                <input type="text" bind:value={identity.new_source_name} />
            </label>
            <label class="wide">
                <span>Source URL</span>
                <input type="url" class="mono" placeholder="optional" bind:value={identity.new_source_url} />
            </label>
        </div>
    {/if}
</fieldset>

<style>
    .identity { margin: 0; padding: 0; border: 0; min-width: 0; display: flex; flex-direction: column; gap: var(--s-2); }
    legend { padding: 0; margin-bottom: var(--s-2); font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 500; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: var(--s-2) var(--s-3); }
    .new-source { padding: var(--s-2); border: 1px dashed var(--border-default); border-radius: var(--r-2); }
    .wide { grid-column: 1 / -1; }
    label { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
    label > span { font-size: 10.5px; color: var(--text-faint); }
    label em { font-style: normal; color: var(--text-muted); }
    input, select {
        width: 100%; padding: 4px 8px;
        background: var(--canvas-inset); color: var(--text-primary);
        border: 1px solid var(--border-default); border-radius: var(--r-2);
        font: var(--fs-meta)/1.4 var(--font-sans);
    }
    input.mono { font-family: var(--font-mono); }
    input:focus, select:focus { outline: none; border-color: var(--accent); }
    input::placeholder { color: var(--text-faint); }
    input:disabled, select:disabled { opacity: 0.6; }
</style>
