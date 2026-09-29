<script lang="ts">
    /**
     * Timestamps footer — "Report" button + category drop-up (BottomPlayer
     * `loc-lead` slot, just left of the surah picker). Files a categorized issue
     * against the verse currently playing; visible to everyone holding
     * `timestamps.report` (anonymous-eligible). Disabled until a verse loads.
     *
     * The verse + reciter are SNAPSHOTTED when the drop-up opens, so the report
     * targets what the user was hearing even if playback advances. The drop-up
     * shows the category taxonomy (ReportMenu); audio/other expand a comment
     * composer INLINE in their row; the grid-target categories enter report mode.
     * The button (and each reported category) lights amber when the playing
     * verse already carries open reports.
     */
    import { clickOutside } from '../../../lib/actions/click-outside';
    import { i18n } from '../../../lib/i18n/locale.svelte';
    import * as m from '../../../lib/paraglide/messages';
    import { can } from '../../../lib/stores/capabilities';
    import { playerContext } from '../../../lib/stores/player-context';
    import { dashPort } from '../../../lib/playback/dash-port';
    import type { TsVerseData } from '../../../lib/types/ts-client';
    import type { TsReport } from '../../../lib/types/generated/schemas';
    import { getVerseReports } from '../services/reports-client';
    import { mushafActive } from '../stores/mushaf';
    import {
        enterPhonemes,
        enterSilence,
        enterTajweed,
        enterTiming,
        type SilenceSubtype,
        type TajweedSubtype,
    } from '../stores/report-mode';
    import { currentVerseReports, loadReciterReports, openReportedVerseKeys } from '../stores/ts-reports';
    import { get } from 'svelte/store';
    import { focusWaslGroup, loadedVerse, selectedVerse } from '../stores/verse';
    import ReportIcon from './report/ReportIcon.svelte';
    import ReportMenu from './report/ReportMenu.svelte';

    const canReport = can('timestamps.report');

    let open = $state(false);
    let snapSlug = $state('');
    let snapVerse = $state('');
    let snapReadingId = $state('');
    let verseReports = $state<TsReport[]>([]);
    let warmedKey = $state('');

    const curSlug = $derived($playerContext.delivery?.slug ?? '');
    const curVerse = $derived($selectedVerse);
    // Reporting works against the waveform + analysis grid, which the Mushaf view hides.
    const disabled = $derived(!curSlug || !curVerse || $mushafActive);
    const isReported = $derived(!!curVerse && $openReportedVerseKeys.has(curVerse));

    // Reading i18n.locale makes these re-run on switch.
    const reportAriaLabel = $derived(
        (i18n.locale,
        curVerse ? m.ts_footer_report_aria_label({ verse: curVerse }) : m.ts_footer_report_aria_label_plain()),
    );
    const reportTitle = $derived(
        (i18n.locale, $mushafActive ? m.ts_mushaf_disabled_title() : m.ts_footer_report_title()),
    );
    const reportButtonLabel = $derived((i18n.locale, m.ts_footer_report_button()));

    // Keep the reciter-level reported-verse counts (button highlight) in sync
    // with the playing reciter.
    let lastSlug = '';
    $effect(() => {
        const slug = curSlug;
        if (slug !== lastSlug) {
            lastSlug = slug;
            void loadReciterReports(slug);
        }
    });

    async function loadVerse(): Promise<void> {
        if (!snapSlug || !snapVerse) return;
        try {
            const doc = await getVerseReports(snapSlug, snapVerse);
            verseReports = doc.reports ?? [];
        } catch {
            verseReports = [];
        }
    }

    async function prewarm(): Promise<void> {
        if (disabled || open) return;
        const slug = curSlug, verse = curVerse;
        const key = `${slug}:${verse}`;
        if (key === warmedKey) return;
        warmedKey = key;
        snapSlug = slug;
        snapVerse = verse;
        snapReadingId = readingIdFor(verse);
        const seed = get(currentVerseReports);
        if (seed.length) verseReports = seed;
        await loadVerse();
    }

    /**
     * Every span of `verse` in the loaded occasion, in the file-absolute ms
     * `dashPort` reports.
     *
     * Native readings carry `parts` in that unit already. A word-profile
     * reading has no parts — its rows are occasion-relative seconds — so shift
     * them by the occasion's own start. Without this branch a non-Hafs delivery
     * matched no reading at all, and the audio/other composers (which ARE
     * offered for a word profile) could never submit.
     */
    function verseSpans(data: TsVerseData, verse: string): { id: string; from: number; to: number }[] {
        const spans: { id: string; from: number; to: number }[] = [];
        for (const reading of data.native) {
            for (const part of reading.parts) {
                if (part.ref === verse) spans.push({ id: reading.id, from: part.t[0], to: part.t[1] });
            }
        }
        for (const reading of data.wordReadings) {
            const words = reading.words.filter((w) => w.location.startsWith(`${verse}:`));
            if (words.length === 0) continue;
            spans.push({
                id: reading.id,
                from: data.time_start_ms + Math.min(...words.map((w) => w.start)) * 1000,
                to: data.time_start_ms + Math.max(...words.map((w) => w.end)) * 1000,
            });
        }
        return spans;
    }

    function readingIdFor(verse: string): string {
        const data = get(focusWaslGroup)?.data ?? get(loadedVerse)?.data;
        if (!data) return '';
        const spans = verseSpans(data, verse);
        const ids = new Set(spans.map((s) => s.id));
        if (ids.size === 1) return [...ids][0]!;
        const at = dashPort.currentTimeMs();
        const active = new Set(spans.filter((s) => s.from <= at && at < s.to).map((s) => s.id));
        return active.size === 1 ? [...active][0]! : '';
    }

    function toggle(): void {
        if (open) {
            open = false;
            return;
        }
        if (disabled) return;
        const key = `${curSlug}:${curVerse}`;
        snapSlug = curSlug;
        snapVerse = curVerse;
        snapReadingId = readingIdFor(curVerse);
        if (key !== warmedKey) {
            const seed = get(currentVerseReports);
            verseReports = seed.length ? seed : [];
            warmedKey = key;
        }
        open = true;
        void loadVerse();
    }

    function onChanged(): void {
        void loadVerse();
        void loadReciterReports(snapSlug);
    }

    function onEnterMode(
        mode: 'timing' | 'tajweed' | 'phonemes' | 'silence',
        subtype?: TajweedSubtype | SilenceSubtype,
    ): void {
        open = false;
        if (!snapSlug || !snapVerse) return;
        if (mode === 'timing') enterTiming(snapSlug, snapVerse);
        else if (mode === 'phonemes') enterPhonemes(snapSlug, snapVerse);
        else if (mode === 'silence')
            enterSilence(snapSlug, snapVerse, (subtype as SilenceSubtype) ?? 'pause_missed');
        else enterTajweed(snapSlug, snapVerse, (subtype as TajweedSubtype) ?? 'wrong_rule');
    }

    // Shift a start-anchored drop-up back on-screen if it overflows the far
    // edge on a narrow window. Direction-aware: in LTR it's anchored left and
    // can overflow right; in RTL it's anchored right and can overflow left.
    function keepInView(node: HTMLElement) {
        const margin = 8;
        const place = (): void => {
            const rtl = getComputedStyle(node).direction === 'rtl';
            const anchor = rtl ? 'right' : 'left';
            const other = rtl ? 'left' : 'right';
            node.style[other] = 'auto';
            node.style[anchor] = '0px';
            node.style.maxWidth = '';
            const rect = node.getBoundingClientRect();
            const overflow = rtl
                ? margin - rect.left
                : rect.right - (window.innerWidth - margin);
            if (overflow > 0) node.style[anchor] = `${-overflow}px`;
            node.style.maxWidth = `${window.innerWidth - margin * 2}px`;
        };
        place();
        window.addEventListener('resize', place);
        return { destroy: () => window.removeEventListener('resize', place) };
    }
