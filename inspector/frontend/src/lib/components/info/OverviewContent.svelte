<script lang="ts">
    /**
     * Project-overview body — the parsed `overview.md` rendered as headings,
     * prose, lists, links, and the five lifecycle `StatePill`s. Chrome-free
     * shared content: the dashboard wraps it in a narrow `Modal` (`InfoModal`),
     * and the segments first-edit gate renders it inside `AccordionGuideModal`
     * via the `::component{name="overview"}` directive. Edit wording in
     * `overview.md`.
     */
    import { onMount } from 'svelte';

    import { localeStore, tr } from '$lib/i18n/locale-store';
    import * as m from '$lib/paraglide/messages';

    import StatePill from '../StatePill.svelte';
    import type { InlineToken } from './info-doc';
    import { getOverviewDoc } from './overview';

    $: indexAriaLabel = tr($localeStore, m.common_info_index_aria_label());

    // Scoped RTL for the modal content + TOC while the app-wide layout stays LTR
    // (the full RTL flip is a separate workflow): Arabic prose, bullets, and the
    // section-index row read right-to-left within this subtree only.
    $: dir = (['ar', 'ckb'].includes($localeStore) ? 'rtl' : 'ltr') as 'rtl' | 'ltr';

    // Locale-aware doc: re-selects the translated overview.md on a switch.
    $: overviewDoc = getOverviewDoc($localeStore);

    let rootEl: HTMLDivElement | undefined;
    let navEl: HTMLElement | undefined;
    let scrollParent: HTMLElement | null = null;

    const slugify = (s: string): string =>
        s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');

    // Unique, non-empty slug per heading block. Arabic headings slugify to '' (no
    // ASCII), so a bare slug would collide across every section and crash the keyed
    // {#each}; a per-base counter guarantees uniqueness while keeping readable
    // slugs for ASCII headings. Keyed by block reference so the nav and the heading
    // anchor resolve to the same slug.
    $: headingSlugs = ((): Map<object, string> => {
        const map = new Map<object, string>();
        const seen = new Map<string, number>();
        for (const b of overviewDoc.blocks) {
            if (b.type !== 'heading') continue;
            const base = slugify(b.text) || 'section';
            const n = seen.get(base) ?? 0;
            seen.set(base, n + 1);
            map.set(b, n === 0 ? base : `${base}-${n}`);
        }
        return map;
    })();

    // Jump index built from the `##` section headings (re-derives on locale switch).
    $: sections = overviewDoc.blocks.flatMap((b) =>
        b.type === 'heading' ? [{ title: b.text, slug: headingSlugs.get(b) ?? '' }] : [],
    );

    // Scroll-spy: the section whose heading currently sits under the pinned index.
    let activeSlug = '';
    // Seed the highlight to the first section once sections resolve.
    $: if (!activeSlug && sections[0]) activeSlug = sections[0].slug;
    // A click holds its section highlighted until the next manual scroll, so the
    // highlight never sweeps past it (released by cancelLock in onMount).
    let lockedSlug: string | null = null;

    // Where a heading should land below the container top: clear the pinned bar
    // plus an 8px gap. updateActive's activation line sits below this, so a
    // clicked heading is always comfortably inside its own active zone.
    const landOffset = (): number => (navEl?.offsetHeight ?? 0) + 8;

    function jumpTo(slug: string): void {
        const el = rootEl?.querySelector<HTMLElement>(`[data-slug="${slug}"]`);
        if (!el) return;
        activeSlug = slug;
        const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        const behavior: ScrollBehavior = reduce ? 'auto' : 'smooth';
        const navH = landOffset();
        lockedSlug = slug;
        if (scrollParent) {
            const delta = el.getBoundingClientRect().top - scrollParent.getBoundingClientRect().top;
            const max = scrollParent.scrollHeight - scrollParent.clientHeight;
            const target = Math.max(0, Math.min(scrollParent.scrollTop + delta - navH, max));
            scrollParent.scrollTo({ top: target, behavior });
        } else {
            const target = Math.max(0, window.scrollY + el.getBoundingClientRect().top - navH);
            window.scrollTo({ top: target, behavior });
        }
    }

    function findScrollParent(el: HTMLElement | undefined): HTMLElement | null {
        let node = el?.parentElement ?? null;
        while (node) {
            const oy = getComputedStyle(node).overflowY;
            if (oy === 'auto' || oy === 'scroll') return node;
            node = node.parentElement;
        }
        return null;
    }

    function updateActive(): void {
        if (!rootEl) return;
        const container = scrollParent;
        // A click holds its section until the next manual scroll (cancelLock).
        if (lockedSlug !== null) {
            activeSlug = lockedSlug;
            return;
        }
        // At the bottom of the scroll the last headings can't reach the line —
        // pin the final section so it still highlights.
        if (
            container &&
            container.scrollTop + container.clientHeight >= container.scrollHeight - 2
        ) {
            const last = sections.at(-1);
            if (last) activeSlug = last.slug;
            return;
        }
        const cTop = container ? container.getBoundingClientRect().top : 0;
        // Activation line ~38% down the viewport (never above the bar): a section
        // lights up a little before its heading reaches the top — once it covers
        // most of the visible area.
        const viewport = container ? container.clientHeight : window.innerHeight;
        const line = Math.max(landOffset() + 12, viewport * 0.38);
        let current = sections[0]?.slug ?? '';
        for (const s of sections) {
            const el = rootEl.querySelector<HTMLElement>(`[data-slug="${s.slug}"]`);
            if (!el) continue;
            if (el.getBoundingClientRect().top - cTop <= line) current = s.slug;
            else break;
        }
        activeSlug = current;
    }

    onMount(() => {
        scrollParent = findScrollParent(rootEl);
        const target: EventTarget = scrollParent ?? window;
        // Any user-initiated scroll releases the click-lock, handing control back
        // to the scroll-spy. Until then a clicked section stays highlighted.
        const cancelLock = (): void => {
            lockedSlug = null;
        };
        const scrollKeys = new Set([
            'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' ',
        ]);
        const onKey = (e: KeyboardEvent): void => {
            if (scrollKeys.has(e.key)) lockedSlug = null;
        };
        updateActive();
        target.addEventListener('scroll', updateActive, { passive: true });
        target.addEventListener('wheel', cancelLock, { passive: true });
        target.addEventListener('touchstart', cancelLock, { passive: true });
        window.addEventListener('keydown', onKey);
        window.addEventListener('resize', updateActive, { passive: true });
        return () => {
            target.removeEventListener('scroll', updateActive);
            target.removeEventListener('wheel', cancelLock);
            target.removeEventListener('touchstart', cancelLock);
            window.removeEventListener('keydown', onKey);
            window.removeEventListener('resize', updateActive);
        };
    });
