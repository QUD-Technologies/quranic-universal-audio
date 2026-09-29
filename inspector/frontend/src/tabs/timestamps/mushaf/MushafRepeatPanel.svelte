<script lang="ts">
    /**
     * Repeat panel — a verse range, how often each verse plays, how often the
     * whole range plays. One field filled = that verse alone. Either end can be
     * typed ("2:255") or picked by clicking a verse on the page; the chips fill
     * the range from where the recitation is.
     */
    import { onMount, untrack } from 'svelte';

    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';
    import { loadManifest, loadQpcVerseIndex } from '../../../lib/recitation-data/ts-source';
    import { recitationFocus } from '../../../lib/recitation-animation/recitation-settings';
    import { playerContext } from '../../../lib/stores/player-context';
    import { mushafYear } from '../stores/mushaf';
    import { firstWordOfVerse, LINE_AYAH, loadLayout, loadWordIndex, pageOfWord } from './layout';
    import { mushafRepeat } from './repeat.svelte';
    import {
        compareRefs,
        expandRange,
        parseRef,
        REPEAT_COUNTS,
        REPEAT_PAUSES_MS,
        refKey,
        type VerseRef,
    } from './repeat-plan';

    const COUNTERS = [
        ['each', m.ts_mushaf_repeat_each],
        ['rounds', m.ts_mushaf_repeat_rounds],
    ] as const;

    let verseCounts = $state.raw(new Map<number, number>());
    let fromText = $state(mushafRepeat.from ? refKey(mushafRepeat.from) : '');
    let toText = $state(mushafRepeat.to ? refKey(mushafRepeat.to) : '');
    let message = $state('');

    const slug = $derived($playerContext.delivery?.slug ?? '');
    const focus = $derived($recitationFocus);
    const fromValid = $derived(parseRef(fromText) !== null);
    /** "To" typed before "From" — the range runs forwards only. */
    const toBeforeFrom = $derived.by(() => {
        const from = parseRef(fromText);
        const to = toText.trim() ? parseRef(toText) : null;
        return !!from && !!to && compareRefs(to, from) < 0;
    });
    const toValid = $derived((!toText.trim() || parseRef(toText) !== null) && !toBeforeFrom);

    // Picked on the page → mirror into the text fields.
    $effect(() => { if (mushafRepeat.from) fromText = refKey(mushafRepeat.from); });
    $effect(() => {
        const to = mushafRepeat.to;
        if (to) untrack(() => { toText = refKey(to); });
    });

    onMount(() => {
        void loadQpcVerseIndex()
            .then((ix) => { verseCounts = new Map([...ix].map(([s, verses]) => [s, verses.size])); })
            .catch((e: unknown) => console.error('Mushaf repeat: verse index load failed', e));
        if (!fromText && focus) fromText = `${focus.surah}:${focus.ayah}`;
    });

    function setRange(from: VerseRef, to: VerseRef | null): void {
        fromText = refKey(from);
        toText = to && refKey(to) !== refKey(from) ? refKey(to) : '';
        mushafRepeat.from = from;
        mushafRepeat.to = to;
        message = '';
    }

    function thisVerse(): void {
        if (focus) setRange(focus, null);
    }

    function thisSurah(): void {
        if (focus) setRange({ surah: focus.surah, ayah: 1 }, { surah: focus.surah, ayah: verseCounts.get(focus.surah) ?? 1 });
    }

    async function thisPage(): Promise<void> {
        if (!focus) return;
        const [layout, words] = await Promise.all([loadLayout($mushafYear), loadWordIndex()]);
        const page = pageOfWord(layout, firstWordOfVerse(words, focus.surah, focus.ayah));
        const ayahLines = (layout.pages[page - 1] ?? []).filter((l) => l[0] === LINE_AYAH);
        const first = ayahLines[0]?.[2];
        const last = ayahLines.at(-1)?.[3];
        if (!first || !last) return;
        setRange(
            { surah: words.surah[first]!, ayah: words.ayah[first]! },
            { surah: words.surah[last]!, ayah: words.ayah[last]! },
        );
    }

    function pick(end: 'from' | 'to'): void {
        mushafRepeat.picking = mushafRepeat.picking === end ? null : end;
    }

    function step(which: 'each' | 'rounds', delta: 1 | -1): void {
        const i = REPEAT_COUNTS.indexOf(mushafRepeat[which] as (typeof REPEAT_COUNTS)[number]);
        const next = REPEAT_COUNTS[Math.max(0, Math.min(REPEAT_COUNTS.length - 1, i + delta))]!;
        mushafRepeat[which] = next;
    }

    function stepPause(delta: 1 | -1): void {
        const i = REPEAT_PAUSES_MS.indexOf(mushafRepeat.pauseMs as (typeof REPEAT_PAUSES_MS)[number]);
        mushafRepeat.pauseMs = REPEAT_PAUSES_MS[Math.max(0, Math.min(REPEAT_PAUSES_MS.length - 1, i + delta))]!;
    }

    function numberFmt(): Intl.NumberFormat {
        return new Intl.NumberFormat(i18n.locale === 'ar' ? 'ar-EG' : 'en');
    }

    function countLabel(n: number): string {
        return n === Infinity ? '∞' : `×${numberFmt().format(n)}`;
    }

    function pauseLabel(ms: number): string {
        return ms === 0
            ? m.ts_mushaf_repeat_pause_none()
            : m.ts_mushaf_repeat_pause_seconds({ n: numberFmt().format(ms / 1000) });
    }

    async function start(): Promise<void> {
        const from = parseRef(fromText);
        const to = toText.trim() ? parseRef(toText) : null;
        if (!from || (toText.trim() && !to)) {
            message = m.ts_mushaf_repeat_invalid();
            return;
        }
        mushafRepeat.from = from;
        mushafRepeat.to = to;
        mushafRepeat.picking = null;
        const manifest = await loadManifest();
        const chapters = new Set(manifest.reciters?.[slug]?.ts_chapters ?? []);
        const verses = expandRange(from, to, (s) => verseCounts.get(s) ?? 0, (s) => chapters.has(s));
        const ok = await mushafRepeat.start(slug, verses);
        message = ok ? '' : m.ts_mushaf_repeat_empty();
    }

    const progress = $derived.by(() => {
        (void i18n.locale);
        const c = mushafRepeat.cursor;
        const cur = mushafRepeat.current;
        if (!c || !cur) return '';
        const fmt = (n: number): string => (n === Infinity ? '∞' : String(n));
        return m.ts_mushaf_repeat_progress({
            verse: refKey(cur),
            rep: String(c.rep),
            each: fmt(mushafRepeat.each),
            round: String(c.round),
            rounds: fmt(mushafRepeat.rounds),
        });
    });

    /** Re-evaluate a message on locale switch (reads the locale rune). */
    const L = (s: string): string => (i18n.locale, s);
