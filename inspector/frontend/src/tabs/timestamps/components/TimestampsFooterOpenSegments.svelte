<script lang="ts">
    /**
     * Timestamps footer — "Open in Segments" redirect (BottomPlayer
     * `download-lead` slot, immediately left of the download button).
     *
     * Jumps to the Segments editor for the reciter + verse currently focused in
     * the Timestamps tab: switches tab, loads that reciter's corpus, opens the
     * verse's chapter, and scrolls its first segment row into view. Wiring is
     * `gotoSegments` + a `focusVerse` deep-link that `SegmentsTab` consumes once
     * the chapter data lands. Disabled until a reciter + verse are loaded.
     */
    import * as m from '$lib/paraglide/messages';
    import { i18n } from '$lib/i18n/locale.svelte';
    import { playerContext } from '../../../lib/stores/player-context';
    import { gotoSegments } from '../../../lib/utils/goto-segments';
    import { selectedVerse } from '../stores/verse';
    import { isSpecialRef } from '../../../lib/recitation-data/specials';

    const slug = $derived($playerContext.delivery?.slug ?? '');
    const verseRef = $derived($selectedVerse);
    // A recited opener (ref `0:x`) has no verse to open in the editor.
    const disabled = $derived(!slug || !verseRef || isSpecialRef(verseRef));

    // Attribute labels gated on i18n.locale so they re-render on a locale switch.
    const ariaLabel = $derived(
        (i18n.locale,
        verseRef
            ? m.ts_footer_open_segments_aria_label_with_verse({ verse: verseRef })
            : m.ts_footer_open_segments_aria_label_plain()),
    );
    const openSegmentsTitle = $derived((i18n.locale, m.ts_footer_open_segments_title()));

    function openInSegments(): void {
        if (disabled) return;
        const [s, a] = verseRef.split(':');
        const chapter = Number(s);
        const verse = Number(a);
        if (!Number.isFinite(chapter) || !Number.isFinite(verse)) return;
        gotoSegments(slug, { focusVerse: { slug, chapter, verse } });
    }
</script>

<button
    type="button"
    class="open-seg-btn"
    {disabled}
    onclick={openInSegments}
    aria-label={ariaLabel}
    title={openSegmentsTitle}
>
    <!-- External-link (box + arrow to top-right): "redirect out to Segments". -->
    <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
    >
        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
        <path d="M15 3h6v6" />
        <path d="M10 14 21 3" />
    </svg>
</button>

<style>
    /* Mirrors the sibling download button (round, currentColor stroke) so the
       two right-edge affordances read as one cluster. */
    .open-seg-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        color: var(--text-secondary);
        background: transparent;
        border: 0;
        border-radius: 50%;
        cursor: pointer;
        transition: color var(--t-fast), background var(--t-fast);
    }
    .open-seg-btn:hover:not(:disabled) {
        color: var(--text-primary);
        background: var(--panel-2);
    }
    .open-seg-btn:disabled {
        opacity: 0.35;
        cursor: not-allowed;
    }
</style>