</script>

{#snippet inline(tokens: InlineToken[])}{#each tokens as t, i (i)}{#if t.href}<a href={t.href} target="_blank" rel="noopener noreferrer">{#if t.bold}<strong>{t.text}</strong>{:else}{t.text}{/if}</a>{:else if t.bold}<strong>{t.text}</strong>{:else}{t.text}{/if}{/each}{/snippet}

{#if sections.length > 1}
    <nav class="info-index" {dir} aria-label={indexAriaLabel} bind:this={navEl}>
        {#each sections as s, i (s.slug)}
            {#if i > 0}<span class="info-index-sep" aria-hidden="true">·</span>{/if}
            <button
                type="button"
                class="info-index-link"
                class:active={s.slug === activeSlug}
                on:click={() => jumpTo(s.slug)}
            >{s.title}</button>
        {/each}
    </nav>
{/if}

<div class="info-doc" {dir} bind:this={rootEl}>
    {#each overviewDoc.blocks as block, i (i)}
        {#if block.type === 'heading'}
            <h3 class="info-h" data-slug={headingSlugs.get(block) ?? ''}>{block.text}</h3>
        {:else if block.type === 'paragraph'}
            <p class="info-p">{@render inline(block.tokens)}</p>
        {:else if block.type === 'list'}
            <ul class="info-list">
                {#each block.items as item, j (j)}
                    <li>{@render inline(item)}</li>
                {/each}
            </ul>
        {:else if block.type === 'lifecycle'}
            <ul class="lifecycle">
                {#each block.rows as row (row.state)}
                    <li class="lifecycle-row">
                        <span class="lifecycle-pill"><StatePill state={row.state} size="sm" /></span>
                        <span class="lifecycle-text">{row.text}</span>
                    </li>
                {/each}
            </ul>
        {/if}
    {/each}
</div>

<style>
    .info-doc {
        padding: var(--s-5) var(--s-6) var(--s-6);
        color: var(--text-secondary);
        font-size: var(--fs-body);
        line-height: 1.62;
    }
    .info-h {
        margin: var(--s-6) 0 var(--s-2);
        font-size: var(--fs-body);
        font-weight: 600;
        color: var(--text-primary);
        letter-spacing: 0.01em;
    }
    .info-doc > :global(:first-child) {
        margin-top: 0;
    }

    .info-index {
        position: sticky;
        /* modal-body adds s-3 top padding; the negative top margin + sticky top
         * offset cancel it so the bar sits flush under the header line with
         * symmetric s-3 padding above/below its centered row. No horizontal
         * negative margins — those would overflow the scroll container (and the
         * border already aligns with the body's content edge). */
        top: calc(-1 * var(--s-3));
        z-index: 2;
        margin: calc(-1 * var(--s-3)) 0 0;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-wrap: nowrap;
        gap: var(--s-2);
        padding: var(--s-3) var(--s-6);
        background: var(--canvas);
        border-bottom: 1px solid var(--border-quiet);
    }
    .info-index-link {
        flex-shrink: 0;
        background: transparent;
        border: 0;
        padding: 0;
        font: inherit;
        font-size: var(--fs-meta);
        color: var(--text-muted);
        cursor: pointer;
        transition: color var(--t-fast);
    }
    .info-index-link:hover,
    .info-index-link.active { color: var(--accent); }
    .info-index-link:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
        border-radius: 2px;
    }
    .info-index-sep {
        flex-shrink: 0;
        color: var(--text-faint);
        user-select: none;
    }

    .info-p {
        margin: 0 0 var(--s-3);
    }
    .info-p :global(strong) {
        color: var(--text-primary);
        font-weight: 600;
    }
    .info-list {
        margin: 0 0 var(--s-3);
        padding-inline-start: var(--s-5);
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
    }
    .info-list li {
        list-style: disc;
    }
    .info-list li :global(strong) {
        color: var(--text-primary);
        font-weight: 600;
    }
    .info-doc :global(a) {
        color: var(--accent);
        text-decoration: underline;
        text-underline-offset: 2px;
        text-decoration-color: var(--accent-tint, currentColor);
        transition: color var(--t-fast);
    }
    .info-doc :global(a:hover) {
        color: var(--accent-strong);
        text-decoration-color: currentColor;
    }
    .info-doc :global(a:focus-visible) {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
        border-radius: 2px;
    }

    .lifecycle {
        list-style: none;
        margin: var(--s-3) 0 var(--s-3);
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: var(--s-3);
    }
    .lifecycle-row {
        display: grid;
        grid-template-columns: max-content 1fr;
        align-items: baseline;
        gap: var(--s-3);
    }
    .lifecycle-pill {
        display: inline-flex;
        align-self: start;
        padding-top: 2px;
    }
    .lifecycle-text {
        min-width: 0;
    }
</style>
