import { parse } from '@quranic-phonemizer/cells';
import { describe, expect, it } from 'vitest';

import { nativeReading } from '../../../../lib/recitation-data/test-native-fixture';
import { defineInspectorRule } from '../tajweed-rules';
import {
    buildBoundaryPolicies,
    buildTimedEntityCache,
    columnReportSpans,
    columnSpans,
    type ParsedReading,
} from '../timed-entities';

function parsed(reading: ReturnType<typeof nativeReading>): ParsedReading {
    const result = parse(reading.wire, defineInspectorRule);
    return { reading, ...result };
}

describe('native timing ownership', () => {
    it('keeps source-unit timing for reports separate from sound playback timing', () => {
        const reading = nativeReading('r1', [{ ref: '1:1', start: 100, end: 400, text: 'a' }]);
        reading.animationTokens = [{
            id: 0, word_id: 0, source_unit_ids: [0], character_ids: [0],
            paint_character_ids: [0], text: 'a',
            sound_ids: [0], policy: 'timed', target_token_id: null, start_ms: 120, end_ms: 280,
        }];
        reading.timing.sounds = [{ sound_id: 0, start_ms: 150, end_ms: 350 }];
        const item = parsed(reading);
        const playback = columnSpans(item);
        const reports = columnReportSpans(item, playback);
        const columnId = String(reading.wire.cells.cell_view.words[0]!.columns[0]!.id);

        expect(playback.get(columnId)).toEqual([150, 350]);
        expect(reports.get(columnId)).toEqual([120, 350]);
    });

    it('times merger presenters but never a silent presenter', () => {
        const reading = nativeReading('r1', [
            { ref: '1:1', start: 100, end: 200, text: 'a' },
            { ref: '1:1', start: 200, end: 400, text: 'b' },
        ]);
        const first = reading.wire.cells.cell_view.words[0]!.columns[0]!;
        const second = reading.wire.cells.cell_view.words[1]!.columns[0]!;
        first.owned_sound_ids = [0];
        first.presented_sound_ids = [1];
        second.owned_sound_ids = [];
        second.presented_sound_ids = [1];
        reading.wire.cells.cell_view.words[1]!.sounds[0]!.column_ids = [
            Number(first.id), Number(second.id),
        ];

        let spans = columnSpans(parsed(reading));
        expect(spans.get(String(first.id))).toEqual([100, 400]);
        expect(spans.get(String(second.id))).toEqual([200, 400]);

        second.silence = 'orthographic_silence';
        spans = columnSpans(parsed(reading));
        expect(spans.has(String(second.id))).toBe(false);

        reading.timing.columns = [{
            column_id: first.id, start_ms: 130, end_ms: 260,
        }];
        spans = columnSpans(parsed(reading));
        expect(spans.get(String(first.id))).toEqual([130, 260]);

        reading.timing.columns = [{
            column_id: first.id, start_ms: null, end_ms: null,
        }];
        spans = columnSpans(parsed(reading));
        expect(spans.has(String(first.id))).toBe(false);
    });

    it('uses the shard boundary span and keeps verse markers static', () => {
        const first = parsed(nativeReading('r1', [
            { ref: '1:1', start: 100, end: 200, text: 'a' },
        ]));
        first.view.boundaries[0]!.verse_end = null;
        first.reading.timing.boundaries = [{
            boundary_id: 1, start_ms: 200, end_ms: 350,
        }];
        const second = parsed(nativeReading('r2', [
            { ref: '1:1', start: 350, end: 500, text: 'b' },
        ]));
        const policies = buildBoundaryPolicies([first, second]);

        expect(policies.get('r1')?.get('1')).toMatchObject({
            span: [200, 350], recordedPause: true, showMarker: true, verseEnd: false,
        });
        expect(policies.get('r2')?.get('1')).toMatchObject({
            recordedPause: false, showMarker: true, verseEnd: true,
        });
    });

    it('indexes an untimed native rule target for report mode', () => {
        const reading = nativeReading('r1', [
            { ref: '1:1', start: 100, end: 200, text: 'a' },
        ]);
        const word = reading.wire.cells.cell_view.words[0]!;
        const column = word.columns[0]!;
        const columnId = String(column.id);
        column.owned_sound_ids = [];
        column.presented_sound_ids = [];
        column.rule_occurrence_ids = [7];
        reading.wire.analysis.result.words[0]!.sound_ids = [];
        reading.wire.analysis.result.rule_occurrences = [{
            id: 7,
            rule_id: 'lam_shamsiyyah',
        }];
        word.sounds = [];
        word.groups = [{
            key: column.id,
            kind: 'base',
            column_ids: [column.id],
            sound_ids: [],
        }];

        const root = document.createElement('div');
        root.innerHTML = `
            <div data-reading-index="0">
                <div data-qc-word-id="0">
                    <div data-qc-group-key="${columnId}">
                        <span data-qc-column-id="${columnId}"></span>
                    </div>
                </div>
            </div>
        `;
        const item = parsed(reading);
        const cache = buildTimedEntityCache(root, [item], [{
            location: '1:1:1',
            text: 'a',
            start: 0.1,
            end: 0.2,
            phoneme_indices: [],
            letters: [],
        }], 0);

        expect(cache.entities.map((entity) => `${entity.kind}:${entity.id}`)).toEqual(
            expect.arrayContaining([`column:${columnId}`, `group:${columnId}`]),
        );
    });
});
