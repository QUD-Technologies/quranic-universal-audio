import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchJson } from '../../../../lib/api';
import { nativeReading, nativeShard } from '../../../../lib/recitation-data/test-native-fixture';
import {
    assembleOccasion,
    assembleWaslGroup,
    chapterVerseRefs,
    resolveVbrChaptersForReciter,
    shardOccasions,
    type TsReciterAudio,
    vbrChaptersFromManifest,
} from '../ts_client';

const BY_SURAH: TsReciterAudio = { audio_category: 'by_surah' };
const BY_AYAH: TsReciterAudio = { audio_category: 'by_ayah' };

vi.mock('../../../../lib/api', () => ({
    fetchArrayBuffer: vi.fn(),
    fetchJson: vi.fn(),
}));

beforeEach(() => vi.mocked(fetchJson).mockReset());

function occasion(ref: string, reading = nativeReading('r1', [
    { ref, start: 5_000, end: 6_000, text: 'native' },
])) {
    const shard = nativeShard([reading]);
    const result = shardOccasions(shard).find((one) => one.ref === ref);
    if (!result) throw new Error(`Missing occasion ${ref}`);
    return { result };
}

describe('native v13 timestamp assembly', () => {
    it('uses native timing IDs and zero-anchors chapter audio', () => {
        const { result } = occasion('1:1');
        const data = assembleOccasion(
            'reciter', result,
            { '1:1:1': { text: 'dk' } },
            BY_SURAH, 'chapter.mp3',
        );
        expect(data.audio_url).toBe('chapter.mp3');
        expect(data.audio_category).toBe('by_surah_audio');
        expect(data.time_start_ms).toBe(5_000);
        expect(data.words[0]).toMatchObject({
            location: '1:1:1', text: 'dk', start: 0, end: 1,
        });
        expect(data.intervals).toEqual([{ phone: 'p0', start: 0, end: 1 }]);
        expect(data.words[0]!.phoneme_indices).toEqual([0]);
        expect(data.native.map((reading) => reading.id)).toEqual(['r1']);
    });

    it('keeps ayah audio absolute within its own file', () => {
        const reading = nativeReading('r1', [{ ref: '1:1', start: 0, end: 800 }]);
        const { result } = occasion('1:1', reading);
        const data = assembleOccasion('r', result, {}, BY_AYAH, 'ayah.mp3');
        expect(data.time_start_ms).toBe(0);
        expect(data.time_end_ms).toBe(800);
        expect(data.words[0]!.end).toBe(0.8);
    });

    it('keeps repeated occasions instead of deduplicating them', () => {
        const shard = nativeShard([
            nativeReading('take-a', [{ ref: '1:1', start: 0, end: 500 }]),
            nativeReading('middle', [{ ref: '1:2', start: 500, end: 1_000 }]),
            nativeReading('take-b', [{ ref: '1:1', start: 1_000, end: 1_500 }]),
        ]);
        expect(shardOccasions(shard).map((one) => one.ref)).toEqual(['1:1', '1:2', '1:1']);
        expect(chapterVerseRefs(shard)).toEqual(['1:1', '1:2']);
    });

    it('assembles a connected reading once and preserves both verse refs', () => {
        const reading = nativeReading('connected', [
            { ref: '1:3', start: 5_000, end: 6_000 },
            { ref: '1:4', start: 6_000, end: 7_000 },
        ]);
        const occasions = shardOccasions(nativeShard([reading]));
        const data = assembleWaslGroup(
            'r', occasions, '1:3', {}, BY_SURAH, 'chapter.mp3',
        );
        expect(data.words.map((word) => word.location)).toEqual(['1:3:1', '1:4:1']);
        expect(data.native).toEqual([reading]);
        expect(data.time_start_ms).toBe(5_000);
        expect(data.time_end_ms).toBe(7_000);
    });
});

describe('VBR metadata', () => {
    it('reads and sorts manifest chapters', () => {
        const manifest = { reciters: { r: { vbr_chapters: [7, 2] } } } as never;
        expect(vbrChaptersFromManifest(manifest, 'r')).toEqual([2, 7]);
    });

    it('uses the metadata endpoint only when the manifest omits the field', async () => {
        vi.mocked(fetchJson).mockResolvedValue({ vbr_chapters: [4, 1] });
        const manifest = { reciters: { r: {} } } as never;
        await expect(resolveVbrChaptersForReciter('r', manifest)).resolves.toEqual([1, 4]);
        expect(fetchJson).toHaveBeenCalledWith('/api/ts/vbr/r');
    });
});