</script>

{#if $canReport}
    <div class="report-wrap" use:clickOutside={() => (open = false)}>
        <button
            type="button"
            class="report-btn"
            class:reported={isReported}
            class:on={open}
            {disabled}
            onclick={toggle}
            onpointerenter={prewarm}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-label={reportAriaLabel}
            title={reportTitle}
        >
            <ReportIcon name="flag" size={14} />
            <span>{reportButtonLabel}</span>
        </button>

        {#if open}
            <div class="report-pop" use:keepInView>
                <ReportMenu
                    slug={snapSlug}
                    verseKey={snapVerse}
                    readingId={snapReadingId}
                    {verseReports}
                    onchanged={onChanged}
                    onenterMode={onEnterMode}
                />
            </div>
        {/if}
    </div>
{/if}

<style>
    .report-wrap {
        position: relative;
        display: inline-flex;
        flex: 0 0 auto;
        align-self: center;
    }
    .report-btn {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 4px var(--s-2);
        line-height: 1.2;
        color: var(--text-muted);
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        cursor: pointer;
        font: inherit;
        font-size: var(--fs-meta);
        white-space: nowrap;
        transition: color var(--t-fast), background var(--t-fast), border-color var(--t-fast);
    }
    .report-btn:hover:not(:disabled) {
        color: var(--text-primary);
        border-color: var(--border-strong);
        background: var(--panel);
    }
    .report-btn.on {
        color: var(--text-primary);
        border-color: var(--border-strong);
        background: var(--panel);
    }
    .report-btn:disabled { opacity: 0.5; cursor: default; }
    /* Amber "reported" highlight (the one warning voice). */
    .report-btn.reported {
        color: var(--state-warn-fg);
        border-color: var(--state-warn-border);
        background: var(--state-warn-bg);
    }
    .report-btn.reported:hover:not(:disabled) {
        background: color-mix(in oklab, var(--state-warn-bg), var(--state-warn-fg) 8%);
    }

    .report-pop {
        position: absolute;
        bottom: calc(100% + var(--s-2));
        inset-inline-start: 0;
        width: max-content;
        max-width: min(380px, calc(100vw - 16px));
        max-height: min(640px, 70vh);
        overflow-y: auto;
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        box-shadow: 0 16px 48px oklch(0 0 0 / 0.45);
        z-index: 50;
        padding: var(--s-3);
    }
</style>
