import { describe, expect, it } from 'vitest';

import { buildChapterRecitation } from '../../recitation-animation/chapter-words';
import { buildSortedIntervals, findActiveAt } from '../../recitation-animation/recitation-active';
import { assembleOccasion, shardOccasions, type TsReciterAudio } from '../ts-source';
import { nativeReading, nativeShard } from '../test-native-fixture';

const AUDIO: TsReciterAudio = { audio_category: 'by_surah' };

describe('native occasion coverage', () => {
    it('keeps every retake active on the filmstrip timeline', () => {
        const shard = nativeShard([
            nativeReading('take-a', [{ ref: '101:1', start: 100, end: 600 }]),
            nativeReading('middle', [{ ref: '101:2', start: 800, end: 1_300 }]),
            nativeReading('take-b', [{ ref: '101:1', start: 1_500, end: 2_000 }]),
        ]);
        const occasions = shardOccasions(shard).map((occasion) => ({
            verseRef: occasion.ref,
            data: assembleOccasion('r', occasion, {}, AUDIO, 'chapter.mp3'),
        }));
        const chapter = buildChapterRecitation('r', 101, occasions);
        const sorted = buildSortedIntervals(chapter.units);

        for (const time of [0.2, 0.9, 1.6]) {
            expect(findActiveAt(chapter.units, sorted, time, -1)).not.toBeNull();
        }
        expect(findActiveAt(chapter.units, sorted, 0.7, -1)).toBeNull();
        expect(chapter.units.some((unit) => unit.intervals.length > 1)).toBe(true);
    });
});
