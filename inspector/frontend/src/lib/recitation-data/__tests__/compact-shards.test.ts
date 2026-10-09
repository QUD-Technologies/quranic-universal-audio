import { describe, expect, it } from 'vitest';

import { decodeTimestampShard } from '../compact-shards';

function reading(
    id: string,
    refs: string[],
    parts: Array<[number, number, number, number]>,
    words: Array<[number, number]>,
) {
    return {
        id,
        parts: parts.map(([start, end, first, count], index) => [
            refs[index], start, end, first, count,
        ]),
        render: {
            v: 1,
            m: ['test', 'canonical', 'native'],
            p: [],
            r: [],
            w: words.map((_, index) => [
                `${refs[Math.min(index, refs.length - 1)]}:1`, 'x', [], [], [], [], [],
            ]),
            b: words.map(() => [3, [], [], [], 1, null]),
            a: [],
        },
        timing: { w: words, s: [], a: [], c: [] },
    };
}

const meta = {
    schema_version: 13,
    native_schema_version: 2,
    renderer_codec_version: 1,
};

describe('compact timestamp shard pauses', () => {
    it.each(['1:1', '1:2'])(
        'preserves an inter-reading word gap before %s',
        (followingRef) => {
        const shard = decodeTimestampShard({
            _meta: meta,
            readings: [
                reading('r1', ['1:1'], [[100, 200, 0, 1]], [[100, 250]]),
                reading('r2', [followingRef], [[220, 300, 0, 1]], [[270, 300]]),
            ],
        });

        expect(shard.readings[0]?.timing.boundaries.at(-1)).toMatchObject({
            start_ms: 250,
            end_ms: 270,
        });
        },
    );

    it('derives internal and chapter-edge pauses from the correct bounds', () => {
        const shard = decodeTimestampShard({
            _meta: meta,
            readings: [
                reading(
                    'r1',
                    ['1:1'],
                    [[100, 400, 0, 2]],
                    [[120, 200], [230, 350]],
                ),
            ],
        });

        expect(shard.readings[0]?.timing.boundaries).toEqual([
            { boundary_id: 0, start_ms: 100, end_ms: 120 },
            { boundary_id: 1, start_ms: 200, end_ms: 230 },
            { boundary_id: 2, start_ms: 350, end_ms: 400 },
        ]);
    });

    it('uses word timings across verse parts inside one connected reading', () => {
        const shard = decodeTimestampShard({
            _meta: meta,
            readings: [
                reading(
                    'r1',
                    ['1:1', '1:2'],
                    [[100, 200, 0, 1], [220, 400, 1, 1]],
                    [[120, 180], [250, 350]],
                ),
            ],
        });

        expect(shard.readings[0]?.timing.boundaries[1]).toMatchObject({
            start_ms: 180,
            end_ms: 250,
        });
    });
});

describe('compact timestamp shard variants (v15)', () => {
    const catalogue = {
        iwaja_qayyima: {
            name: 'Iwaja qayyima', description: null, options: ['sakt', 'idraj'], default: 'sakt',
        },
    };
    const variant = {
        id: 'iwaja_qayyima', chosen: 'idraj', words: [0, 1], targets: [0, 1],
        anchor: 'boundary', boundary: 1, by: 'scored', score: 4.0,
        affected: { sakt: { c: [5, 6], s: [1], b: [1] } },
    };
    const v15 = { ...meta, schema_version: 15, variant_catalogue: catalogue, variant_policy: '2026-10-05' };

    it('copies a reading\'s variants with the chapter definitions', () => {
        const withVariants = {
            ...reading('r1', ['18:1'], [[100, 400, 0, 2]], [[100, 200], [200, 400]]),
            variants: [variant],
        };
        const plain = reading('r2', ['18:2'], [[500, 600, 0, 1]], [[500, 600]]);
        const shard = decodeTimestampShard({ _meta: v15, readings: [withVariants, plain] });
        const [first, second] = shard.readings as Array<{ variants?: unknown; variantCatalogue?: unknown }>;

        expect(first?.variants).toEqual([variant]);
        expect(first?.variantCatalogue).toEqual(catalogue);
        expect(second).not.toHaveProperty('variants');
        expect(second).not.toHaveProperty('variantCatalogue');
    });

    it('reads native 13 and 15 and rejects a native 14', () => {
        const one = [reading('r1', ['1:1'], [[100, 200, 0, 1]], [[100, 200]])];
        expect(() => decodeTimestampShard({ _meta: meta, readings: one })).not.toThrow();
        expect(() => decodeTimestampShard({ _meta: v15, readings: one })).not.toThrow();
        expect(() => decodeTimestampShard({ _meta: { ...meta, schema_version: 14 }, readings: one }))
            .toThrow('schema 14 is unsupported');
    });
});
