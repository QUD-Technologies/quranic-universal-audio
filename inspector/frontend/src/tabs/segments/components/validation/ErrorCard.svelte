<script lang="ts">
    /**
     * ErrorCard — dispatcher that selects the appropriate card subcomponent
     * based on the validation category.
     *
     * Public 3-method API (getIsContextShown / showContextForced /
     * hideContextForced) is forwarded to the active child via bind:this so
     * ValidationPanel's "Show All Context" feature keeps working.
     *
     * `initialContextShown`: context-open state to restore when this card
     * re-mounts after scrolling back into the virtualization window.
     *
     * Dispatches `contextchange` (detail: boolean) whenever the child card's
     * context-shown state changes so ValidationPanel can persist it.
     */

    import { createEventDispatcher, onMount } from 'svelte';

    import type { SegValAnyItem, SegValMissingVerseItem, SegValMissingWordsItem } from '../../../../lib/types/generated/schemas';
    import { IssueRegistry } from '../../domain/registry';
    import GenericIssueCard from './GenericIssueCard.svelte';
    import MissingVersesCard from './MissingVersesCard.svelte';
    import MissingWordsCard from './MissingWordsCard.svelte';

    // ---- Props ----
    export let category: string;
    export let item: SegValAnyItem;
    /** Settled by an edit since the accordion opened: shown as done. */
    export let settled = false;
    /** Context-shown state to restore when re-entering the virtualization window. */
    export let initialContextShown = false;

    const dispatch = createEventDispatcher<{ contextchange: boolean }>();

    function bubbleContextChange(e: CustomEvent<boolean>): void {
        dispatch('contextchange', e.detail);
    }

    // ---- Child refs for API forwarding ----
    let mwCard: MissingWordsCard;
    let mvCard: MissingVersesCard;
    let genCard: GenericIssueCard;

    // Type-narrowed item props for each branch (avoids `as` casts in template).
    $: mwItem = item as SegValMissingWordsItem;
    $: mvItem = item as SegValMissingVerseItem;

    /** Card-type from the registry drives which subcomponent renders. */
    $: cardType = IssueRegistry[category]?.cardType ?? 'generic';

    function _active(): { getIsContextShown(): boolean; showContextForced(): void; hideContextForced(): void } | null {
        if (cardType === 'missingWords') return mwCard ?? null;
        if (cardType === 'missingVerses') return mvCard ?? null;
        return genCard ?? null;
    }

    // ---- Public interface ----
    export function getIsContextShown(): boolean {
        return _active()?.getIsContextShown() ?? false;
    }
    export function showContextForced(): void {
        // Child dispatches contextchange(true); the template on:contextchange
        // re-bubbles it from this component — no need to dispatch twice.
        _active()?.showContextForced();
    }
    export function hideContextForced(): void {
        _active()?.hideContextForced();
    }

    // Restore context state when re-mounted into the virtualization window.
    // Deferred via microtask so child card refs are set before the call.
    onMount(() => {
        if (initialContextShown) {
            Promise.resolve().then(() => { _active()?.showContextForced(); });
        }
    });
</script>

<div class="val-card-wrapper">
    {#if cardType === 'missingWords'}
        <MissingWordsCard
            bind:this={mwCard}
            item={mwItem}
            on:contextchange={bubbleContextChange}
        />
    {:else if cardType === 'missingVerses'}
        <MissingVersesCard
            bind:this={mvCard}
            item={mvItem}
            on:contextchange={bubbleContextChange}
        />
    {:else}
        <GenericIssueCard
            bind:this={genCard}
            {category}
            {item}
            {settled}
            on:contextchange={bubbleContextChange}
        />
    {/if}
</div>

<style>
    .val-card-wrapper {
        margin-bottom: 8px;
        padding: 6px 8px;
        background: var(--canvas-inset);
        border: 1px solid var(--border-default);
        border-radius: 4px;
    }
</style>
