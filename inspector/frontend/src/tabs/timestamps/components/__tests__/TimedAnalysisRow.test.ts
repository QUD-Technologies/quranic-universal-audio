import { cleanup, fireEvent, render, waitFor } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashPort } from '../../../../lib/playback/dash-port';
import { chapterOccasions } from '../../../../lib/recitation-data/occasions';
import { nativeReading } from '../../../../lib/recitation-data/test-native-fixture';
import { assembleWaslGroup } from '../../../../lib/recitation-data/ts-source';
import {
    enterTajweed,
    enterTiming,
    exitReportMode,
    staged,
} from '../../stores/report-mode';
import { focusWaslGroup, loadedVerse } from '../../stores/verse';
import { loopTarget } from '../../stores/playback';
import TimedAnalysisRow from '../TimedAnalysisRow.svelte';

describe('TimedAnalysisRow native renderer integration', () => {
    beforeEach(() => {
        loadedVerse.set(null);
        focusWaslGroup.set(null);
    });

    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
        exitReportMode();
        loadedVerse.set(null);
        focusWaslGroup.set(null);
    });

    it('highlights a verse marker for the recorded pause after its final word', async () => {
        const reading = nativeReading('r1', [
            { ref: '1:1', start: 100, end: 300, text: 'a' },
        ]);
        reading.parts[0]!.t[1] = 500;
        reading.timing.boundaries = [{
            boundary_id: 1, start_ms: 300, end_ms: 500,
        }];
        const data = assembleWaslGroup(
            'r', chapterOccasions([reading], 1), '1:1', {}, {}, { audio_category: 'by_surah' }, '',
        );
        loadedVerse.set({ data, tsSegOffset: 0.1, tsSegEnd: 0.5 });
        vi.spyOn(dashPort, 'currentTimeMs').mockReturnValue(400);

        const { component, container } = render(TimedAnalysisRow);
        await waitFor(() => expect(container.querySelector('.verse-mark')).not.toBeNull());
        (component as unknown as { updateHighlights: () => void }).updateHighlights();

        expect(container.querySelector('[data-qc-boundary-id]')?.classList).toContain('active');
    });

    it('renders connected verses with native hooks, a verse marker, and context opacity', async () => {
        const reading = nativeReading('r1', [
            { ref: '1:3', start: 100, end: 1_000, text: 'a' },
            { ref: '1:4', start: 1_000, end: 2_000, text: 'b' },
        ]);
        const occasions = chapterOccasions([reading], 1);
        const data = assembleWaslGroup(
            'r', occasions, '1:3', {}, {}, { audio_category: 'by_surah' }, '',
        );
        loadedVerse.set({ data, tsSegOffset: 0.1, tsSegEnd: 2 });
        focusWaslGroup.set({
            data,
            span: [100, 2_000],
            refs: ['1:3', '1:4'],
            focusRef: '1:3',
        });
        const { container } = render(TimedAnalysisRow);
        await waitFor(() => expect(container.querySelectorAll('[data-qc-word-id]')).toHaveLength(2));
        const words = container.querySelectorAll<HTMLElement>('[data-qc-word-id]');
        expect(words[0]!.classList.contains('qc-context')).toBe(false);
        expect(words[1]!.classList.contains('qc-context')).toBe(true);
        expect(container.querySelectorAll('[data-qc-column-id]')).toHaveLength(2);
        expect(container.querySelectorAll('[data-qc-sound-id]')).toHaveLength(2);
        expect(container.querySelector('.verse-glyph')).not.toBeNull();
    });

    it('shows the native merger rule when its compact bridge phoneme is hovered', async () => {
        const reading = nativeReading('r1', [
            { ref: '1:1', start: 100, end: 300, text: 'a' },
            { ref: '1:1', start: 300, end: 500, text: 'b' },
        ]);
        const firstColumn = Number(reading.wire.cells.cell_view.words[0]!.columns[0]!.id);
        const secondColumn = Number(reading.wire.cells.cell_view.words[1]!.columns[0]!.id);
        reading.wire.analysis.result.rule_occurrences = [{
            id: 7,
            rule_id: 'idgham_mutamathilayn',
        }];
        reading.wire.cells.cell_view.boundaries[0]!.bridges = [{
            merger_id: 9,
            before_column_ids: [firstColumn],
            after_column_ids: [secondColumn],
            sound: { sound_id: 1, column_ids: [firstColumn, secondColumn], rule_occurrence_ids: [7] },
        }];
        const data = assembleWaslGroup(
            'r', chapterOccasions([reading], 1), '1:1', {}, {}, { audio_category: 'by_surah' }, '',
        );
        loadedVerse.set({ data, tsSegOffset: 0.1, tsSegEnd: 0.5 });

        const { container } = render(TimedAnalysisRow);
        await waitFor(() => expect(container.querySelector('[data-qc-bridge-id="9"]')).not.toBeNull());
        const bridgeSound = container.querySelector<HTMLElement>(
            '[data-qc-bridge-id="9"] [data-qc-sound-id="1"]',
        )!;
        await fireEvent.pointerOver(bridgeSound.querySelector('.ph-base')!);

        await waitFor(
            () => expect(container.querySelector('.cell-tip')?.textContent)
                .toContain('Idgham Mutamathilayn'),
            { timeout: 1_000 },
        );
    });

    it.each([
        ['lam_shamsiyyah', 'Lam Shamsiyyah'],
        ['waqf_silah_drop', 'Waqf Silah Drop'],
    ])('shows %s on an untimed native column', async (ruleId, label) => {
        const reading = nativeReading('r1', [
            { ref: '1:1', start: 100, end: 300, text: 'a' },
        ]);
        const word = reading.wire.cells.cell_view.words[0]!;
        const column = word.columns[0]!;
        const sounding = {
            ...column,
            id: Number(column.id) + 1,
            text: 'b',
            owned_sound_ids: [0],
            rule_occurrence_ids: [],
            silence: null,
        };
        reading.wire.analysis.result.rule_occurrences = [{ id: 7, rule_id: ruleId }];
        column.owned_sound_ids = [];
        column.rule_occurrence_ids = [7];
        column.silence = 7;
        word.columns = [column, sounding];
        word.sounds[0]!.column_ids = [Number(sounding.id)];
        word.groups = [
            { key: column.id, kind: 'base', column_ids: [column.id], sound_ids: [] },
            { key: sounding.id, kind: 'base', column_ids: [sounding.id], sound_ids: [0] },
        ];

        const data = assembleWaslGroup(
            'r', chapterOccasions([reading], 1), '1:1', {}, {}, { audio_category: 'by_surah' }, '',
        );
        loadedVerse.set({ data, tsSegOffset: 0.1, tsSegEnd: 0.3 });

        const { container } = render(TimedAnalysisRow);
        const selector = `[data-qc-column-id="${String(column.id)}"]`;
        await waitFor(() => expect(container.querySelector(selector)).not.toBeNull());
        await fireEvent.pointerOver(container.querySelector(`${selector} .cell-ink`)!);

        await waitFor(
            () => expect(container.querySelector('.cell-tip')?.textContent).toContain(label),
            { timeout: 1_000 },
        );
    });

    it('does not highlight an untimed silent column during its owner word', async () => {
        const reading = nativeReading('r1', [
            { ref: '1:1', start: 100, end: 300, text: 'a' },
        ]);
        const word = reading.wire.cells.cell_view.words[0]!;
        const silent = word.columns[0]!;
        const sounding = {
            ...silent,
            id: Number(silent.id) + 1,
            text: 'b',
            owned_sound_ids: [0],
            rule_occurrence_ids: [],
            silence: null,
        };
        silent.owned_sound_ids = [];
        silent.presented_sound_ids = [];
        silent.silence = 7;
        word.columns = [silent, sounding];
        word.sounds[0]!.column_ids = [Number(sounding.id)];
        word.groups = [
            { key: silent.id, kind: 'base', column_ids: [silent.id], sound_ids: [] },
            { key: sounding.id, kind: 'base', column_ids: [sounding.id], sound_ids: [0] },
        ];
        reading.wire.analysis.result.rule_occurrences = [{
            id: 7,
            rule_id: 'lam_shamsiyyah',
        }];
        const data = assembleWaslGroup(
            'r', chapterOccasions([reading], 1), '1:1', {}, {}, { audio_category: 'by_surah' }, '',
        );
        loadedVerse.set({ data, tsSegOffset: 0.1, tsSegEnd: 0.3 });
        vi.spyOn(dashPort, 'currentTimeMs').mockReturnValue(200);

        const { component, container } = render(TimedAnalysisRow);
        const silentSelector = `[data-qc-column-id="${String(silent.id)}"]`;
        const soundingSelector = `[data-qc-column-id="${String(sounding.id)}"]`;
        await waitFor(() => expect(container.querySelector(silentSelector)).not.toBeNull());
        (component as unknown as { updateHighlights: () => void }).updateHighlights();

        expect(container.querySelector(soundingSelector)?.classList).toContain('active');
        expect(container.querySelector(silentSelector)?.classList).not.toContain('active');
    });

    it('stages an untimed native column for a tajweed report', async () => {
        const reading = nativeReading('r1', [
            { ref: '1:1', start: 100, end: 300, text: 'a' },
        ]);
        const word = reading.wire.cells.cell_view.words[0]!;
        const column = word.columns[0]!;
        const columnId = String(column.id);
        column.owned_sound_ids = [];
        column.presented_sound_ids = [];
        column.rule_occurrence_ids = [7];
        word.sounds = [];
        word.groups = [{
            key: column.id,
            kind: 'base',
            column_ids: [column.id],
            sound_ids: [],
        }];
        reading.wire.analysis.result.words[0]!.sound_ids = [];
        reading.wire.analysis.result.rule_occurrences = [{
            id: 7,
            rule_id: 'lam_shamsiyyah',
        }];
        const data = assembleWaslGroup(
            'r', chapterOccasions([reading], 1), '1:1', {}, {}, { audio_category: 'by_surah' }, '',
        );
        loadedVerse.set({ data, tsSegOffset: 0.1, tsSegEnd: 0.3 });
        enterTajweed('r', '1:1', 'wrong_rule');

        const { container } = render(TimedAnalysisRow);
        const selector = `[data-qc-column-id="${columnId}"]`;
        await waitFor(() => expect(container.querySelector(selector)).not.toBeNull());
        await fireEvent.click(container.querySelector(`${selector} .cell-ink`)!);

        await waitFor(() => {
            const key = `r1:column:${columnId}`;
            expect(get(staged).has(key)).toBe(true);
        });
    });

    it('stages a timed native column for a timing report', async () => {
        const reading = nativeReading('r1', [
            { ref: '1:1', start: 100, end: 400, text: 'a' },
        ]);
        reading.animationTokens = [{
            id: 0, word_id: 0, source_unit_ids: [0], character_ids: [0],
            paint_character_ids: [0], text: 'a',
            sound_ids: [0], policy: 'timed', target_token_id: null, start_ms: 120, end_ms: 280,
        }];
        reading.timing.sounds = [{ sound_id: 0, start_ms: 150, end_ms: 350 }];
        reading.timing.words = [{ word_id: 0, start_ms: 100, end_ms: 400 }];
        const columnId = String(reading.wire.cells.cell_view.words[0]!.columns[0]!.id);
        const data = assembleWaslGroup(
            'r', chapterOccasions([reading], 1), '1:1', {}, {}, { audio_category: 'by_surah' }, '',
        );
        loadedVerse.set({ data, tsSegOffset: 0.1, tsSegEnd: 0.4 });
        enterTiming('r', '1:1');

        const { container } = render(TimedAnalysisRow);
        const selector = `[data-qc-column-id="${columnId}"]`;
        await waitFor(() => expect(container.querySelector(selector)).not.toBeNull());
        await fireEvent.click(container.querySelector(`${selector} .cell-ink`)!);

        await waitFor(() => {
            const key = `r1:column:${columnId}`;
            expect(get(staged).has(key)).toBe(true);
            expect(get(loopTarget)?.startSec).toBeCloseTo(0.02, 5);
            expect(get(loopTarget)?.endSec).toBeCloseTo(0.25, 5);
        });
    });
});
