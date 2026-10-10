/**
 * Hand-built shard v15 readings for tests, in the SDK's shapes (qua
 * `shard_schema.py` / `shard_variants.py`): a reading with a word-anchored
 * variant, and one with a timed mid-verse sakt sign.
 */

import type { TsReadingVariant, TsVariantDefinition } from '../types/generated/schemas';
import type { TsShardReading } from '../types/ts-client';
import { nativeReading } from './test-native-fixture';

export const V15_CATALOGUE: Record<string, TsVariantDefinition> = {
    istifham_article: {
        name: 'Istifham article',
        description: 'Hamzat al-wasl after the question hamza.',
        options: ['ibdal', 'tashil'],
        default: 'ibdal',
    },
};

/** Word 1 was read with tashil; ibdal would change its column 101 and sound 1. */
export const V15_VARIANT: TsReadingVariant = {
    id: 'istifham_article',
    chosen: 'tashil',
    words: [1],
    targets: [1],
    anchor: 'word',
    boundary: null,
    by: 'length',
    score: 0.31,
    affected: { ibdal: { c: [101], s: [1], b: [] } },
};

export function v15VariantReading(variant: TsReadingVariant = V15_VARIANT): TsShardReading {
    const reading = nativeReading('r1', [
        { ref: '6:143', start: 100, end: 300, text: 'a' },
        { ref: '6:143', start: 300, end: 500, text: 'b' },
        { ref: '6:143', start: 500, end: 700, text: 'c' },
    ]);
    return { ...reading, variants: [variant], variantCatalogue: V15_CATALOGUE };
}

export const SAKT_COLUMN_ID = 51;
export const SAKT_SPAN: [number, number] = [86_325, 86_700];

/** 75:27 read as مَنْ ۜ رَاقٍ: boundary 2 is a sakt whose sign column 51 is timed. */
export function v15SaktReading(timed = true): TsShardReading {
    const reading = nativeReading('r1', [
        { ref: '75:27', start: 85_500, end: 85_900, text: 'a' },
        { ref: '75:27', start: 85_900, end: SAKT_SPAN[0], text: 'b' },
        { ref: '75:27', start: SAKT_SPAN[1], end: 87_200, text: 'c' },
    ]);
    const boundaries = reading.wire.cells.cell_view.boundaries;
    boundaries.slice(0, -1).forEach((boundary) => { boundary.verse_end = null; });
    const sakt = boundaries[1]!;
    const sign = reading.wire.cells.cell_view.words[0]!.columns[0]!;
    sakt.state = 'sakt';
    sakt.columns = [{
        ...sign,
        id: SAKT_COLUMN_ID,
        role: 'stop_sign',
        text: 'ۜ',
        source_character_ids: [],
        source_unit_ids: [],
        slot_ids: [],
        owned_sound_ids: [],
    }];
    if (timed) {
        reading.timing.columns = [
            { column_id: SAKT_COLUMN_ID, start_ms: SAKT_SPAN[0], end_ms: SAKT_SPAN[1] },
        ];
    }
    return reading;
}
