<script lang="ts">
    /** Browser-local bookmark rail. */
    import { onMount } from 'svelte';

    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';

    import { bookmarks, bookmarksVisible, initBookmarks, removeBookmark } from '../stores/bookmarks';
    import { pendingTsNavigation } from '../stores/navigation';
    import { setActiveTab } from '../utils/active-tab';
    import { TAB_NAMES } from '../utils/constants';
    import { surahInfoReady, surahOptionText } from '../utils/surah-info';

    let ready = $state(false);

    onMount(() => {
        initBookmarks();
        void surahInfoReady.then(() => { ready = true; });
    });

    function open(surah: number, ayah: number): void {
        pendingTsNavigation.set({ surah, ayah, autoplay: true });
        setActiveTab(TAB_NAMES.TIMESTAMPS);
        bookmarksVisible.set(false);
    }

    const label = (surah: number): string => surahOptionText(surah, i18n.locale);
    const panelAria = $derived((i18n.locale, m.common_bookmarks_panel_aria_label()));
    const heading = $derived((i18n.locale, m.common_bookmarks_heading()));
    const close = $derived((i18n.locale, m.common_action_close()));
    const empty = $derived((i18n.locale, m.common_bookmarks_empty()));
    const remove = $derived((i18n.locale, m.common_bookmarks_remove_title()));
</script>

<aside id="bookmarks-panel" class="bookmarks-panel" hidden={!$bookmarksVisible} aria-label={panelAria}>
    <header>
        <h2>{heading}</h2>
        <button class="close" type="button" title={close} onclick={() => bookmarksVisible.set(false)}>×</button>
    </header>

    {#if $bookmarks.length === 0}
        <p class="empty">{empty}</p>
    {:else}
        <ul>
            {#each $bookmarks as bookmark (bookmark.key)}
                <li>
                    <button class="open" type="button" onclick={() => open(bookmark.surah, bookmark.ayah)}>
                        <span>{ready ? label(bookmark.surah) : m.common_bookmarks_item_surah_fallback({ surah: bookmark.surah })}</span>
                        <span class="ayah">{m.common_bookmarks_item_ayah({ ayah: bookmark.ayah })}</span>
                    </button>
                    <button class="remove" type="button" title={remove} onclick={() => removeBookmark(bookmark.key)}>×</button>
                </li>
            {/each}
        </ul>
    {/if}
</aside>

<style>
    .bookmarks-panel {
        position: fixed;
        inset-block: 0;
        inset-inline-start: 0;
        z-index: 1000;
        display: flex;
        width: min(340px, 90vw);
        flex-direction: column;
        overflow-y: auto;
        border-inline-end: 1px solid var(--border-default);
        background: var(--panel-sidebar);
        box-shadow: var(--shadow-pop);
        color: var(--text-secondary);
        padding: 16px;
    }
    header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
    h2 { margin: 0; color: var(--text-primary); font-size: 1.1rem; }
    .close, .remove { border: 0; background: none; color: var(--text-secondary); cursor: pointer; }
    .close { font-size: 1.5rem; line-height: 1; }
    .close:hover, .remove:hover { color: var(--text-primary); }
    ul { display: flex; flex-direction: column; gap: 4px; margin: 0; padding: 0; list-style: none; }
    li { display: flex; align-items: center; border: 1px solid var(--border-default); border-radius: 6px; }
    .open { display: flex; flex: 1; justify-content: space-between; gap: 8px; border: 0; background: none; color: var(--text-primary); padding: 10px; text-align: start; cursor: pointer; }
    .open:hover { background: var(--surface-hover); }
    .ayah { color: var(--text-muted); }
    .remove { padding: 10px; font-size: 1rem; }
    .empty { color: var(--text-muted); font-size: 0.88rem; }
</style>
