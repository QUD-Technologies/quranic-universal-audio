/** Native shard indexing and timing-view assembly. */

import type {
    Letter,
    SpecialKind,
    PhonemeInterval,
    TsShardReading,
    TsShardResponse,
    TsVerseData,
    TsWord,
} from '../types/ts-client';
import { type AnyShardReading, type ChapterOccasion, chapterOccasions } from './occasions';
import { isVerseRef } from './specials';

const occasionsByShard = new WeakMap<TsShardResponse, ChapterOccasion[]>();

export function shardOccasions(shard: TsShardResponse): ChapterOccasion[] {
    let occasions = occasionsByShard.get(shard);
    if (!occasions) {
        occasions = chapterOccasions(shard.readings, shard._meta.chapter);
        occasionsByShard.set(shard, occasions);
    }
    return occasions;
}

interface AssembleOptions {
    reciter: string;
    members: ChapterOccasion[];
    verseRef: string;
    /** The shard's chapter — never parsed from `verseRef` (an opener's is `0:x`). */
    chapter: number;
    special: SpecialKind | null;
    qpc: Record<string, { text?: string }>;
    dk: Record<string, { text?: string }>;
    audioCategory: 'by_surah' | 'by_ayah';
    audioUrl: string;
}

/**
 * Narrow an occasion reading to the native profile.
 *
 * Occasion splitting is profile-independent, so a `ChapterOccasion` can carry
 * either kind. Everything below this line reads cells, sounds and animation
 * tokens, which a proxy-timed word reading does not have — callers must branch
 * on `isWordShard` before assembling. Throwing here makes a missed branch a
 * loud failure rather than a half-rendered verse.
 */
function asNative(reading: AnyShardReading): TsShardReading {
    if (!('wire' in reading)) {
        throw new Error(`reading ${reading.id}: word-profile shard has no native cells`);
    }
    return reading;
}

const uniqueReadings = (members: ChapterOccasion[]): TsShardReading[] => [
    ...new Set(members.flatMap((member) => member.readings.map((one) => asNative(one.reading)))),
];

function selectedWords(members: ChapterOccasion[]): Map<TsShardReading, Set<number>> {
    const selected = new Map<TsShardReading, Set<number>>();
    for (const member of members) {
        for (const entry of member.readings) {
            const reading = asNative(entry.reading);
            const ids = selected.get(reading) ?? new Set<number>();
            entry.parts.flatMap((part) => part.word_ids).forEach((id) => ids.add(id));
            selected.set(reading, ids);
        }
    }
    return selected;
}

function lettersOf(reading: TsShardReading, wordId: number, offset: number): Letter[] {
    return reading.animationTokens
        .filter((token) => token.word_id === wordId)
        .map((token) => {
            const start = token.start_ms == null ? null : token.start_ms / 1000 - offset;
            const end = token.end_ms == null ? null : token.end_ms / 1000 - offset;
            return {
                char: token.text,
                start,
                end,
                tokenId: token.id,
                sourceUnitIds: token.source_unit_ids,
                characterIds: token.character_ids,
                paintCharacterIds: token.paint_character_ids,
                policy: token.policy,
                silent: token.sound_ids.length === 0,
            };
        });
}

function buildTimedRows(
    readings: TsShardReading[],
    selected: Map<TsShardReading, Set<number>>,
    qpc: AssembleOptions['qpc'],
    dk: AssembleOptions['dk'],
    offset: number,
): { intervals: PhonemeInterval[]; words: TsWord[] } {
    const intervals: PhonemeInterval[] = [];
    const words: TsWord[] = [];
    for (const reading of readings) {
        const wordTiming = new Map(reading.timing.words.map((row) => [row.word_id, row]));
        const soundTiming = new Map(reading.timing.sounds.map((row) => [row.sound_id, row]));
        for (const word of reading.wire.analysis.result.words) {
            if (!selected.get(reading)?.has(word.id)) continue;
            const timed = wordTiming.get(word.id);
            if (!timed) throw new Error(`${reading.id}: missing word timing ${word.id}`);
            const phonemeIndices: number[] = [];
            for (const soundId of word.sound_ids) {
                const sound = reading.wire.analysis.result.sounds.find((one) => one.id === soundId);
                const span = soundTiming.get(soundId);
                if (!sound || !span) throw new Error(`${reading.id}: missing sound ${soundId}`);
                phonemeIndices.push(intervals.length);
                intervals.push({
                    phone: sound.token,
                    start: span.start_ms / 1000 - offset,
                    end: span.end_ms / 1000 - offset,
                });
            }
            words.push({
                location: word.ref,
                text: qpc[word.ref]?.text ?? word.text,
                display_text: dk[word.ref]?.text ?? qpc[word.ref]?.text ?? word.text,
                start: timed.start_ms / 1000 - offset,
                end: timed.end_ms / 1000 - offset,
                phoneme_indices: phonemeIndices,
                letters: lettersOf(reading, word.id, offset),
            });
        }
    }
    return { intervals, words };
}

export function assembleNative(options: AssembleOptions): TsVerseData {
    const readings = uniqueReadings(options.members);
    const parts = options.members.flatMap((member) => member.parts);
    const startMs = Math.min(...parts.map((part) => part.t[0]));
    const endMs = Math.max(...parts.map((part) => part.t[1]));
    const offset = options.audioCategory === 'by_surah' ? startMs / 1000 : 0;
    const timed = buildTimedRows(
        readings,
        selectedWords(options.members),
        options.qpc,
        options.dk,
        offset,
    );
    return {
        reciter: options.reciter,
        chapter: options.chapter,
        verse_ref: options.verseRef,
        audio_url: options.audioUrl,
        audio_category: options.audioCategory === 'by_surah' ? 'by_surah_audio' : 'by_ayah_audio',
        time_start_ms: options.audioCategory === 'by_surah' ? startMs : 0,
        time_end_ms: endMs,
        ...timed,
        native: readings,
        wordReadings: [],
        special: options.special,
    };
}

/** The shard's distinct VERSE refs — recited openers are not verses. */
export function chapterVerseRefs(shard: TsShardResponse): string[] {
    const verses = shardOccasions(shard).filter((one) => !one.special && isVerseRef(one.ref));
    return [...new Set(verses.map((occasion) => occasion.ref))];
}
