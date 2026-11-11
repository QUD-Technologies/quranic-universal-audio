import { describe, expect, it } from 'vitest';

import { assembleOccasion, shardOccasions, type TsReciterAudio } from '../ts-source';
import { nativeReading, nativeShard } from '../test-native-fixture';

const audio: TsReciterAudio = { audio_category: 'by_surah' };

describe('native animation tokens', () => {
    it('uses token timing directly and preserves co-highlighted marks', () => {
        const reading = nativeReading('r1', [{ ref: '2:1', start: 0, end: 300 }]);
        reading.animationTokens = [
            { id: 0, word_id: 0, source_unit_ids: [10], character_ids: [0], paint_character_ids: [0], text: 'ب',
                sound_ids: [0], policy: 'timed', target_token_id: null, start_ms: 0, end_ms: 300 },
            { id: 1, word_id: 0, source_unit_ids: [11], character_ids: [1], paint_character_ids: [1], text: 'ا',
                sound_ids: [], policy: 'cohighlight_previous', target_token_id: 0, start_ms: 0, end_ms: 300 },
        ];
        const occasion = shardOccasions(nativeShard([reading]))[0]!;
        const data = assembleOccasion('r', occasion, {}, audio, '');
        expect(data.words[0]!.letters).toEqual([
            { char: 'ب', start: 0, end: 0.3, tokenId: 0,
                sourceUnitIds: [10], characterIds: [0], paintCharacterIds: [0],
                policy: 'timed', silent: false },
            { char: 'ا', start: 0, end: 0.3, tokenId: 1,
                sourceUnitIds: [11], characterIds: [1], paintCharacterIds: [1],
                policy: 'cohighlight_previous', silent: true },
        ]);
    });
});
