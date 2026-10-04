<script lang="ts">
    // External link rail (top-left of the header). Brand marks render muted
    // and reveal their true brand color + a sliding label on hover.
    // Order is deliberate: the ecosystem this belongs to (QUD³, set off by a
    // hairline) -> source -> builds -> published data -> community.
    import { localeStore, tr } from '$lib/i18n/locale-store';
    import * as m from '$lib/paraglide/messages';

    const DISCORD_URL = 'https://discord.gg/cZ3V2FynXz';

    type Brand = 'qud' | 'gh' | 'rel' | 'hf' | 'discord';
    type Link = { key: Brand; label: string; href: string };

    // `gh`/`discord` are brand names (data — not translated); `rel`/`hf` are
    // chrome labels keyed to common_*. The base `label` holds the brand fallback.
    const links: Link[] = [
        { key: 'qud', label: 'QUD³', href: 'https://qud.dev' },
        { key: 'gh', label: 'GitHub', href: 'https://github.com/QUD-Technologies/quranic-universal-audio' },
        { key: 'rel', label: 'Releases', href: 'https://github.com/QUD-Technologies/quranic-universal-audio/releases' },
        { key: 'hf', label: 'Dataset', href: 'https://huggingface.co/datasets/QUD-Technologies/quranic-universal-ayahs' },
        { key: 'discord', label: 'Discord', href: DISCORD_URL },
    ];

    $: lang = $localeStore;
    $: navAriaLabel = tr(lang, m.common_external_links_nav_aria_label());
    $: linkLabel = (link: Link): string => {
        if (link.key === 'rel') return tr(lang, m.common_external_links_releases_label());
        if (link.key === 'hf') return tr(lang, m.common_external_links_dataset_label());
        return link.label;
    };
</script>

