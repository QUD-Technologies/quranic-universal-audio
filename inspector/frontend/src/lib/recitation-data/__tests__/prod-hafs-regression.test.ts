/**
 * E2E gate check: a REAL production Hafs shard (restamped 15) must decode unchanged
 * through the multi-riwayah decoder — same verse numbering, native profile,
 * phonemizer cells intact.
 */
import { describe, expect, it } from 'vitest';

import prodShard from './fixtures/prod-hafs-106.json';
import { decodeTimestampShard } from '../compact-shards';
import { isWordShard } from '../../types/ts-client';

describe('prod Hafs native shard', () => {
    it('decodes as the native profile with Hafs verse numbering', () => {
        const meta = prodShard._meta as Record<string, unknown>;
        expect(meta.schema_version).toBe(15);
        expect(meta.profile).toBeUndefined();
        expect(meta.riwayah).toBeUndefined();

        const decoded = decodeTimestampShard(prodShard);
        expect(isWordShard(decoded)).toBe(false);
        expect(decoded.readings).toHaveLength(4);

        const refs = decoded.readings.flatMap((r) => r.parts.map((p) => p.ref));
        expect(refs).toEqual(['106:1', '106:2', '106:3', '106:4']);

        // Native profile keeps its phonemizer cells and column timing; the word
        // profile has neither.
        const first = decoded.readings[0] as unknown as {
            wire: Record<string, unknown>;
        };
        expect(Object.keys(first.wire).length).toBeGreaterThan(0);
        expect(decoded.readings.some((r) => (r as unknown as {
            timing: { sounds: unknown[] };
        }).timing.sounds.length > 0)).toBe(true);
    });
});