</script>

<div class="mr" dir="auto">
    <div class="mr-range">
        <label class="mr-field" class:invalid={!fromValid && fromText !== ''}>
            <span>{L(m.ts_mushaf_repeat_from())}</span>
            <input type="text" inputmode="numeric" dir="ltr" placeholder="2:255" aria-label={L(m.ts_mushaf_repeat_from())} bind:value={fromText} disabled={mushafRepeat.running} />
            <button
                type="button" class="mr-pick" class:on={mushafRepeat.picking === 'from'}
                title={L(m.ts_mushaf_repeat_pick_aria())} aria-label={L(m.ts_mushaf_repeat_pick_aria())}
                aria-pressed={mushafRepeat.picking === 'from'} disabled={mushafRepeat.running}
                onclick={() => pick('from')}
            ><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="4.5" /><path d="M8 1v3M8 12v3M1 8h3M12 8h3" /></svg></button>
        </label>
        <label class="mr-field" class:invalid={!toValid}>
            <span>{L(m.ts_mushaf_repeat_to())}</span>
            <input
                type="text" inputmode="numeric" dir="ltr"
                placeholder={L(m.ts_mushaf_repeat_to_placeholder())}
                aria-label={L(m.ts_mushaf_repeat_to())}
                bind:value={toText} disabled={mushafRepeat.running}
            />
            <button
                type="button" class="mr-pick" class:on={mushafRepeat.picking === 'to'}
                title={L(m.ts_mushaf_repeat_pick_aria())} aria-label={L(m.ts_mushaf_repeat_pick_aria())}
                aria-pressed={mushafRepeat.picking === 'to'} disabled={mushafRepeat.running}
                onclick={() => pick('to')}
            ><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="4.5" /><path d="M8 1v3M8 12v3M1 8h3M12 8h3" /></svg></button>
        </label>
    </div>
    {#if mushafRepeat.picking}
        <p class="mr-hint">{L(m.ts_mushaf_repeat_pick_hint())}</p>
    {:else if toBeforeFrom}
        <p class="mr-msg" role="alert">{L(m.ts_mushaf_repeat_to_before_from())}</p>
    {/if}

    <div class="mr-chips">
        <button type="button" disabled={!focus || mushafRepeat.running} onclick={thisVerse}>{L(m.ts_mushaf_repeat_this_verse())}</button>
        <button type="button" disabled={!focus || mushafRepeat.running} onclick={() => void thisPage()}>{L(m.ts_mushaf_repeat_this_page())}</button>
        <button type="button" disabled={!focus || mushafRepeat.running} onclick={thisSurah}>{L(m.ts_mushaf_repeat_this_surah())}</button>
    </div>

    {#each COUNTERS as [key, label] (key)}
        <div class="mr-count">
            <span>{L(label())}</span>
            <div class="mr-stepper">
                <button
                    type="button" aria-label={L(m.ts_mushaf_repeat_less())}
                    disabled={mushafRepeat.running || mushafRepeat[key] === 1}
                    onclick={() => step(key, -1)}
                >−</button>
                <output>{countLabel(mushafRepeat[key])}</output>
                <button
                    type="button" aria-label={L(m.ts_mushaf_repeat_more())}
                    disabled={mushafRepeat.running || mushafRepeat[key] === Infinity}
                    onclick={() => step(key, 1)}
                >+</button>
            </div>
        </div>
    {/each}

    <!-- Pause can change mid-run: it only shapes the gap before the next play. -->
    <div class="mr-count">
        <span>{L(m.ts_mushaf_repeat_pause())}</span>
        <div class="mr-stepper">
            <button
                type="button" aria-label={L(m.ts_mushaf_repeat_less())}
                disabled={mushafRepeat.pauseMs === REPEAT_PAUSES_MS[0]}
                onclick={() => stepPause(-1)}
            >−</button>
            <output>{L(pauseLabel(mushafRepeat.pauseMs))}</output>
            <button
                type="button" aria-label={L(m.ts_mushaf_repeat_more())}
                disabled={mushafRepeat.pauseMs === REPEAT_PAUSES_MS.at(-1)}
                onclick={() => stepPause(1)}
            >+</button>
        </div>
    </div>

    {#if mushafRepeat.running}
        <p class="mr-progress" aria-live="polite">{progress}</p>
        <button type="button" class="mr-go stop" onclick={() => mushafRepeat.stop()}>{L(m.ts_mushaf_repeat_stop())}</button>
    {:else}
        {#if message}<p class="mr-msg" role="alert">{message}</p>{/if}
        <button
            type="button" class="mr-go" disabled={!fromValid || !toValid || mushafRepeat.loading || !slug}
            onclick={() => void start()}
        >{mushafRepeat.loading ? L(m.ts_mushaf_repeat_loading()) : L(m.ts_mushaf_repeat_start())}</button>
    {/if}
</div>

<style>
    .mr {
        display: grid;
        gap: var(--s-2);
        width: 300px;
        font-size: var(--fs-meta);
        color: var(--text-secondary);
    }
    .mr-range { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-2); }
    .mr-field {
        display: grid;
        grid-template-columns: 1fr auto;
        grid-template-rows: auto auto;
        gap: 2px var(--s-1);
    }
    .mr-field > span { grid-column: 1 / -1; color: var(--text-muted); }
    .mr-field input {
        min-width: 0;
        padding: 3px var(--s-2);
        font: inherit;
        font-family: var(--font-mono);
        color: var(--text-primary);
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
    }
    .mr-field input:focus-visible { outline: 2px solid var(--accent); outline-offset: 0; }
    .mr-field.invalid input { border-color: var(--state-error, var(--border-strong)); }
    .mr-pick,
    .mr-stepper button,
    .mr-chips button {
        color: var(--text-muted);
        background: transparent;
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        cursor: pointer;
        transition: color var(--t-fast), background var(--t-fast);
    }
    .mr-pick { display: inline-flex; align-items: center; padding: 0 5px; }
    .mr-pick svg { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 1.4; }
    .mr-pick.on { color: var(--accent); background: var(--accent-tint); border-color: transparent; }
    button:disabled { opacity: 0.35; cursor: not-allowed; }
    .mr-hint { margin: 0; color: var(--accent); }

    .mr-chips { display: flex; gap: var(--s-1); flex-wrap: wrap; }
    .mr-chips button { padding: 2px var(--s-2); font-size: var(--fs-meta); }
    .mr-chips button:hover:not(:disabled),
    .mr-stepper button:hover:not(:disabled),
    .mr-pick:hover:not(:disabled) { color: var(--text-primary); background: var(--panel-2); }

    .mr-count { display: flex; align-items: center; justify-content: space-between; }
    .mr-stepper { display: inline-flex; align-items: center; gap: var(--s-1); }
    .mr-stepper button { width: 22px; height: 22px; padding: 0; line-height: 1; }
    .mr-stepper output {
        min-width: 3.2em;
        text-align: center;
        font-family: var(--font-mono);
        color: var(--text-primary);
    }

    .mr-progress { margin: 0; color: var(--text-primary); font-variant-numeric: tabular-nums; }
    .mr-msg { margin: 0; color: var(--text-muted); }
    .mr-go {
        padding: 5px var(--s-3);
        font: inherit;
        color: var(--accent-fg);
        background: var(--accent);
        border: none;
        border-radius: var(--r-2);
        cursor: pointer;
    }
    .mr-go.stop { color: var(--text-primary); background: var(--panel-2); }
    .mr-go:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