<nav class="link-rail" aria-label={navAriaLabel}>
    {#each links as link (link.key)}
        <a
            class="link"
            data-brand={link.key}
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={linkLabel(link)}
            title={linkLabel(link)}
        >
            {#if link.key === 'qud'}
                <!-- The QUD³ mark, outlined from qud.dev's brand kit (npm run brand). -->
                <svg class="qud-mark" viewBox="0 -830.0 2324.7 1000.0" fill="currentColor" aria-hidden="true"><path transform="translate(0.0 0.0)" d="M618 140Q553 154 507 157.5Q461 161 425 157.5Q389 154 354.5 147.5Q320 141 279 134Q238 127 182 123.5Q126 120 46 124L46 17Q79 18 117 17Q155 16 194 13Q233 10 271.5 4Q310 -2 344 -11.5Q378 -21 405 -36L405 -42Q332 -21 273.5 -24.5Q215 -28 169.5 -51.5Q124 -75 93.5 -116Q63 -157 47.5 -211.5Q32 -266 32 -331Q32 -407 52 -469.5Q72 -532 110 -578Q148 -624 203.5 -649Q259 -674 331 -674Q422 -674 488 -633Q554 -592 589.5 -515Q625 -438 625 -330Q625 -243 601 -180Q577 -117 532 -74.5Q487 -32 424.5 -7Q362 18 284 30L285 36Q341 44 384.5 49.5Q428 55 465 55Q502 55 539 49.5Q576 44 618 30ZM329 -88Q374 -88 411 -112Q448 -136 470.5 -188.5Q493 -241 493 -326Q493 -406 473 -459.5Q453 -513 416 -540Q379 -567 328 -567Q283 -567 246 -542Q209 -517 187 -464Q165 -411 165 -326Q165 -242 186.5 -189.5Q208 -137 245 -112.5Q282 -88 329 -88Z"/><path transform="translate(717.0 0.0)" d="M311 14Q258 14 215 2Q172 -10 140 -33.5Q108 -57 86.5 -89.5Q65 -122 54 -164.5Q43 -207 43 -257L43 -660L170 -660L170 -264Q170 -212 186 -175Q202 -138 233 -119Q264 -100 311 -100Q359 -100 390 -119Q421 -138 437 -174.5Q453 -211 453 -264L453 -660L579 -660L579 -257Q579 -127 509.5 -56.5Q440 14 311 14Z"/><path class="d3" transform="translate(1399.3 0.0)" d="M107 0L107 -108L285 -108Q336 -108 373.5 -131Q411 -154 432.5 -202.5Q454 -251 454 -328Q454 -384 442.5 -426Q431 -468 408 -496Q385 -524 349.5 -538Q314 -552 264 -552L107 -552L107 -660L259 -660Q373 -660 445 -621Q517 -582 551 -509.5Q585 -437 585 -336Q585 -258 568 -201Q551 -144 521 -105Q491 -66 453 -43Q415 -20 372.5 -10Q330 0 289 0ZM46 0L46 -660L173 -660L173 0Z"/><path class="d3" transform="translate(2045.3 -490.0)" d="M134 7Q101.5 7 77.2 0.2Q53 -6.5 37 -19.2Q21 -32 13.7 -51Q6.5 -70 8.5 -95L68.5 -104Q66.5 -88.5 71.2 -77.2Q76 -66 86 -58.7Q96 -51.5 109.2 -48Q122.5 -44.5 137.5 -44.5Q156.5 -44.5 172 -49.7Q187.5 -55 196.7 -66.7Q206 -78.5 206 -97Q206 -121.5 191.5 -135.2Q177 -149 147.7 -153.7Q118.5 -158.5 74 -155L74 -194.5L178.5 -274.5L178.5 -277L23.5 -277L23.5 -330.5L252.5 -330.5L252.5 -276L143 -191.5L143 -189Q188.5 -190.5 216.7 -177.7Q245 -165 258.2 -143Q271.5 -121 271.5 -92.5Q271.5 -62.5 256.2 -40.2Q241 -18 210.5 -5.5Q180 7 134 7Z"/></svg>
            {:else if link.key === 'gh'}
                <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>
            {:else if link.key === 'rel'}
                <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.42l9 9c.36.36.86.58 1.41.58s1.05-.22 1.41-.59l7-7c.37-.36.59-.86.59-1.41s-.23-1.06-.59-1.42zM5.5 7C4.67 7 4 6.33 4 5.5S4.67 4 5.5 4 7 4.67 7 5.5 6.33 7 5.5 7z"/></svg>
            {:else if link.key === 'hf'}
                <svg class="hf-logo" viewBox="0 0 95 88" fill="none" aria-hidden="true">
                    <path fill="#FFD21E" d="M47.21 76.5a34.75 34.75 0 1 0 0-69.5 34.75 34.75 0 0 0 0 69.5Z"/>
                    <path fill="#FF9D0B" d="M81.96 41.75a34.75 34.75 0 1 0-69.5 0 34.75 34.75 0 0 0 69.5 0Zm-73.5 0a38.75 38.75 0 1 1 77.5 0 38.75 38.75 0 0 1-77.5 0Z"/>
                    <path fill="#3A3B45" d="M58.5 32.3c1.28.44 1.78 3.06 3.07 2.38a5 5 0 1 0-6.76-2.07c.61 1.15 2.55-.72 3.7-.32ZM34.95 32.3c-1.28.44-1.79 3.06-3.07 2.38a5 5 0 1 1 6.76-2.07c-.61 1.15-2.56-.72-3.7-.32Z"/>
                    <path fill="#FF323D" d="M46.96 56.29c9.83 0 13-8.76 13-13.26 0-2.34-1.57-1.6-4.09-.36-2.33 1.15-5.46 2.74-8.9 2.74-7.19 0-13-6.88-13-2.38s3.16 13.26 13 13.26Z"/>
                    <path fill="#3A3B45" fill-rule="evenodd" d="M39.43 54a8.7 8.7 0 0 1 5.3-4.49c.4-.12.81.57 1.24 1.28.4.68.82 1.37 1.24 1.37.45 0 .9-.68 1.33-1.35.45-.7.89-1.38 1.32-1.25a8.61 8.61 0 0 1 5 4.17c3.73-2.94 5.1-7.74 5.1-10.7 0-2.34-1.57-1.6-4.09-.36l-.14.07c-2.31 1.15-5.39 2.67-8.77 2.67s-6.45-1.52-8.77-2.67c-2.6-1.29-4.23-2.1-4.23.29 0 3.05 1.46 8.06 5.47 10.97Z" clip-rule="evenodd"/>
                    <path fill="#FF9D0B" d="M70.71 37a3.25 3.25 0 1 0 0-6.5 3.25 3.25 0 0 0 0 6.5ZM24.21 37a3.25 3.25 0 1 0 0-6.5 3.25 3.25 0 0 0 0 6.5ZM17.52 48c-1.62 0-3.06.66-4.07 1.87a5.97 5.97 0 0 0-1.33 3.76 7.1 7.1 0 0 0-1.94-.3c-1.55 0-2.95.59-3.94 1.66a5.8 5.8 0 0 0-.8 7 5.3 5.3 0 0 0-1.79 2.82c-.24.9-.48 2.8.8 4.74a5.22 5.22 0 0 0-.37 5.02c1.02 2.32 3.57 4.14 8.52 6.1 3.07 1.22 5.89 2 5.91 2.01a44.33 44.33 0 0 0 10.93 1.6c5.86 0 10.05-1.8 12.46-5.34 3.88-5.69 3.33-10.9-1.7-15.92-2.77-2.78-4.62-6.87-5-7.77-.78-2.66-2.84-5.62-6.25-5.62a5.7 5.7 0 0 0-4.6 2.46c-1-1.26-1.98-2.25-2.86-2.82A7.4 7.4 0 0 0 17.52 48Zm0 4c.51 0 1.14.22 1.82.65 2.14 1.36 6.25 8.43 7.76 11.18.5.92 1.37 1.31 2.14 1.31 1.55 0 2.75-1.53.15-3.48-3.92-2.93-2.55-7.72-.68-8.01.08-.02.17-.02.24-.02 1.7 0 2.45 2.93 2.45 2.93s2.2 5.52 5.98 9.3c3.77 3.77 3.97 6.8 1.22 10.83-1.88 2.75-5.47 3.58-9.16 3.58-3.81 0-7.73-.9-9.92-1.46-.11-.03-13.45-3.8-11.76-7 .28-.54.75-.76 1.34-.76 2.38 0 6.7 3.54 8.57 3.54.41 0 .7-.17.83-.6.79-2.85-12.06-4.05-10.98-8.17.2-.73.71-1.02 1.44-1.02 3.14 0 10.2 5.53 11.68 5.53.11 0 .2-.03.24-.1.74-1.2.33-2.04-4.9-5.2-5.21-3.16-8.88-5.06-6.8-7.33.24-.26.58-.38 1-.38 3.17 0 10.66 6.82 10.66 6.82s2.02 2.1 3.25 2.1c.28 0 .52-.1.68-.38.86-1.46-8.06-8.22-8.56-11.01-.34-1.9.24-2.85 1.31-2.85Z"/>
                    <path fill="#FFD21E" d="M38.6 76.69c2.75-4.04 2.55-7.07-1.22-10.84-3.78-3.77-5.98-9.3-5.98-9.3s-.82-3.2-2.69-2.9c-1.87.3-3.24 5.08.68 8.01 3.91 2.93-.78 4.92-2.29 2.17-1.5-2.75-5.62-9.82-7.76-11.18-2.13-1.35-3.63-.6-3.13 2.2.5 2.79 9.43 9.55 8.56 11-.87 1.47-3.93-1.71-3.93-1.71s-9.57-8.71-11.66-6.44c-2.08 2.27 1.59 4.17 6.8 7.33 5.23 3.16 5.64 4 4.9 5.2-.75 1.2-12.28-8.53-13.36-4.4-1.08 4.11 11.77 5.3 10.98 8.15-.8 2.85-9.06-5.38-10.74-2.18-1.7 3.21 11.65 6.98 11.76 7.01 4.3 1.12 15.25 3.49 19.08-2.12Z"/>
                    <path fill="#FF9D0B" d="M77.4 48c1.62 0 3.07.66 4.07 1.87a5.97 5.97 0 0 1 1.33 3.76 7.1 7.1 0 0 1 1.95-.3c1.55 0 2.95.59 3.94 1.66a5.8 5.8 0 0 1 .8 7 5.3 5.3 0 0 1 1.78 2.82c.24.9.48 2.8-.8 4.74a5.22 5.22 0 0 1 .37 5.02c-1.02 2.32-3.57 4.14-8.51 6.1-3.08 1.22-5.9 2-5.92 2.01a44.33 44.33 0 0 1-10.93 1.6c-5.86 0-10.05-1.8-12.46-5.34-3.88-5.69-3.33-10.9 1.7-15.92 2.78-2.78 4.63-6.87 5.01-7.77.78-2.66 2.83-5.62 6.24-5.62a5.7 5.7 0 0 1 4.6 2.46c1-1.26 1.98-2.25 2.87-2.82A7.4 7.4 0 0 1 77.4 48Zm0 4c-.51 0-1.13.22-1.82.65-2.13 1.36-6.25 8.43-7.76 11.18a2.43 2.43 0 0 1-2.14 1.31c-1.54 0-2.75-1.53-.14-3.48 3.91-2.93 2.54-7.72.67-8.01a1.54 1.54 0 0 0-.24-.02c-1.7 0-2.45 2.93-2.45 2.93s-2.2 5.52-5.97 9.3c-3.78 3.77-3.98 6.8-1.22 10.83 1.87 2.75 5.47 3.58 9.15 3.58 3.82 0 7.73-.9 9.93-1.46.1-.03 13.45-3.8 11.76-7-.29-.54-.75-.76-1.34-.76-2.38 0-6.71 3.54-8.57 3.54-.42 0-.71-.17-.83-.6-.8-2.85 12.05-4.05 10.97-8.17-.19-.73-.7-1.02-1.44-1.02-3.14 0-10.2 5.53-11.68 5.53-.1 0-.19-.03-.23-.1-.74-1.2-.34-2.04 4.88-5.2 5.23-3.16 8.9-5.06 6.8-7.33-.23-.26-.57-.38-.98-.38-3.18 0-10.67 6.82-10.67 6.82s-2.02 2.1-3.24 2.1a.74.74 0 0 1-.68-.38c-.87-1.46 8.05-8.22 8.55-11.01.34-1.9-.24-2.85-1.31-2.85Z"/>
                    <path fill="#FFD21E" d="M56.33 76.69c-2.75-4.04-2.56-7.07 1.22-10.84 3.77-3.77 5.97-9.3 5.97-9.3s.82-3.2 2.7-2.9c1.86.3 3.23 5.08-.68 8.01-3.92 2.93.78 4.92 2.28 2.17 1.51-2.75 5.63-9.82 7.76-11.18 2.13-1.35 3.64-.6 3.13 2.2-.5 2.79-9.42 9.55-8.55 11 .86 1.47 3.92-1.71 3.92-1.71s9.58-8.71 11.66-6.44c2.08 2.27-1.58 4.17-6.8 7.33-5.23 3.16-5.63 4-4.9 5.2.75 1.2 12.28-8.53 13.36-4.4 1.08 4.11-11.76 5.3-10.97 8.15.8 2.85 9.05-5.38 10.74-2.18 1.69 3.21-11.65 6.98-11.76 7.01-4.31 1.12-15.26 3.49-19.08-2.12Z"/>
                </svg>
            {:else}
                <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z"/></svg>
            {/if}
            {#if link.key !== 'qud'}<span class="lbl">{linkLabel(link)}</span>{/if}
        </a>
    {/each}
</nav>

<style>
    .link-rail {
        grid-column: 1;
        justify-self: start;
        display: flex;
        align-items: center;
        gap: 2px;
    }
    .link {
        --c: var(--brand-gh); /* GitHub default; overridden per brand below */
        position: relative;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        color: var(--brand-rail-fg);
        text-decoration: none;
        padding: 8px;
        border-radius: 7px;
        transition: color 0.18s cubic-bezier(0.22, 1, 0.36, 1),
            background 0.18s cubic-bezier(0.22, 1, 0.36, 1);
    }
    .link[data-brand='gh'] { --c: var(--brand-gh); }
    .link[data-brand='rel'] { --c: var(--brand-rel); }
    .link[data-brand='hf'] { --c: var(--brand-hf); }
    .link[data-brand='discord'] { --c: var(--brand-discord); }
    /* QUD³ is a wordmark, not an icon: no sliding label. At rest it is muted like
       the rest of the rail; on hover the Q and U take the text colour and only the
       D³ takes the brand accent, as the mark is drawn everywhere else. */
    .link[data-brand='qud'] {
        --c: var(--brand-qud-fg);
        margin-inline-end: 8px;
    }
    .link[data-brand='qud']::after {
        content: '';
        position: absolute;
        inset-inline-end: -5px;
        inset-block: 9px;
        border-inline-end: 1px solid var(--brand-rail-fg);
        opacity: 0.45;
    }
    .link .qud-mark {
        width: auto;
        height: 15px;
    }
    .link .qud-mark .d3 {
        transition: fill 0.18s cubic-bezier(0.22, 1, 0.36, 1);
    }
    .link:hover .qud-mark .d3,
    .link:focus-visible .qud-mark .d3 {
        fill: var(--brand-qud);
    }

    .link svg {
        width: 18px;
        height: 18px;
        display: block;
        flex-shrink: 0;
    }
    /* Official multi-color HF logo: desaturated at rest to read as a muted
       monochrome mark, bursting into full brand color on hover/focus. The bright
       yellow/orange artwork greyscales LIGHT, so on the light theme it must be
       darkened to stay a visible mark on near-white. */
    .link .hf-logo {
        filter: grayscale(1) opacity(0.6);
        transition: filter 0.2s cubic-bezier(0.22, 1, 0.36, 1);
    }
    :global(html[data-theme='light']) .link .hf-logo {
        filter: grayscale(1) brightness(0.72) opacity(0.7);
    }
    .link:hover .hf-logo,
    .link:focus-visible .hf-logo {
        filter: none;
    }
    :global(html[data-theme='light']) .link:hover .hf-logo,
    :global(html[data-theme='light']) .link:focus-visible .hf-logo {
        filter: none;
    }
    .lbl {
        font-size: 0.85rem;
        font-weight: 500;
        white-space: nowrap;
        max-width: 0;
        opacity: 0;
        overflow: hidden;
        transform: translateX(-4px);
        transition: max-width 0.26s cubic-bezier(0.22, 1, 0.36, 1),
            opacity 0.2s cubic-bezier(0.22, 1, 0.36, 1),
            transform 0.26s cubic-bezier(0.22, 1, 0.36, 1),
            color 0.18s cubic-bezier(0.22, 1, 0.36, 1);
    }

    .link:hover,
    .link:focus-visible {
        color: var(--c);
        background: var(--panel-2);
        outline: none;
    }
    .link:focus-visible {
        box-shadow: 0 0 0 2px var(--c);
    }
    .link:hover .lbl,
    .link:focus-visible .lbl {
        max-width: 120px;
        opacity: 1;
        transform: translateX(0);
        color: var(--c);
    }

    @media (prefers-reduced-motion: reduce) {
        .link,
        .lbl,
        .link .hf-logo {
            transition: none;
        }
    }
</style>
