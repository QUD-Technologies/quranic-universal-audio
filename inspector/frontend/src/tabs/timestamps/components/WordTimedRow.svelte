<script lang="ts">
    /**
     * Analysis row for a word-profile (non-Hafs) delivery.
     *
     * The native row renders phonemizer cells through `@quranic-phonemizer/cells`:
     * letters, sounds, columns, tajweed rule spans, cross-word bridges. A
     * proxy-timed word shard has none of those — it has words and the gaps
     * between them — so `parse()` would throw on the degenerate wire rather than
     * degrade (D8). This is the whole row for that profile: word cells, the pause
     * gap after each, and the verse-end marker in the edition's own glyph.
     *
     * It reuses the `.timed-analysis` class deliberately: every `--qc-*` token,
     * report-mode outline and pause-tile rule in `styles/timestamps.css` then
     * applies unchanged, and a word cell here looks like a word cell there.
     *
     * Report targets use the same `data-qc-word-id` / `data-qc-boundary-id`
     * hooks and the same `[data-reading-id]` ancestor, so a report filed here
     * resolves through `cellTargetFromEl` exactly like a native one.
     */
    import { get } from 'svelte/store';
    import { onDestroy, tick } from 'svelte';

    import { dashPort } from '../../../lib/playback/dash-port';
    import { verseMarkerPrefix } from '../../../lib/riwayat';
    import type { WordProfileBoundary, WordProfileWord } from '../../../lib/types/ts-client';
    import { toArabicNumeral, ZWSP } from '../../../lib/utils/arabic-text';
    import { qpcWaqfRenderStyle } from '../../../lib/utils/qpc-waqf-render';
    import {
        deliveryRiwayah,
        showTranslations,
        tsHoveredElement,
        tsWaveformHoverTime,
        verseTranslations,
    } from '../stores/display';
    import { loopTarget } from '../stores/playback';
    import {
        focusCell,
        focusedCellKey,
        reportMode,
        staged,
        upsertStaged,
    } from '../stores/report-mode';
    import { currentVerseReports } from '../stores/ts-reports';
    import { focusWaslGroup, loadedVerse } from '../stores/verse';
    import { TS_CLICK_DELAY_MS } from '../utils/constants';
    import { cellTargetFromEl, targetCellKey } from '../utils/report-target';
    import WordTranslation from './WordTranslation.svelte';

    /** A rendered element with a playback span — words and pause gaps only. */
    interface WordEntity {
        kind: 'word' | 'boundary';
        readingId: string;
        id: number;
        element: HTMLElement;
        start: number;
        end: number;
        /** Index of the owning word, for loop identity and silence targeting. */
        wordIndex: number;
    }

    let root = $state<HTMLDivElement | undefined>(undefined);
    let entities: WordEntity[] = [];
    let entityByElement = new Map<HTMLElement, WordEntity>();
    let clickTimer: ReturnType<typeof setTimeout> | null = null;

    const displayData = $derived($focusWaslGroup?.data ?? $loadedVerse?.data ?? null);
    const focusRef = $derived($focusWaslGroup?.focusRef ?? $loadedVerse?.data.verse_ref ?? '');
    const readings = $derived(displayData?.wordReadings ?? []);
    const marker = $derived(verseMarkerPrefix($deliveryRiwayah));
    /** The packaged QPC faces decorate their own digits — and set marks higher. */
    const qpcFace = $derived(marker === '');

    const verseOf = (location: string): string => location.split(':').slice(0, 2).join(':');
    /** Words from a neighbouring verse pulled in by a waṣl chain read as context. */
    const isContext = (word: WordProfileWord): boolean => verseOf(word.location) !== focusRef;

    /** A gap only reads as a pause when it actually has duration. */
    const recorded = (boundary: WordProfileBoundary): boolean =>
        boundary.end - boundary.start > 0.001;
    const gapText = (boundary: WordProfileBoundary): string =>
        boundary.verseEnd == null ? '' : marker + toArabicNumeral(boundary.verseEnd);
    /**
     * The lifted waqf mark, shown once the reciter actually paused on it — the
     * native row hides its `stop_sign` column the same way until a pause is
     * recorded. Rendered through the cells package's `.pause-waqf` so the
     * per-mark calibration centres the ink in the tile exactly as it does
     * for Hafs. The QPC faces draw a bare combining mark with no ink at all
     * (DigitalKhatt tolerates it), so the glyph rides a word joiner — the
     * same anchor the teleprompter's decorators use.
     */
    const stopMark = (boundary: WordProfileBoundary): string | null =>
        boundary.verseEnd == null && boundary.stopSign && recorded(boundary)
            ? boundary.stopSign
            : null;

    function offsetSeconds(): number {
        const group = get(focusWaslGroup);
        if (group) return group.span[0] / 1000;
        return get(loadedVerse)?.tsSegOffset ?? 0;
    }

    function rebuildCache(): void {
        entities = [];
        entityByElement = new Map();
        if (!root) return;
        for (const reading of readings) {
            const selector = '[data-reading-id="' + CSS.escape(reading.id) + '"]';
            const host = root.querySelector<HTMLElement>(selector);
            if (!host) continue;
            for (const word of reading.words) {
                const cell = host.querySelector<HTMLElement>(
                    '[data-qc-word-id="' + String(word.id) + '"]',
                );
                if (cell) {
                    const entity: WordEntity = {
                        kind: 'word', readingId: reading.id, id: word.id, element: cell,
                        start: word.start, end: word.end, wordIndex: word.displayIndex,
                    };
                    entities.push(entity);
                    entityByElement.set(cell, entity);
                    cell.tabIndex = 0;
                    cell.setAttribute('role', 'button');
                }
                const gap = word.boundary;
                if (!gap) continue;
                const tile = host.querySelector<HTMLElement>(
                    '[data-qc-boundary-id="' + String(gap.id) + '"]',
                );
                if (!tile) continue;
                const entity: WordEntity = {
                    kind: 'boundary', readingId: reading.id, id: gap.id, element: tile,
                    start: gap.start, end: gap.end, wordIndex: word.displayIndex,
                };
                entities.push(entity);
                entityByElement.set(tile, entity);
            }
        }
    }

    function updateReportClasses(): void {
        const silenceMode = $reportMode.kind === 'silence';
        const reports = new Map($currentVerseReports
            .filter((report) => report.status === 'open')
            .map((report) => [targetCellKey(report.target), report]));
        for (const entity of entities) {
            const key = entity.readingId + ':' + entity.kind + ':' + String(entity.id);
            const report = reports.get(key);
            entity.element.classList.toggle('report-flag-public', Boolean(report));
            entity.element.classList.toggle('report-flag-staged', $staged.has(key));
            entity.element.classList.toggle('report-focused', $focusedCellKey === key);
            if (report) entity.element.dataset.qcReportCategory = report.category;
            else delete entity.element.dataset.qcReportCategory;
            if (entity.kind !== 'boundary') continue;
            entity.element.tabIndex = silenceMode ? 0 : -1;
            if (silenceMode) entity.element.setAttribute('role', 'button');
            else entity.element.removeAttribute('role');
        }
    }

    function updateLoopClasses(): void {
        const target = $loopTarget;
        for (const entity of entities) {
            const matches = target?.kind === 'word'
                && entity.kind === 'word'
                && entity.wordIndex === target.wordIndex;
            entity.element.classList.toggle('loop', Boolean(matches));
        }
    }

    $effect(() => {
        void readings;
        void tick().then(() => {
            rebuildCache();
            updateReportClasses();
            updateLoopClasses();
        });
    });

    $effect(() => {
        void $currentVerseReports;
        void $staged;
        void $focusedCellKey;
        void $reportMode;
        updateReportClasses();
    });

    $effect(() => {
        void $loopTarget;
        updateLoopClasses();
    });

    function currentTime(): number {
        const hover = get(tsWaveformHoverTime);
        if (hover != null && dashPort.paused) return hover;
        return dashPort.currentTimeMs() / 1000 - offsetSeconds();
    }

    /**
     * Called every frame by the tab. No karaoke wipe here — a word shard has no
     * sub-word sound timing to interpolate one from.
     */
    export function updateHighlights(): void {
        const time = currentTime();
        let inPause = false;
        for (const entity of entities) {
            const active = time >= entity.start && time < entity.end;
            entity.element.classList.toggle('active', active);
            if (active && entity.kind === 'boundary') inPause = true;
        }
        root?.classList.toggle('in-pause', inPause);
    }

    export function scrollActiveIntoView(): void {
        root?.querySelector<HTMLElement>('[data-qc-word-id].active')
            ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function entityOf(target: EventTarget | null): WordEntity | null {
        let element = target instanceof HTMLElement ? target : null;
        while (element && element !== root) {
            const entity = entityByElement.get(element);
            if (entity) return entity;
            element = element.parentElement;
        }
        return null;
    }

    function seek(entity: WordEntity): void {
        dashPort.seek((entity.start + offsetSeconds()) * 1000);
        if (dashPort.paused) dashPort.play();
        updateHighlights();
    }

    const loopFor = (entity: WordEntity) => ({
        kind: 'word' as const,
        startSec: entity.start,
        endSec: entity.end,
        wordIndex: entity.wordIndex,
        childIndex: undefined,
    });

    function sameLoop(entity: WordEntity): boolean {
        const current = get(loopTarget);
        return current?.kind === 'word'
            && current.wordIndex === entity.wordIndex
            && Math.abs(current.startSec - entity.start) < 0.001;
    }

    /**
     * Stage a report. Only `timing` (on a word) and `silence` (on a gap) are
     * reachable: the footer disables the tajweed and phoneme categories for this
     * profile, because neither fact exists in a proxy-timed shard.
     */
    function stageReport(targetElement: Element, entity: WordEntity): boolean {
        const mode = get(reportMode);
        if (mode.kind === 'inactive') return false;
        if (targetElement.closest('.qc-context')) return true;
        const target = cellTargetFromEl(targetElement);
        if (!target) return true;
        const cellKey = targetCellKey(target);
        if (get(staged).has(cellKey)) {
            focusCell(cellKey);
            updateReportClasses();
            return true;
        }
        if (mode.kind === 'timing') {
            if (target.kind !== 'word') return true;
            upsertStaged({
                kind: 'timing', cellKey, target, wordIndex: entity.wordIndex,
                onset: null, offset: null, comment: '',
            });
            loopTarget.set(loopFor(entity));
        } else if (mode.kind === 'silence') {
            if (target.kind !== 'boundary') return true;
            upsertStaged({
                kind: 'silence', cellKey, target, gapWordIndex: entity.wordIndex,
                subtype: mode.subtype, onset: null, offset: null,
            });
        } else return true;
        focusCell(cellKey);
        updateReportClasses();
        return true;
    }

    function onClick(event: MouseEvent): void {
        const entity = entityOf(event.target);
        if (!entity) return;
        if (event.target instanceof Element && stageReport(event.target, entity)) return;
        if (entity.kind === 'boundary') return;
        if (clickTimer) clearTimeout(clickTimer);
        clickTimer = setTimeout(() => {
            if (get(loopTarget)) loopTarget.set(loopFor(entity));
            seek(entity);
        }, TS_CLICK_DELAY_MS);
    }

    function onDoubleClick(event: MouseEvent): void {
        const entity = entityOf(event.target);
        if (!entity || entity.kind === 'boundary') return;
        if (clickTimer) clearTimeout(clickTimer);
        if (sameLoop(entity)) loopTarget.set(null);
        else {
            loopTarget.set(loopFor(entity));
            seek(entity);
        }
    }

    function onKeyDown(event: KeyboardEvent): void {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const entity = entityOf(event.target);
        if (!entity) return;
        event.preventDefault();
        if (event.target instanceof Element && stageReport(event.target, entity)) return;
        if (entity.kind === 'boundary') return;
        if (event.key === ' ') {
            if (sameLoop(entity)) loopTarget.set(null);
            else loopTarget.set(loopFor(entity));
        }
        seek(entity);
    }

    function onPointerOver(event: PointerEvent): void {
        const entity = entityOf(event.target);
        // The hover bus only ever carries `word` for this profile — there is no
        // letter or phoneme span for the waveform to mirror.
        if (entity?.kind === 'word') {
            tsHoveredElement.set({ kind: 'word', startSec: entity.start, endSec: entity.end });
        }
    }

    function onPointerLeave(): void {
        tsHoveredElement.set(null);
    }

    onDestroy(() => {
        if (clickTimer) clearTimeout(clickTimer);
        tsHoveredElement.set(null);
    });
</script>

<div
    class="timed-analysis word-profile no-letters no-phonemes"
    role="toolbar"
    class:report-mode={$reportMode.kind !== 'inactive'}
    class:report-silence={$reportMode.kind === 'silence'}
    class:report-missed={$reportMode.kind === 'silence' && $reportMode.subtype === 'pause_missed'}
    class:report-existing={$reportMode.kind === 'silence' && $reportMode.subtype !== 'pause_missed'}
    bind:this={root}
    tabindex="-1"
    onclick={onClick}
    ondblclick={onDoubleClick}
    onkeydown={onKeyDown}
    onpointerover={onPointerOver}
    onpointerleave={onPointerLeave}
>
    {#each readings as reading (reading.id)}
        <div class="timed-reading" data-reading-id={reading.id}>
            {#each reading.words as word (word.id)}
                <span class="word-run">
                    <span
                        class="word-cell"
                        class:qc-context={isContext(word)}
                        data-qc-word-id={word.id}
                        title={String(Math.round(((word.end - word.start) * 1000) / 10) * 10) + ' ms'}
                    >
                        {#if $showTranslations}
                            <WordTranslation text={$verseTranslations[word.location] ?? ''} />
                        {/if}
                        <span class="word-text">{word.text}</span>
                    </span>
                    {#if word.boundary}
                        <span
                            class="boundary-tile"
                            class:qc-verse-end={word.boundary.verseEnd != null}
                            class:qc-sakt={word.boundary.state === 'sakt'}
                            class:qc-recorded-pause={recorded(word.boundary)}
                            class:qc-boundary-empty={gapText(word.boundary) === ''
                                && stopMark(word.boundary) == null}
                            data-qc-boundary-id={word.boundary.id}
                        >
                            <span
                                class="pause-bridge"
                                class:verse-mark={word.boundary.verseEnd != null}
                                class:stop-mark={stopMark(word.boundary) != null}
                            >{#if stopMark(word.boundary)}<span
                                    class="pause-waqf"
                                    style={qpcWaqfRenderStyle(stopMark(word.boundary) ?? '', qpcFace)}
                                >{ZWSP + (stopMark(word.boundary) ?? '')}</span>{:else}{gapText(word.boundary)}{/if}</span>
                        </span>
                    {/if}
                </span>
            {/each}
        </div>
    {/each}
</div>

<style>
    .timed-reading {
        display: contents;
    }
    .word-run {
        display: inline-flex;
        align-items: flex-end;
        gap: 6px;
    }
    /* The cells stylesheet pins the pause control to the tile's top edge, where
       the native row's word text sits. Here the tile stretches to the word cell,
       so centre the control on the word text instead. */
    .boundary-tile {
        justify-content: center;
    }
    .word-cell {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 2px;
        padding: 4px 6px;
        border-radius: 4px;
        background: var(--qc-cell-rest);
        color: var(--qc-word-ink);
        cursor: pointer;
        transition: background var(--qc-dur) var(--qc-ease), color var(--qc-dur) var(--qc-ease);
    }
    .word-cell:hover {
        background: var(--qc-cell-hover);
    }
    .word-text {
        font-family: var(--qc-connected);
        font-size: var(--analysis-word-font-size, 30px);
        line-height: 1.7;
    }
    /* The verse marker is Quran text, not chrome: typeset it in the edition's
       own face at word size. The packaged QPC faces draw the end-of-ayah
       ornament around the bare digit themselves, so a UI font here renders a
       naked numeral instead of the marker. */
    .pause-bridge.verse-mark {
        font-family: var(--qc-connected);
        font-size: var(--analysis-word-font-size, 30px);
        line-height: 1.7;
    }
    /* `.active` and `.loop` are toggled imperatively per frame, so Svelte's
       scoped-CSS pass never sees them on an element and would prune the rules. */
    :global(.timed-analysis.word-profile .word-cell.active) {
        background: var(--ts-word-deep, var(--anim-highlight-color));
        color: var(--hl-word-ink, var(--ink-on-color));
    }
    :global(.timed-analysis.word-profile .word-cell.loop) {
        outline: 2px solid var(--qc-accent-line);
        outline-offset: -2px;
    }
</style>
