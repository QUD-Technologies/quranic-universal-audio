/**
 * mp3-header.ts — first-frame VBR-tag sniff that gates direct-CDN playback.
 */

import { describe, expect, it } from 'vitest';

import { firstFrameVbrTag, isNativelySeekable } from '../mp3-header';
import { frame, frameRun, id3v2, mp3Head, MPEG1_MONO, MPEG1_STEREO, MPEG2_STEREO } from './mp3-fixtures';

describe('firstFrameVbrTag', () => {
    it('reads an Info tag on a bare MPEG-1 stereo frame', () => {
        expect(firstFrameVbrTag(mp3Head({ tag: 'Info' }))).toBe('Info');
    });

    it('reads a Xing tag behind an ID3v2 tag', () => {
        expect(firstFrameVbrTag(mp3Head({ tag: 'Xing', id3: 9759 }))).toBe('Xing');
    });

    it('skips an ID3v2 footer', () => {
        const bytes = new Uint8Array([...id3v2(100, true), ...frame(MPEG1_STEREO, 32, 'Xing')]);
        expect(firstFrameVbrTag(bytes)).toBe('Xing');
    });

    it('uses the mono / MPEG-2 side-info lengths', () => {
        expect(firstFrameVbrTag(mp3Head({ tag: 'Info', header: MPEG1_MONO, sideInfo: 17 }))).toBe('Info');
        expect(firstFrameVbrTag(mp3Head({ tag: 'Xing', header: MPEG2_STEREO, sideInfo: 17 }))).toBe('Xing');
    });

    it('returns null for a frame without a VBR tag', () => {
        expect(firstFrameVbrTag(mp3Head({ tag: null }))).toBeNull();
    });

    it('returns undefined when no frame header is inside the window', () => {
        expect(firstFrameVbrTag(new Uint8Array(id3v2(50)))).toBeUndefined();
        expect(firstFrameVbrTag(new Uint8Array([1, 2, 3]))).toBeUndefined();
    });

    it('ignores a false sync inside the ID3 payload', () => {
        const junk = id3v2(8);
        junk[10] = 0xff; junk[11] = 0xfb; junk[12] = 0x90; junk[13] = 0x64;
        const bytes = new Uint8Array([...junk, ...frame(MPEG1_STEREO, 32, 'Info')]);
        expect(firstFrameVbrTag(bytes)).toBe('Info');
    });
});

describe('isNativelySeekable', () => {
    it('is true for an Info-tagged file', () => {
        expect(isNativelySeekable(mp3Head({ tag: 'Info' }))).toBe(true);
    });

    it('is true for an untagged file whose frames hold the nominal byte rate', () => {
        expect(isNativelySeekable(frameRun(150, { padded: true }))).toBe(true);
        const hz48k = [0xff, 0xfb, 0x94, 0x64];
        expect(isNativelySeekable(frameRun(150, { padded: false, header: hz48k, nominal: 384 }))).toBe(true);
    });

    it('is false for an untagged file that never pads (nominal-rate seek drifts)', () => {
        expect(isNativelySeekable(frameRun(150, { padded: false }))).toBe(false);
    });

    it('is false for an untagged file that changes bitrate (untagged VBR)', () => {
        expect(isNativelySeekable(frameRun(150, { padded: true, switchAt: 40 }))).toBe(false);
    });

    it('is false for an untagged head too short to vouch for', () => {
        expect(isNativelySeekable(mp3Head({ tag: null }))).toBe(false);
        expect(isNativelySeekable(frameRun(4, { padded: true }))).toBe(false);
    });

    it('does not walk frames behind an Info tag', () => {
        expect(isNativelySeekable(frameRun(150, { padded: false, tag: 'Info' }))).toBe(true);
    });

    it('is false for a Xing-tagged file (TOC seek) and for an unreadable head', () => {
        expect(isNativelySeekable(mp3Head({ tag: 'Xing' }))).toBe(false);
        expect(isNativelySeekable(new Uint8Array(id3v2(50)))).toBe(false);
    });
});
