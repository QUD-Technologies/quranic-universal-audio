import type { SpecialKind, TsShardReading, TsShardResponse } from '../types/ts-client';

export interface FixturePart {
    ref: string;
    start: number;
    end: number;
    text?: string;
}

export function nativeReading(
    id: string,
    parts: FixturePart[],
    options: { special?: SpecialKind } = {},
): TsShardReading {
    const words = parts.map((part, index) => ({
        id: index,
        ref: `${part.ref}:${parts.slice(0, index).filter((prior) => prior.ref === part.ref).length + 1}`,
        text: part.text ?? `w${index}`,
        before_boundary_id: index, after_boundary_id: index + 1, sound_ids: [index],
    }));
    const sounds = words.map((word) => ({
        id: word.id, order: word.id, token: `p${word.id}`, word_id: word.id,
        rule_occurrence_ids: [],
    }));
    const columns = words.map((word) => ({
        id: word.id + 100, role: 'letter' as const, text: word.text,
        source_character_ids: [word.id], source_unit_ids: [word.id], slot_ids: [word.id],
        tier: 'main' as const, attached_to_column_id: null, status: 'present' as const,
        variant_id: null, variant_choice: null, anchor_unit_id: null, side: null,
        owned_sound_ids: [word.id], presented_sound_ids: [], rule_occurrence_ids: [],
        silence: null,
    }));
    return {
        id,
        parts: parts.map((part, index) => ({
            ref: part.ref, t: [part.start, part.end], word_ids: [index],
        })),
        wire: {
            analysis: {
                schema_version: 2,
                result: { words, sounds, boundaries: [], rule_occurrences: [] },
            },
            cells: {
                schema_version: 2,
                cell_view: {
                    words: words.map((word) => ({
                        word_id: word.id,
                        columns: [columns[word.id]!],
                        sounds: [{
                            sound_id: word.id,
                            column_ids: [columns[word.id]!.id],
                            rule_occurrence_ids: [],
                        }],
                        groups: [{
                            key: columns[word.id]!.id,
                            kind: 'base',
                            column_ids: [columns[word.id]!.id],
                            sound_ids: [word.id],
                        }],
                        runs: [],
                        bridges: [],
                    })),
                    boundaries: words.map((word) => ({
                        boundary_id: word.after_boundary_id,
                        columns: [], sounds: [], bridges: [],
                        state: word.id + 1 < words.length ? 'join' : 'stop',
                        // An opener ends no verse.
                        verse_end: options.special ? null : Number(word.ref.split(':')[1]),
                        exclusive_group: null,
                    })),
                },
            },
        },
        animationTokens: parts.map((part, index) => ({
            id: index, word_id: index, source_unit_ids: [index], character_ids: [index],
            paint_character_ids: [index],
            text: words[index]!.text, sound_ids: [index], policy: 'timed' as const,
            target_token_id: null, start_ms: part.start, end_ms: part.end,
        })),
        timing: {
            words: parts.map((part, index) => ({
                word_id: index, start_ms: part.start, end_ms: part.end,
            })),
            sounds: parts.map((part, index) => ({
                sound_id: index, start_ms: part.start, end_ms: part.end,
            })),
            boundaries: [],
            columns: [],
        },
        ...(options.special ? { special: options.special } : {}),
    };
}

export function nativeShard(readings: TsShardReading[], chapter = 1): TsShardResponse {
    return {
        _meta: {
            schema_version: 15,
            native_schema_version: 2,
            renderer_codec_version: 1,
            native_profile: {
                riwayah: 'hafs', script: 'uthmani', variant: {}, extra_phonemes: [],
            },
            chapter,
            audio_category: 'by_surah',
            phonemizer_version: '2.15',
        },
        readings,
    };
}

/** Al-Baqarah's chapter number — a by-surah chapter whose ayah 1 the Basmala precedes. */
export const BASMALA_FIXTURE_CHAPTER = 2;

/**
 * A chapter that opens with a timed Basmala special reading (part `0:2`, words
 * `0:2:1`..`0:2:4`, 1.0–3.0 s) and then ayah 1 and ayah 2 as ordinary readings.
 * Ayah 2 exists so an opener keyed by its `ayah` (2 of `0:2`) would collide.
 */
export function basmalaShard(chapter = BASMALA_FIXTURE_CHAPTER): TsShardResponse {
    const basmala = nativeReading('r1', [
        { ref: '0:2', start: 1000, end: 1500, text: 'بِسْمِ' },
        { ref: '0:2', start: 1500, end: 2000, text: 'ٱللَّهِ' },
        { ref: '0:2', start: 2000, end: 2500, text: 'ٱلرَّحْمَـٰنِ' },
        { ref: '0:2', start: 2500, end: 3000, text: 'ٱلرَّحِيمِ' },
    ], { special: 'Basmala' });
    const ayah1 = nativeReading('r2', [{ ref: `${chapter}:1`, start: 3400, end: 4400, text: 'الٓمٓ' }]);
    const ayah2 = nativeReading('r3', [
        { ref: `${chapter}:2`, start: 4800, end: 5600 },
        { ref: `${chapter}:2`, start: 5600, end: 6400 },
    ]);
    return nativeShard([basmala, ayah1, ayah2], chapter);
}
