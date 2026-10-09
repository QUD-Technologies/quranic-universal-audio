/**
 * Decode stored shard storage into the Inspector's timing view.
 *
 * Two profiles share the path, discriminated by `_meta.profile`:
 * native (schema 15, and 13 until every reciter is re-timed: full phonemizer
 * cells — Hafs only) and word (schema 14, proxy-timed word intervals for
 * another riwayah).
 */

import {
    decodeCompact,
    type CompactCellPayload,
} from '@quranic-phonemizer/cells';

import type {
    TsReadingVariant,
    TsShardMeta,
    TsVariantDefinition,
    TsWordShardMeta,
} from '../types/generated/schemas';
import type {
    TsBoundaryTiming,
    TsShardPart,
    TsShardReading,
    TsShardResponse,
    TsWordShardReading,
} from '../types/ts-client';

/** Schema versions a native document may declare; 13 is read until the fleet re-time. */
const NATIVE_SCHEMA_VERSIONS: readonly number[] = [13, 15];

/** Index order must match `TS_WORD_BOUNDARY_STATES` in the Pydantic schema. */
const WORD_BOUNDARY_STATES = ['start', 'join', 'sakt', 'stop'] as const;

type StoredPart = [string, number, number, number, number];
type StoredWordRow = [string, string, number, number];
type StoredWordBoundary = [number, number | null];

interface StoredWordReading {
    id: string;
    parts: StoredPart[];
    words: StoredWordRow[];
    boundaries: StoredWordBoundary[];
}

interface StoredWordShard {
    _meta: TsWordShardMeta;
    readings: StoredWordReading[];
}
type StoredAnimation = [number | null, number | null];
type StoredAnimationMeta = [number, number[], number[], number[], string, number[], 0 | 1 | 2, number | null];
type StoredColumn = [string | number, number | null, number | null];

interface StoredReading {
    id: string;
    parts: StoredPart[];
    render: CompactCellPayload & { a: StoredAnimationMeta[] };
    timing: {
        w: Array<[number, number]>;
        s: Array<[number, number]>;
        a: StoredAnimation[];
        c: StoredColumn[];
    };
    variants?: TsReadingVariant[] | null;
}

interface StoredShard {
    _meta: TsShardMeta;
    readings: StoredReading[];
}

function partsOf(rows: StoredPart[]): TsShardPart[] {
    return rows.map(([ref, start, end, first, count]) => ({
        ref,
        t: [start, end],
        word_ids: Array.from({ length: count }, (_, index) => first + index),
    }));
}

function boundariesOf(
    parts: TsShardPart[],
    words: Array<[number, number]>,
): TsBoundaryTiming[] {
    if (!words.length) return [];
    const first = parts[0]?.t[0] ?? words[0]![0];
    const last = parts.at(-1)?.t[1] ?? words.at(-1)![1];
    const rows: TsBoundaryTiming[] = [{
        boundary_id: 0,
        start_ms: first,
        end_ms: Math.max(first, words[0]![0]),
    }];
    for (let id = 1; id < words.length; id += 1) {
        const start = words[id - 1]![1];
        rows.push({ boundary_id: id, start_ms: start, end_ms: Math.max(start, words[id]![0]) });
    }
    const start = words.at(-1)![1];
    rows.push({ boundary_id: words.length, start_ms: start, end_ms: Math.max(start, last) });
    return rows;
}

function readingOf(
    raw: StoredReading,
    catalogue: Record<string, TsVariantDefinition> | null | undefined,
): TsShardReading {
    const parts = partsOf(raw.parts);
    if (raw.timing.w.length !== raw.render.w.length
        || raw.timing.s.length !== raw.render.p.length
        || raw.timing.a.length !== raw.render.a.length) {
        throw new Error(`${raw.id}: compact timing count mismatch`);
    }
    return {
        id: raw.id,
        parts,
        wire: decodeCompact(raw.render),
        animationTokens: raw.render.a.map((meta, id) => {
            const [word_id, source_unit_ids, character_ids, paint_character_ids, text,
                sound_ids, policyCode,
                target_token_id] = meta;
            const [start_ms, end_ms] = raw.timing.a[id]!;
            const policies = ['timed', 'cohighlight_previous', 'cohighlight_next'] as const;
            const policy = policies[policyCode];
            if (!policy) throw new Error(`${raw.id}: invalid animation policy ${policyCode}`);
            return { id, word_id, source_unit_ids, character_ids, paint_character_ids,
                text, sound_ids,
                policy, target_token_id, start_ms, end_ms };
        }),
        timing: {
            words: raw.timing.w.map(([start_ms, end_ms], word_id) => ({
                word_id, start_ms, end_ms,
            })),
            sounds: raw.timing.s.map(([start_ms, end_ms], sound_id) => ({
                sound_id, start_ms, end_ms,
            })),
            boundaries: boundariesOf(parts, raw.timing.w),
            columns: raw.timing.c.map(([column_id, start_ms, end_ms]) => ({
                column_id, start_ms, end_ms,
            })),
        },
        ...(raw.variants?.length && catalogue
            ? { variants: raw.variants, variantCatalogue: catalogue }
            : {}),
    };
}

