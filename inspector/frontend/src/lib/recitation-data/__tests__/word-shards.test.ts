import { describe, expect, it } from 'vitest';

import { decodeTimestampShard } from '../compact-shards';
import { WORD_SHARD, WORD_TEXTS } from '../test-word-fixture';
import { assembleOccasion, shardOccasions, type TsReciterAudio } from '../ts-source';
import type { TsWordShardResponse } from '../../types/ts-client';

const BY_SURAH: TsReciterAudio = { audio_category: 'by_surah' };

function assemble() {
    const shard = decodeTimestampShard(WORD_SHARD) as TsWordShardResponse;
    const occasion = shardOccasions(shard)[0]!;
    return assembleOccasion('r', occasion, {}, {}, BY_SURAH, 'chapter.mp3');
}

describe('word-profile assembly', () => {
    it('takes each word text from the shard row, not the Hafs script files', () => {
        // `qpc` and `dk` are passed empty on purpose: reaching for them would
        // render Qalun coordinates in the Hafs spelling.
        const data = assemble();
        expect(data.words.map((word) => word.text)).toEqual(WORD_TEXTS);
        expect(data.words.map((word) => word.display_text)).toEqual(WORD_TEXTS);
        expect(data.words.map((word) => word.location)).toEqual([
            '112:1:1', '112:1:2', '112:1:3', '112:1:4',
        ]);
    });

    it('emits no letters and no phoneme intervals', () => {
        const data = assemble();
        expect(data.intervals).toEqual([]);
        expect(data.words.every((word) => word.letters.length === 0)).toBe(true);
        expect(data.words.every((word) => word.phoneme_indices.length === 0)).toBe(true);
    });

    it('leaves `native` empty and fills `wordReadings` instead', () => {
        const data = assemble();
        expect(data.native).toEqual([]);
        expect(data.wordReadings.map((reading) => reading.id)).toEqual(['r1']);
    });

    it('anchors word times against the occasion start for by_surah audio', () => {
        // Occasion starts at 0 here, so the offset is 0 and the seconds are the
        // stored ms — the point is that the SAME offset reaches both views.
        const data = assemble();
        expect(data.words[1]!.start).toBeCloseTo(0.8, 6);
        expect(data.wordReadings[0]!.words[1]!.start).toBeCloseTo(0.8, 6);
        expect(data.time_start_ms).toBe(0);
        expect(data.time_end_ms).toBe(3000);
    });

    it('pairs each word with the gap that follows it', () => {
        const data = assemble();
        const gaps = data.wordReadings[0]!.words.map((word) => word.boundary);
        // Boundary `i + 1` follows word `i`; boundary 0 is the lead-in and is
        // never attached to a word.
        expect(gaps.map((gap) => gap?.id)).toEqual([1, 2, 3, 4]);
        expect(gaps[0]!.start).toBeCloseTo(0.7, 6);
        expect(gaps[0]!.end).toBeCloseTo(0.8, 6);
    });

    it('marks the verse end on the gap that closes the verse', () => {
        const data = assemble();
        const gaps = data.wordReadings[0]!.words.map((word) => word.boundary);
        expect(gaps.map((gap) => gap?.verseEnd)).toEqual([null, null, null, 1]);
        expect(gaps.map((gap) => gap?.state)).toEqual(['join', 'join', 'join', 'stop']);
    });

    it('lifts a mid-verse waqf mark off the word onto its gap', () => {
        // U+06DA on word 2, which does not end the verse: the row gets the clean
        // word and the gap carries the mark as its own bridge cell.
        const marked = { ...WORD_SHARD.readings[0]! };
        const words = marked.words.map((row) => [...row]) as typeof marked.words;
        words[1] = [words[1]![0], String(words[1]![1]) + 'ۚ', words[1]![2], words[1]![3]];
        const shard = decodeTimestampShard({
            ...WORD_SHARD, readings: [{ ...marked, words }],
        }) as TsWordShardResponse;
        const data = assembleOccasion(
            'r', shardOccasions(shard)[0]!, {}, {}, BY_SURAH, 'chapter.mp3',
        );
        const view = data.wordReadings[0]!.words;
        expect(view[1]!.text).toBe(WORD_TEXTS[1]);
        expect(view[1]!.boundary?.stopSign).toBe('ۚ');
        // The teleprompter's `display_text` keeps the mark: it splits it itself.
        expect(data.words[1]!.display_text).toBe(WORD_TEXTS[1] + 'ۚ');
    });

    it('keeps the waqf mark inside the word at a verse end', () => {
        // Word 4 ends on U+06D6 AND closes the verse; the verse marker owns the
        // gap, so the mark stays in the word cell and highlights with it.
        const data = assemble();
        const view = data.wordReadings[0]!.words;
        expect(view[3]!.text).toBe(WORD_TEXTS[3]);
        expect(view[3]!.boundary?.verseEnd).toBe(1);
        expect(view[3]!.boundary?.stopSign).toBeNull();
    });

    it('keeps only the words this occasion selected', () => {
        const shard = decodeTimestampShard({
            ...WORD_SHARD,
            readings: [{ ...WORD_SHARD.readings[0]!, parts: [['112:1', 800, 1500, 1, 2]] }],
        }) as TsWordShardResponse;
        const data = assembleOccasion(
            'r', shardOccasions(shard)[0]!, {}, {}, BY_SURAH, 'chapter.mp3',
        );
        expect(data.words.map((word) => word.location)).toEqual(['112:1:2', '112:1:3']);
        expect(data.wordReadings[0]!.words.map((word) => word.id)).toEqual([1, 2]);
        // …and the row's `displayIndex` follows `data.words`, not the reading.
        // The two only coincide for a chapter's first verse, which is the whole
        // reason a partial selection is the case that pins this down: a loop
        // target built from `id` here would address `data.words[1]` and
        // `data.words[2]`, the second of which does not exist.
        expect(data.wordReadings[0]!.words.map((word) => word.displayIndex)).toEqual([0, 1]);
    });

    it('numbers every rendered word by its position in the flat word list', () => {
        const data = assemble();
        const flat = data.wordReadings.flatMap((reading) => reading.words);
        expect(flat.map((word) => word.displayIndex)).toEqual(data.words.map((_, i) => i));
        for (const word of flat) {
            expect(data.words[word.displayIndex]!.location).toBe(word.location);
        }
    });
});
