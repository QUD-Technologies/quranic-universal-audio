import { describe, expect, it } from 'vitest';

import { decodeTimestampShard } from '../compact-shards';
import { chapterOccasions } from '../occasions';
import { WORD_SHARD, WORD_TEXTS } from '../test-word-fixture';
import { isWordShard, type TsWordShardResponse } from '../../types/ts-client';

describe('decodeTimestampShard — word profile', () => {
    it('narrows to the word profile on _meta.profile', () => {
        const shard = decodeTimestampShard(WORD_SHARD);
        expect(isWordShard(shard)).toBe(true);
    });

    it('treats an absent profile as native', () => {
        // A native object carries no discriminator; it must not be mistaken
        // for a word shard (it would render as an empty verse).
        expect(() => decodeTimestampShard({ _meta: { schema_version: 15 }, readings: [] }))
            .toThrowError(/Native schema is not v2/);
    });

    it('rejects a word shard that is not schema 14', () => {
        expect(() => decodeTimestampShard({
            ...WORD_SHARD,
            _meta: { ...WORD_SHARD._meta, schema_version: 13 },
        })).toThrowError(/Word shard schema 13 is unsupported/);
    });

    it('decodes each word row with its edition ref and exact text', () => {
        const shard = decodeTimestampShard(WORD_SHARD) as TsWordShardResponse;
        expect(shard.readings[0]!.words).toEqual([
            { ref: '112:1:1', text: WORD_TEXTS[0], start_ms: 0, end_ms: 700 },
            { ref: '112:1:2', text: WORD_TEXTS[1], start_ms: 800, end_ms: 1500 },
            { ref: '112:1:3', text: WORD_TEXTS[2], start_ms: 1500, end_ms: 2200 },
            { ref: '112:1:4', text: WORD_TEXTS[3], start_ms: 2200, end_ms: 2900 },
        ]);
    });

    it('derives boundary timing by the same rule as the native profile', () => {
        const shard = decodeTimestampShard(WORD_SHARD) as TsWordShardResponse;
        // Leading boundary spans part start -> first word start; internal
        // boundaries span previous word end -> next word start; the final one
        // runs from the last word end to the part edge.
        expect(shard.readings[0]!.timing.boundaries).toEqual([
            { boundary_id: 0, start_ms: 0, end_ms: 0 },
            { boundary_id: 1, start_ms: 700, end_ms: 800 },
            { boundary_id: 2, start_ms: 1500, end_ms: 1500 },
            { boundary_id: 3, start_ms: 2200, end_ms: 2200 },
            { boundary_id: 4, start_ms: 2900, end_ms: 3000 },
        ]);
    });

    it('decodes boundary state codes and verse ends', () => {
        const shard = decodeTimestampShard(WORD_SHARD) as TsWordShardResponse;
        expect(shard.readings[0]!.states).toEqual(['join', 'join', 'join', 'stop']);
        expect(shard.readings[0]!.verseEnds).toEqual([null, null, null, 1]);
    });

    it('rejects an unknown boundary state code', () => {
        expect(() => decodeTimestampShard({
            ...WORD_SHARD,
            readings: [{ ...WORD_SHARD.readings[0]!, boundaries: [[9, null], [1, null], [1, null], [3, 1]] }],
        })).toThrowError(/invalid boundary state 9/);
    });

    it('rejects a word/boundary count mismatch', () => {
        expect(() => decodeTimestampShard({
            ...WORD_SHARD,
            readings: [{ ...WORD_SHARD.readings[0]!, boundaries: [[1, null]] }],
        })).toThrowError(/word and boundary counts differ/);
    });

    it('splits into occasions like a native reading', () => {
        const shard = decodeTimestampShard(WORD_SHARD) as TsWordShardResponse;
        const occasions = chapterOccasions(shard.readings, 112);
        expect(occasions).toHaveLength(1);
        expect(occasions[0]!.ref).toBe('112:1');
        expect(occasions[0]!.parts[0]!.word_ids).toEqual([0, 1, 2, 3]);
    });
});