/** Structural subset both profiles share — all the stitch actually reads. */
interface StitchableReading {
    parts: TsShardPart[];
    timing: { words: { start_ms: number }[]; boundaries: TsBoundaryTiming[] };
}

function stitchInterReadingPauses(readings: StitchableReading[]): void {
    const ordered = [...readings].sort((a, b) =>
        (a.parts[0]?.t[0] ?? 0) - (b.parts[0]?.t[0] ?? 0),
    );
    ordered.slice(0, -1).forEach((reading, index) => {
        const boundary = reading.timing.boundaries.at(-1);
        const next = ordered[index + 1];
        const nextStart = next?.timing.words[0]?.start_ms ?? next?.parts[0]?.t[0];
        if (boundary && nextStart !== undefined) {
            boundary.end_ms = Math.max(boundary.end_ms, nextStart);
        }
    });
}

function storedShard(raw: unknown): StoredShard {
    if (!raw || typeof raw !== 'object') throw new Error('Timestamp shard is not an object');
    const shard = raw as StoredShard;
    if (!NATIVE_SCHEMA_VERSIONS.includes(shard._meta?.schema_version)) {
        throw new Error(`Timestamp shard schema ${String(shard._meta?.schema_version)} is unsupported`);
    }
    if (shard._meta.native_schema_version !== 2) throw new Error('Native schema is not v2');
    if (shard._meta.renderer_codec_version !== 1) {
        throw new Error(`Renderer codec ${String(shard._meta.renderer_codec_version)} is unsupported`);
    }
    if (!Array.isArray(shard.readings)) throw new Error('Timestamp shard has no readings');
    return shard;
}

function wordReadingOf(raw: StoredWordReading): TsWordShardReading {
    const parts = partsOf(raw.parts);
    if (raw.boundaries.length !== raw.words.length) {
        throw new Error(`${raw.id}: word and boundary counts differ`);
    }
    const wordSpans = raw.words.map(([, , start, end]) => [start, end] as [number, number]);
    return {
        id: raw.id,
        parts,
        words: raw.words.map(([ref, text, start_ms, end_ms]) => ({ ref, text, start_ms, end_ms })),
        states: raw.boundaries.map(([code]) => {
            const state = WORD_BOUNDARY_STATES[code];
            if (!state) throw new Error(`${raw.id}: invalid boundary state ${code}`);
            return state;
        }),
        verseEnds: raw.boundaries.map(([, verseEnd]) => verseEnd),
        timing: {
            words: wordSpans.map(([start_ms, end_ms], word_id) => ({ word_id, start_ms, end_ms })),
            // Same derivation as the native profile — pause geometry is
            // phoneme-free, so the two profiles agree on boundary timing.
            boundaries: boundariesOf(parts, wordSpans),
        },
    };
}

function storedWordShard(raw: unknown): StoredWordShard {
    const shard = raw as StoredWordShard;
    if (shard._meta.schema_version !== 14) {
        throw new Error(`Word shard schema ${String(shard._meta.schema_version)} is unsupported`);
    }
    if (!Array.isArray(shard.readings)) throw new Error('Word shard has no readings');
    return shard;
}

/**
 * Decode a stored shard of either profile.
 *
 * `_meta.profile` is the discriminator. No native object carries it, so
 * absent means native. A v15 reading's `variants` travel with the chapter's
 * definitions of them.
 */
export function decodeTimestampShard(raw: unknown): TsShardResponse {
    if (!raw || typeof raw !== 'object') throw new Error('Timestamp shard is not an object');
    const profile = (raw as { _meta?: { profile?: string } })._meta?.profile ?? 'native';

    if (profile === 'word') {
        const shard = storedWordShard(raw);
        const readings = shard.readings.map(wordReadingOf);
        stitchInterReadingPauses(readings);
        return { _meta: shard._meta, readings };
    }

    const shard = storedShard(raw);
    const catalogue = shard._meta.variant_catalogue;
    const readings = shard.readings.map((reading) => readingOf(reading, catalogue));
    stitchInterReadingPauses(readings);
    return { _meta: shard._meta, readings };
}
