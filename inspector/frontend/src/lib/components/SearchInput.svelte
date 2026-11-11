<script context="module" lang="ts">
    /**
     * Canonical search input — magnifying-glass icon on the left, optional
     * inline "X of Y" count on the right (a flex sibling, so it never
     * overlaps the text however many digits it grows to). Layout (width, container) is the
     * caller's job; this component owns input chrome only.
     *
     * Used by CombinationPicker (modal search), CatalogList (dashboard), and
     * the Timestamps reciter picker. Pass `debounceMs` to coalesce keystrokes
     * for an expensive consumer (e.g. the multi-thousand-row catalog) — the
     * field stays responsive while the outward `input` event is throttled.
     */
    export interface SearchInputEvents { input: string; }
</script>

<script lang="ts">
    import { createEventDispatcher, onDestroy } from 'svelte';

    import { localeStore, tr } from '$lib/i18n/locale-store';
    import * as m from '$lib/paraglide/messages';

    export let value = '';
    export let placeholder: string | undefined = undefined;
    export let count: number | null = null;
    export let total: number | null = null;
    export let ariaLabel: string | undefined = undefined;
    export let debounceMs = 0;

    // Default placeholder is the localized "Search"; callers may override it.
    $: effectivePlaceholder = placeholder ?? tr($localeStore, m.common_search_input_placeholder());

    let inputEl: HTMLInputElement | null = null;
    export function focus(): void { inputEl?.focus(); }

    const dispatch = createEventDispatcher<SearchInputEvents>();

    // The input shows `display` (instant on keystroke); the outward `input`
    // event is what we debounce. `lastDispatched` tracks the value we last sent
    // up so we can tell our own echo from an external reset of `value`.
    let display = value;
    let lastDispatched = value;
    let timer: ReturnType<typeof setTimeout> | null = null;

    // Parent changed `value` from the outside (e.g. "Clear all") — adopt it and
    // drop any pending debounced dispatch so the stale value can't resurrect it.
    $: if (value !== lastDispatched) {
        display = value;
        lastDispatched = value;
        if (timer) { clearTimeout(timer); timer = null; }
    }

    function fire(v: string): void {
        lastDispatched = v;
        dispatch('input', v);
    }

    function onInput(e: Event): void {
        const v = (e.target as HTMLInputElement).value;
        display = v;
        if (debounceMs > 0) {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => { timer = null; fire(v); }, debounceMs);
        } else {
            fire(v);
        }
    }

    onDestroy(() => { if (timer) clearTimeout(timer); });

    $: countLabel =
        count !== null && total !== null
            ? tr($localeStore, m.common_search_input_count_label({ count, total }))
            : null;
</script>

<div class="search-input">
    <span class="icon" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.35-4.35" />
        </svg>
    </span>
    <input
        bind:this={inputEl}
        type="search"
        autocomplete="off"
        placeholder={effectivePlaceholder}
        aria-label={ariaLabel ?? effectivePlaceholder}
        value={display}
        on:input={onInput}
    />
    {#if countLabel}<span class="count">{countLabel}</span>{/if}
</div>

<style>
    /* The wrapper draws the field chrome and lays icon / input / count out as
       flex siblings, so the count reserves exactly its own width — a fixed
       end-padding let a wide label ("200 of 200") overlap the text (#338). */
    .search-input {
        display: flex;
        align-items: center;
        gap: var(--s-2);
        width: 100%;
        height: 32px;
        padding-inline: var(--s-3);
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-2);
        transition: border-color var(--t-fast), background var(--t-fast);
    }
    .search-input:focus-within {
        border-color: var(--accent);
        background: var(--panel-2);
    }
    .icon {
        display: flex;
        flex-shrink: 0;
        color: var(--text-faint);
        pointer-events: none;
    }
    input {
        flex: 1 1 auto;
        min-width: 0;
        height: 100%;
        padding: 0;
        background: transparent;
        border: none;
        color: var(--text-primary);
        font-size: var(--fs-body);
        text-overflow: ellipsis;
        outline: none;
    }
    input::placeholder { color: var(--text-faint); }
    /* Suppress the native ×; no custom clear button yet. */
    input::-webkit-search-cancel-button {
        -webkit-appearance: none;
        appearance: none;
        display: none;
    }
    .count {
        flex-shrink: 0;
        white-space: nowrap;
        font-size: 10.5px;
        color: var(--text-faint);
        font-family: var(--font-mono);
        font-variant-numeric: tabular-nums;
        pointer-events: none;
    }
</style>
