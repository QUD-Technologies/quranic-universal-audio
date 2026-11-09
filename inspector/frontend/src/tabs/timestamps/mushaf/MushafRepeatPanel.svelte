<script lang="ts">
    /**
     * Repeat, as the Mushaf app's panel: which verses (this verse, the spread on show, this
     * surah, or a range chosen as a surah and a verse for each end), how many times each verse,
     * how many times the whole range, and a pause between. Bounds come from the core's
     * `repeatBounds`; the driver plays the plan (`repeat.svelte.ts`). Any seek the reader makes ends it.
     */
    import { formatNumber, type RepeatRange, repeatBounds } from '@qud-technologies/mushaf-core/app';
    import type { QuranPack } from '@qud-technologies/quran-data';
    import { onMount, untrack } from 'svelte';

    import { i18n } from '$lib/i18n/locale.svelte';
    import * as m from '$lib/paraglide/messages';

    import Segmented from '../../../lib/components/Segmented.svelte';
    import { loadQuranPack } from '../../../lib/quran-data/pack';
    import { loadManifest } from '../../../lib/recitation-data/ts-source';
    import { recitationFocus } from '../../../lib/recitation-animation/recitation-settings';
    import { playerContext } from '../../../lib/stores/player-context';
    import { surahName } from '../../../lib/utils/surah-info';
    import { mushafYear } from '../stores/mushaf';
    import { mushafRepeat } from './repeat.svelte';

    const SURAH_COUNT = 114;
    const LOOP = 'loop';
    const EACH = ['1', '2', '3', '5', LOOP] as const;
    const WHOLE = ['1', '2', '3', LOOP] as const;
    const PAUSES = ['0', '1', '2', '3'] as const;

    let pack = $state.raw<QuranPack | null>(null);
    let message = $state('');
    let range = $state<RepeatRange>('verse');
    let each = $state<string>('1');
    let whole = $state<string>(LOOP);
    let pause = $state<string>('0');

    const slug = $derived($playerContext.delivery?.slug ?? '');
    const arabic = $derived(i18n.locale === 'ar');
    const focus = $derived($recitationFocus);
    const verse = $derived(focus ? `${focus.surah}:${focus.ayah}` : '1:1');
    const ayahCount = (s: number): number => pack?.counts(s).ayahs ?? 1;

    // A chosen range starts at the verse in play when the panel opens.
    const [startSurah, startAyah] = untrack(() => verse.split(':').map(Number)) as [number, number];
    let fromSurah = $state(startSurah);
    let fromAyah = $state(startAyah);
    let toSurah = $state(startSurah);
    let toAyah = $state(startAyah);

    /** A start after the end moves the end with it; a surah change starts at its first verse. */
    function setFromSurah(s: number): void {
        fromSurah = s;
        fromAyah = 1;
        if (toSurah < s) { toSurah = s; toAyah = ayahCount(s); }
    }
    function setToSurah(s: number): void {
        toSurah = s;
        toAyah = s === fromSurah ? Math.max(fromAyah, ayahCount(s)) : ayahCount(s);
    }

    /** Before the view reports the spread on show: the one holding `verse` (right page odd, left even). */
    function spreadBounds(p: QuranPack): [string, string] {
        const layout = `madani-${$mushafYear}`;
        const page = p.pageOf(layout, `${verse}:1`);
        const first = page % 2 ? page : page - 1;
        const refs = [first, first + 1]
            .filter((n) => n >= 1 && n <= (p.layouts().find((l) => l.id === layout)?.pages ?? n))
            .flatMap((n) => p.page(layout, n).lines.filter((l) => l.kind === 'ayah').flatMap((l) => l.items))
            .map((a) => a.split(':').slice(0, 2).join(':'));
        return [refs[0] ?? verse, refs.at(-1) ?? verse];
    }

    const bounds = $derived.by(() => {
        if (!pack) return null;
        const s = Number(verse.split(':')[0]);
        return repeatBounds(range, {
            verse,
            page: mushafRepeat.spread ?? spreadBounds(pack),
            surah: [`${s}:1`, `${s}:${ayahCount(s)}`],
            from: `${fromSurah}:${fromAyah}`,
            to: `${toSurah}:${toAyah}`,
        });
    });

    const times = (v: string): number => (v === LOOP ? Infinity : Number(v));
    const num = (n: number | string): string => formatNumber(Number(n), arabic);
    const ayahsOf = (s: number): number[] => Array.from({ length: ayahCount(s) }, (_, i) => i + 1);
    const surahs = Array.from({ length: SURAH_COUNT }, (_, i) => i + 1);

    onMount(() => {
        void loadQuranPack()
            .then((p) => { pack = p; })
            .catch((e: unknown) => console.error('Mushaf repeat: Quran data load failed', e));
    });

    async function start(): Promise<void> {
        if (!bounds) return;
        const manifest = await loadManifest();
        const chapters = (manifest.reciters?.[slug]?.ts_chapters ?? []).slice().sort((a, b) => a - b);
        const plan = { from: bounds[0], to: bounds[1], verseTimes: times(each), rangeTimes: times(whole), pauseMs: Number(pause) * 1000 };
        const ok = await mushafRepeat.start(slug, plan, chapters);
        message = ok ? '' : m.ts_mushaf_repeat_empty();
    }

    const progress = $derived.by(() => {
        (void i18n.locale);
        const at = mushafRepeat.at;
        const plan = mushafRepeat.plan;
        if (!at || !plan) return '';
        const of = (n: number): string => (n === Infinity ? '∞' : num(n));
        return m.ts_mushaf_repeat_progress({
            verse: `${num(at.surah)}:${num(at.ayah)}`,
            rep: num(at.n + 1),
            each: of(plan.verseTimes),
            round: num(at.round + 1),
            rounds: of(plan.rangeTimes),
        });
    });

    /** Re-evaluate a message on locale switch (reads the locale rune). */
    const L = (s: string): string => (i18n.locale, s);
    const countOptions = (list: readonly string[]): { value: string; label: string }[] =>
        list.map((v) => ({ value: v, label: v === LOOP ? L(m.ts_mushaf_repeat_loop()) : L(m.ts_mushaf_repeat_times({ n: num(v) })) }));
