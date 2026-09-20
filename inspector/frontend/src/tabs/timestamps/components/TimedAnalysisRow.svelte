<script lang="ts">
    import {
        AnalysisRow,
        parse,
        stripBoundaryMarks,
        type CellBoundary,
        type HostClasses,
    } from '@quranic-phonemizer/cells';
    import { get } from 'svelte/store';
    import { onDestroy, tick } from 'svelte';

    import { dashPort } from '../../../lib/playback/dash-port';
    import { nativePayload } from '../../../lib/types/ts-client';
    import { i18n } from '$lib/i18n/locale.svelte';
    import {
        lettersVisible,
        phonemesVisible,
        wipeActive,
        tsHoveredElement,
        tsWaveformHoverTime,
    } from '../stores/display';
    import { focusWaslGroup, loadedVerse } from '../stores/verse';
    import { loopTarget } from '../stores/playback';
    import {
        focusCell,
        focusedCellKey,
        reportMode,
        staged,
        upsertStaged,
    } from '../stores/report-mode';
    import { currentVerseReports } from '../stores/ts-reports';
    import { tajweedSettings } from '../stores/tajweed-settings';
    import { TS_CLICK_DELAY_MS } from '../utils/constants';
    import {
        buildBoundaryPolicies,
        buildTimedEntityCache,
        type BoundaryPolicy,
        type ParsedReading,
        type TimedEntity,
    } from '../utils/timed-entities';
    import { defineInspectorRule, ruleLabel } from '../utils/tajweed-rules';
    import {
        cellTargetFromEl,
        ruleIdsFromEl,
        targetCellKey,
    } from '../utils/report-target';

    let root: HTMLDivElement;
    let entities: TimedEntity[] = [];
    let entityByElement = new Map<HTMLElement, TimedEntity>();
    let clickTimer: ReturnType<typeof setTimeout> | null = null;
    let rowGap = $state(16);
    let tipText = $state<string | null>(null);
    let tipX = $state(0);
    let tipY = $state(0);
    let tipElement: HTMLElement | null = null;
    let tipWarm = false;
    let tipShowTimer: ReturnType<typeof setTimeout> | null = null;
    let tipCoolTimer: ReturnType<typeof setTimeout> | null = null;

    type DisplayReading = ParsedReading & {
        boundaryPolicies: Map<string, BoundaryPolicy>;
    };

    const displayData = $derived($focusWaslGroup?.data ?? $loadedVerse?.data ?? null);
    const focusRef = $derived($focusWaslGroup?.focusRef ?? $loadedVerse?.data.verse_ref ?? '');
    const disabled = $derived(new Set(
        Object.entries($tajweedSettings).filter(([, value]) => !value.enabled).map(([key]) => key),
    ));

    const parsed = $derived.by((): DisplayReading[] => {
        void i18n.locale;
        const data = displayData;
        const text = new Map(data?.words.map((word) => [word.location, word.display_text]) ?? []);
        const readings: ParsedReading[] = data?.native.map((reading) => {
            const result = parse(nativePayload(reading), defineInspectorRule, {
                iqlabTanween: 'mini-meem',
                iqlabNoon: 'mini-meem',
                openTanween: true,
            });
            result.context.disabled = disabled;
            return { reading, ...result };
        }) ?? [];
        const policies = buildBoundaryPolicies(readings);
        return readings.map((item) => {
            const boundaryPolicies = policies.get(item.reading.id) ?? new Map();
            item.view.words.forEach((word, index) => {
                const boundary = item.view.boundaries[index];
                const sourceText = text.get(word.location) ?? word.display_text;
                const policy = boundary && boundaryPolicies.get(String(boundary.boundary_id));
                word.display_text = stripBoundaryMarks(
                    sourceText,
                    policy?.showMarker ? boundary : undefined,
                );
            });
            return { ...item, boundaryPolicies };
        });
    });

    const verseOf = (location: string): string => location.split(':').slice(0, 2).join(':');
    const wordClass = (word: { location: string }): string =>
        verseOf(word.location) === focusRef ? '' : 'qc-context';

    function boundaryClass(item: DisplayReading, boundary: CellBoundary): string {
        const policy = item.boundaryPolicies.get(String(boundary.boundary_id));
        const hasContent = boundary.bridges.length > 0
            || boundary.columns.some((column) => column.role !== 'stop_sign');
        return [
            policy?.showMarker ? '' : 'qc-boundary-hidden',
            !policy?.showMarker && !hasContent ? 'qc-boundary-empty' : '',
            policy?.recordedPause ? 'qc-recorded-pause' : '',
            policy?.verseEnd ? 'qc-verse-end' : '',
            policy?.sakt ? 'qc-sakt' : '',
        ].filter(Boolean).join(' ');
    }

    const hostClasses = (item: DisplayReading): HostClasses => ({
        word: wordClass,
        boundary: (boundary) => boundaryClass(item, boundary),
    });

    const keepBoundaryWithNext = (item: DisplayReading) =>
        (boundary: CellBoundary): boolean => Boolean(
            item.boundaryPolicies.get(String(boundary.boundary_id))?.recordedPause,
        );

    function offsetSeconds(): number {
        const group = get(focusWaslGroup);
        if (group) return group.span[0] / 1000;
        return get(loadedVerse)?.tsSegOffset ?? 0;
    }

    function rebuildCache(): void {
        const cache = buildTimedEntityCache(
            root,
            parsed,
            displayData?.words ?? [],
            offsetSeconds(),
        );
        entities = cache.entities;
        entityByElement = cache.byElement;
        for (const entity of entities) {
            if (entity.kind === 'boundary') continue;
            entity.element.tabIndex = 0;
            entity.element.setAttribute('role', 'button');
        }
    }

    function recomputeRowGap(): void {
        if (!root) return;
        const units = root.querySelectorAll<HTMLElement>('.word-run');
        if (units.length < 2) {
            rowGap = 16;
            return;
        }
        const style = getComputedStyle(root);
        const width = root.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        const rows = new Map<number, { free: number; count: number }>();
        units.forEach((unit) => {
            const box = unit.getBoundingClientRect();
            const key = [...rows.keys()].find((row) => Math.abs(row - box.bottom) <= 1)
                ?? Math.round(box.bottom);
            const value = rows.get(key) ?? { free: width, count: 0 };
            value.free -= box.width;
            value.count += 1;
            rows.set(key, value);
        });
        const candidates = [...rows.values()]
            .filter((row) => row.count > 1)
            .map((row) => row.free / (row.count - 1));
        const next = candidates.length ? Math.min(...candidates) : 16;
        rowGap = Math.max(16, Math.min(next, 40));
    }

    function updateReportClasses(): void {
        const mode = $reportMode;
        const reports = new Map($currentVerseReports
            .filter((report) => report.status === 'open')
            .map((report) => [targetCellKey(report.target), report]));
        for (const entity of entities) {
            const key = `${entity.readingId}:${entity.kind}:${entity.id}`;
            const report = reports.get(key);
            entity.element.classList.toggle('report-flag-public', Boolean(report));
            entity.element.classList.toggle('report-flag-staged', $staged.has(key));
            entity.element.classList.toggle('report-focused', $focusedCellKey === key);
            if (report) entity.element.dataset.qcReportCategory = report.category;
            else delete entity.element.dataset.qcReportCategory;
            if (entity.kind === 'boundary') {
                const targetable = mode.kind === 'silence';
                entity.element.tabIndex = targetable ? 0 : -1;
                if (targetable) entity.element.setAttribute('role', 'button');
                else entity.element.removeAttribute('role');
            }
        }
    }

    function updateLoopClasses(): void {
        const target = $loopTarget;
        for (const entity of entities) {
            if (!entity.timed) {
                entity.element.classList.remove('loop');
                continue;
            }
            const kindMatches = target?.kind === 'word'
                ? entity.kind === 'word'
                : target?.kind === 'phoneme'
                    ? entity.kind === 'sound'
                    : entity.kind === 'column';
            const identityMatches = target?.kind === 'word'
                ? entity.wordIndex === target.wordIndex
                : entity.childIndex === target?.childIndex;
            entity.element.classList.toggle(
                'loop', Boolean(target && kindMatches && identityMatches),
            );
        }
    }

    $effect(() => {
        void parsed;
        void displayData;
        void tick().then(() => {
            rebuildCache();
            updateReportClasses();
            updateLoopClasses();
            recomputeRowGap();
        });
    });

    $effect(() => {
        void $lettersVisible;
        void $phonemesVisible;
        void tick().then(recomputeRowGap);
    });

    $effect(() => {
        if (!root || typeof ResizeObserver === 'undefined') return;
        const observer = new ResizeObserver(recomputeRowGap);
        observer.observe(root);
        if (document.fonts) void document.fonts.ready.then(recomputeRowGap);
        return () => observer.disconnect();
    });

    $effect(() => {
        void $currentVerseReports;
        void $staged;
        void $focusedCellKey;
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

    export function updateHighlights(): void {
        const time = currentTime();
        const wipe = get(wipeActive);
        for (const entity of entities) {
            if (!entity.timed) {
                entity.element.classList.remove('active');
                entity.element.style.removeProperty('--fill');
                continue;
            }
            const active = time >= entity.start && time < entity.end;
            entity.element.classList.toggle('active', active);
            const tracks = entity.kind === 'column' || entity.kind === 'sound';
            if (wipe && active && tracks) {
                const fill = (time - entity.start) / Math.max(entity.end - entity.start, 0.001);
                entity.element.style.setProperty('--fill', String(Math.max(0, Math.min(fill, 1))));
            } else entity.element.style.removeProperty('--fill');
        }
        root?.classList.toggle('in-pause', entities.some((entity) =>
            entity.kind === 'boundary' && time >= entity.start && time < entity.end,
        ));
    }

    export function scrollActiveIntoView(): void {
        root?.querySelector<HTMLElement>('[data-qc-word-id].active')
            ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function entityOf(target: EventTarget | null): TimedEntity | null {
        let element = target instanceof HTMLElement ? target : null;
        while (element && element !== root) {
            const entity = entityByElement.get(element);
            if (entity) return entity;
            element = element.parentElement;
        }
        return null;
    }

    function seek(entity: TimedEntity): void {
        dashPort.seek((entity.start + offsetSeconds()) * 1000);
        if (dashPort.paused) dashPort.play();
        updateHighlights();
    }

    function loopFor(entity: TimedEntity, report = false) {
        return {
            kind: entity.kind === 'sound' || entity.kind === 'bridge' ? 'phoneme' as const
                : entity.kind === 'word' ? 'word' as const : 'letter' as const,
            startSec: report ? entity.reportStart : entity.start,
            endSec: report ? entity.reportEnd : entity.end,
            wordIndex: entity.wordIndex,
            childIndex: entity.childIndex,
        };
    }

    function sameLoop(entity: TimedEntity): boolean {
        const current = get(loopTarget);
        const next = loopFor(entity);
        return current?.kind === next.kind
            && current.wordIndex === next.wordIndex
            && current.childIndex === next.childIndex
            && Math.abs(current.startSec - next.startSec) < 0.001
            && Math.abs(current.endSec - next.endSec) < 0.001;
    }

    function stageReport(targetElement: Element, entity: TimedEntity): boolean {
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
            if (!entity.reportTimed) return true;
            if (!['word', 'column', 'group', 'sound', 'bridge'].includes(target.kind)) return true;
            upsertStaged({
                kind: 'timing', cellKey, target, wordIndex: entity.wordIndex,
                onset: null, offset: null, comment: '',
            });
            loopTarget.set(loopFor(entity, true));
        } else if (mode.kind === 'tajweed') {
            if (!['column', 'group', 'bridge'].includes(target.kind)) return true;
            const rules = ruleIdsFromEl(targetElement);
            if (mode.subtype === 'wrong_rule' && rules.length === 0) return true;
            upsertStaged({
                kind: 'tajweed', cellKey, target, subtype: mode.subtype,
                wordIndex: entity.wordIndex, ruleOptions: rules,
                selectedRuleTags: [], comment: '',
            });
        } else if (mode.kind === 'phonemes') {
            if (target.kind !== 'sound' && target.kind !== 'bridge') return true;
            upsertStaged({
                kind: 'phonemes', cellKey, target, wordIndex: entity.wordIndex,
                glyph: entity.element.textContent?.trim() ?? '',
            });
        } else {
            if (target.kind !== 'boundary') return true;
            upsertStaged({
                kind: 'silence', cellKey, target, gapWordIndex: entity.wordIndex,
                subtype: mode.subtype, onset: null, offset: null,
            });
        }
        focusCell(cellKey);
        updateReportClasses();
        return true;
    }

    function onClick(event: MouseEvent): void {
        const entity = entityOf(event.target);
        if (!entity) return;
        if (!entity.timed && get(reportMode).kind === 'inactive') return;
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
        if (!entity || !entity.timed || entity.kind === 'boundary') return;
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
        if (!entity.timed && get(reportMode).kind === 'inactive') return;
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
        if (entity?.timed) {
            tsHoveredElement.set({
                kind: entity.kind === 'sound' || entity.kind === 'bridge' ? 'phoneme' :
                    entity.kind === 'word' ? 'word' : 'letter',
                startSec: entity.start,
                endSec: entity.end,
            });
        }
        const ruleTarget = event.target instanceof Element
            ? event.target.closest<HTMLElement>('[data-qc-rule-ids]')
            : null;
        const target = ruleTarget ?? entity?.element ?? null;
        if (!target) {
            hideTip(false);
            return;
        }
        if (target === tipElement) return;
        hideTip(false);
        tipElement = target;
        const rules = (target.dataset.qcRuleIds ?? '').split(' ').filter(Boolean);
        const ownsTiming = entity?.timed && (
            target === entity.element || target.contains(entity.element)
        );
        const duration = ownsTiming
            ? `${Math.round(((entity.end - entity.start) * 1000) / 10) * 10} ms`
            : null;
        const lines = [duration, ...rules.map(ruleLabel)].filter(Boolean) as string[];
        if (!lines.length) return;
        if (tipCoolTimer) {
            clearTimeout(tipCoolTimer);
            tipCoolTimer = null;
        }
        const show = () => {
            if (!target.isConnected || tipElement !== target) return;
            const box = target.getBoundingClientRect();
            tipX = box.left + box.width / 2;
            tipY = box.top;
            tipText = lines.join('\n');
            tipWarm = true;
        };
        if (tipWarm) show();
        else tipShowTimer = setTimeout(show, 500);
    }

    function hideTip(cool = true): void {
        if (tipShowTimer) clearTimeout(tipShowTimer);
        tipShowTimer = null;
        tipText = null;
        tipElement = null;
        if (!cool) return;
        if (tipCoolTimer) clearTimeout(tipCoolTimer);
        tipCoolTimer = setTimeout(() => {
            tipWarm = false;
            tipCoolTimer = null;
        }, 2000);
    }

    function onPointerLeave(): void {
        tsHoveredElement.set(null);
        hideTip();
    }

    onDestroy(() => {
        if (clickTimer) clearTimeout(clickTimer);
        if (tipShowTimer) clearTimeout(tipShowTimer);
        if (tipCoolTimer) clearTimeout(tipCoolTimer);
        tsHoveredElement.set(null);
    });
</script>

<div
    class="timed-analysis"
    role="toolbar"
    class:no-letters={!$lettersVisible}
    class:no-phonemes={!$phonemesVisible}
    class:hl-track={$wipeActive}
    class:report-mode={$reportMode.kind !== 'inactive'}
    class:report-silence={$reportMode.kind === 'silence'}
    class:report-missed={$reportMode.kind === 'silence' && $reportMode.subtype === 'pause_missed'}
    class:report-existing={$reportMode.kind === 'silence' && $reportMode.subtype !== 'pause_missed'}
    style:--inspector-row-gap={`${rowGap}px`}
    bind:this={root}
    tabindex="-1"
    onclick={onClick}
    ondblclick={onDoubleClick}
    onkeydown={onKeyDown}
    onpointerover={onPointerOver}
    onpointerleave={onPointerLeave}
>
    {#each parsed as item, index (item.reading.id)}
        <div class="timed-reading" data-reading-id={item.reading.id} data-reading-index={index}>
            <AnalysisRow
                view={item.view}
                context={item.context}
                crossWordMergers="compact"
                verseFlow="inline"
                showTooltips={false}
                hostClasses={hostClasses(item)}
                keepBoundaryWithNext={keepBoundaryWithNext(item)}
            />
        </div>
    {/each}
</div>

{#if tipText}
    <div class="cell-tip" dir="ltr" style:left={`${tipX}px`} style:top={`${tipY}px`} role="tooltip">
        {#each tipText.split('\n') as line (line)}
            <div class:tip-rule={!line.endsWith(' ms')}>{line}</div>
        {/each}
    </div>
{/if}
