<script lang="ts">
    /**
     * Shared surah picker — the trigger chip plus its `SurahPopover` dropup, used
     * by both the dashboard/timestamps bottom player and the Segments footer. The
     * only cross-tab difference is size: pass `compact` for the denser Segments
     * transport. The chip renders no caret (its border + hover state signal that
     * it opens); `open` is parent-controlled so each footer keeps owning its
     * click-outside and any sibling-popover coordination.
     *
     * The dropup is pinned to the locale direction (`popDir`) rather than
     * inheriting it: in the Segments footer the picker sits inside the
     * transport's `dir="ltr"` island, but its surah grid + search must still
     * flow RTL under Arabic.
     */
    import { i18n } from '$lib/i18n/locale.svelte';

    import SurahPopover from './SurahPopover.svelte';

    let {
        surahNums,
        value,
        label,
        open,
        disabled = false,
        compact = false,
        live = false,
        hasValue = false,
        ariaLabel,
        ontoggle,
        onchange,
        onhover,
    }: {
        surahNums: number[];
        value: number | null;
        /** Text shown on the trigger — resolved active name or a placeholder. */
        label: string;
        open: boolean;
        disabled?: boolean;
        compact?: boolean;
        live?: boolean;
        hasValue?: boolean;
        ariaLabel?: string;
        ontoggle: () => void;
        onchange: (n: number) => void;
        onhover?: (n: number) => void;
    } = $props();

    const popDir = $derived(['ar', 'ckb'].includes(i18n.locale) ? 'rtl' : 'ltr');
</script>

<div class="surah-picker" class:compact>
    <button
        type="button"
        class="trigger"
        class:live
        class:has-value={hasValue}
        {disabled}
        aria-label={ariaLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        onclick={ontoggle}
    >{label}</button>
    {#if open}
        <div class="pop" dir={popDir}>
            <SurahPopover {surahNums} {value} {onchange} {onhover} />
        </div>
    {/if}
</div>

<style>
    .surah-picker { position: relative; }
    .trigger {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        padding: 4px var(--s-2);
        font-size: var(--fs-meta);
        color: var(--text-secondary);
        background: transparent;
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        cursor: pointer;
        white-space: nowrap;
        transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
    }
    .trigger:hover:not(:disabled) {
        border-color: var(--border-strong);
        color: var(--text-primary);
    }
    .trigger.has-value { color: var(--text-primary); }
    .trigger:disabled { opacity: 0.35; cursor: not-allowed; }
    .trigger.live { color: var(--accent); border-color: var(--accent-border-soft); }

    /* Segments-transport sizing: match the sibling location cells' height. No
       fixed width — the chip hugs its label exactly like the bottom-player
       picker, so a short surah name never leaves an empty gap. */
    .compact .trigger {
        height: 36px;
        padding: 0 10px;
    }

    .pop {
        position: absolute;
        bottom: calc(100% + var(--s-2));
        /* Physical centering under the trigger — symmetric, so correct in both
           directions (logical inset would offset it under RTL). */
        left: 50%;
        transform: translateX(-50%);
        width: min(700px, calc(100vw - var(--s-4) * 2));
        padding: var(--s-2);
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        box-shadow: var(--shadow-pop);
        z-index: 50;
    }
</style>