</script>

<div class="mr">
    <section>
        <h3>{L(m.ts_mushaf_repeat_verses())}</h3>
        <Segmented
            ariaLabel={L(m.ts_mushaf_repeat_verses())} value={range} disabled={mushafRepeat.running}
            onchange={(v) => (range = v as RepeatRange)}
            options={[
                { value: 'verse', label: L(m.ts_mushaf_repeat_this_verse()) },
                { value: 'page', label: L(m.ts_mushaf_repeat_this_page()) },
                { value: 'surah', label: L(m.ts_mushaf_repeat_this_surah()) },
                { value: 'custom', label: L(m.ts_mushaf_repeat_custom()) },
            ]}
        />
        {#if range === 'custom'}
            <div class="mr-fields">
                <span class="mr-end">{L(m.ts_mushaf_repeat_from())}</span>
                <select
                    aria-label={`${L(m.ts_mushaf_repeat_from())}: ${L(m.ts_mushaf_repeat_surah())}`}
                    value={fromSurah} disabled={mushafRepeat.running}
                    onchange={(e) => setFromSurah(Number(e.currentTarget.value))}
                >
                    {#each surahs as s (s)}<option value={s}>{num(s)}. {surahName(s, i18n.locale)}</option>{/each}
                </select>
                <select class="mr-ayah" aria-label={`${L(m.ts_mushaf_repeat_from())}: ${L(m.ts_mushaf_repeat_ayah())}`} bind:value={fromAyah} disabled={mushafRepeat.running}>
                    {#each ayahsOf(fromSurah) as a (a)}<option value={a}>{num(a)}</option>{/each}
                </select>
                <span class="mr-end">{L(m.ts_mushaf_repeat_to())}</span>
                <select
                    aria-label={`${L(m.ts_mushaf_repeat_to())}: ${L(m.ts_mushaf_repeat_surah())}`}
                    value={toSurah} disabled={mushafRepeat.running}
                    onchange={(e) => setToSurah(Number(e.currentTarget.value))}
                >
                    {#each surahs.slice(fromSurah - 1) as s (s)}<option value={s}>{num(s)}. {surahName(s, i18n.locale)}</option>{/each}
                </select>
                <select class="mr-ayah" aria-label={`${L(m.ts_mushaf_repeat_to())}: ${L(m.ts_mushaf_repeat_ayah())}`} bind:value={toAyah} aria-invalid={!bounds} disabled={mushafRepeat.running}>
                    {#each ayahsOf(toSurah) as a (a)}<option value={a} disabled={toSurah === fromSurah && a < fromAyah}>{num(a)}</option>{/each}
                </select>
            </div>
            {#if pack && !bounds}<p class="mr-msg" role="alert">{L(m.ts_mushaf_repeat_to_before_from())}</p>{/if}
        {/if}
    </section>
    <section>
        <h3>{L(m.ts_mushaf_repeat_each())}</h3>
        <Segmented ariaLabel={L(m.ts_mushaf_repeat_each())} value={each} disabled={mushafRepeat.running} onchange={(v) => (each = v)} options={countOptions(EACH)} />
    </section>
    <section>
        <h3>{L(m.ts_mushaf_repeat_rounds())}</h3>
        <Segmented ariaLabel={L(m.ts_mushaf_repeat_rounds())} value={whole} disabled={mushafRepeat.running} onchange={(v) => (whole = v)} options={countOptions(WHOLE)} />
    </section>
    <section>
        <h3>{L(m.ts_mushaf_repeat_pause())}</h3>
        <Segmented
            ariaLabel={L(m.ts_mushaf_repeat_pause())} value={pause} disabled={mushafRepeat.running}
            onchange={(v) => (pause = v)}
            options={PAUSES.map((v) => ({ value: v, label: v === '0' ? L(m.ts_mushaf_repeat_pause_none()) : L(m.ts_mushaf_repeat_pause_seconds({ n: num(v) })) }))}
        />
    </section>

    {#if mushafRepeat.running}
        <p class="mr-progress" aria-live="polite">{progress}</p>
    {:else if message}
        <p class="mr-msg" role="alert">{message}</p>
    {/if}
    <div class="mr-actions">
        {#if mushafRepeat.running}
            <button type="button" class="mr-ghost" onclick={() => mushafRepeat.stop()}>{L(m.ts_mushaf_repeat_stop())}</button>
        {:else}
            <button type="button" class="mr-go" disabled={!bounds || mushafRepeat.loading || !slug} onclick={() => void start()}>
                {mushafRepeat.loading ? L(m.ts_mushaf_repeat_loading()) : L(m.ts_mushaf_repeat_start())}
            </button>
        {/if}
    </div>
</div>

<style>
    .mr {
        display: flex;
        flex-direction: column;
        gap: var(--s-3);
        width: 340px;
        max-width: calc(100vw - 2 * var(--s-3));
        font-size: var(--fs-meta);
        color: var(--text-secondary);
    }
    section { display: flex; flex-direction: column; gap: var(--s-1); }
    h3 { margin: 0; color: var(--text-secondary); font-size: var(--fs-meta); font-weight: 560; }
    .mr-fields {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) 4.5em;
        align-items: center;
        gap: var(--s-1) var(--s-2);
        margin-top: var(--s-1);
    }
    .mr-end { color: var(--text-muted); }
    .mr-fields select {
        min-width: 0;
        padding: 3px var(--s-2);
        font: inherit;
        color: var(--text-primary);
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
    }
    .mr-fields select.mr-ayah { font-variant-numeric: tabular-nums; }
    .mr-fields select:focus-visible { outline: 2px solid var(--accent); outline-offset: 0; }
    .mr-fields select[aria-invalid='true'] { border-color: var(--accent); }
    .mr-progress { margin: 0; color: var(--text-primary); font-variant-numeric: tabular-nums; }
    .mr-msg { margin: 0; color: var(--accent); }
    .mr-actions { display: flex; justify-content: flex-end; gap: var(--s-2); }
    .mr-actions button {
        padding: 5px var(--s-3);
        font: inherit;
        font-weight: 560;
        border: none;
        border-radius: var(--r-2);
        cursor: pointer;
    }
    .mr-go { color: var(--accent-fg); background: var(--accent); }
    .mr-go:disabled { opacity: 0.4; cursor: default; }
    .mr-ghost { color: var(--text-secondary); background: transparent; box-shadow: inset 0 0 0 1px var(--border-strong); }
    .mr-actions button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
