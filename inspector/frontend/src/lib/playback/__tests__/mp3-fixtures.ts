/** Synthetic MP3 heads for the play-url / mp3-header tests. */

/** MPEG-1 Layer III, 128 kbps, 44.1 kHz, joint stereo — side info 32 bytes. */
export const MPEG1_STEREO = [0xff, 0xfb, 0x90, 0x64];
/** MPEG-1 Layer III mono — side info 17 bytes. */
export const MPEG1_MONO = [0xff, 0xfb, 0x90, 0xc4];
/** MPEG-2 Layer III, 64 kbps, 22.05 kHz, stereo — side info 17 bytes. */
export const MPEG2_STEREO = [0xff, 0xf3, 0x80, 0x64];

export function ascii(s: string): number[] {
    return [...s].map((c) => c.charCodeAt(0));
}

export function id3v2(payloadBytes: number, footer = false): number[] {
    const size = [
        (payloadBytes >> 21) & 0x7f, (payloadBytes >> 14) & 0x7f,
        (payloadBytes >> 7) & 0x7f, payloadBytes & 0x7f,
    ];
    return [...ascii('ID3'), 0x04, 0x00, footer ? 0x10 : 0x00, ...size,
        ...new Array<number>(payloadBytes).fill(0x00),
        ...(footer ? [...ascii('3DI'), 0x04, 0x00, 0x10, ...size] : [])];
}

export function frame(header: number[], sideInfo: number, tag: string | null): number[] {
    return [...header, ...new Array<number>(sideInfo).fill(0x00),
        ...(tag ? ascii(tag) : [0, 0, 0, 0]), ...new Array<number>(64).fill(0xaa)];
}

export function mp3Head(opts: {
    tag: string | null; id3?: number; header?: number[]; sideInfo?: number;
}): Uint8Array {
    const { tag, id3 = 0, header = MPEG1_STEREO, sideInfo = 32 } = opts;
    return new Uint8Array([...(id3 ? id3v2(id3) : []), ...frame(header, sideInfo, tag)]);
}

/** 128 kbps frame length at 44.1 kHz: 144 * 128000 / 44100 = 417.96 bytes. */
const MPEG1_128K_NOMINAL = (144 * 128000) / 44100;
const PADDING_BIT = 0x02;

/** `n` consecutive MPEG-1 128 kbps frames with real lengths. `padded` spreads
 *  the padding bit the way an encoder must to hold the nominal byte rate;
 *  unpadded frames are all 417 bytes and run 0.23 % slow. `header` overrides
 *  the rate (a 48 kHz header has integral 384-byte frames and never pads). */
export function frameRun(n: number, opts: {
    padded: boolean; tag?: string | null; header?: number[]; nominal?: number; switchAt?: number;
}): Uint8Array {
    const { padded, tag = null, header = MPEG1_STEREO, nominal = MPEG1_128K_NOMINAL, switchAt } = opts;
    const out: number[] = [];
    let owed = 0;
    for (let k = 0; k < n; k += 1) {
        owed += nominal - Math.floor(nominal);
        const pad = padded && owed >= 1;
        if (pad) owed -= 1;
        const h = [...header];
        if (k === switchAt) h[2] = (h[2]! & 0x0f) | 0xa0;
        if (pad) h[2] = h[2]! | PADDING_BIT;
        const length = Math.floor(k === switchAt ? (144 * 160000) / 44100 : nominal) + (pad ? 1 : 0);
        const body = new Array<number>(length - 4).fill(0x00);
        if (k === 0 && tag) body.splice(32, 4, ...ascii(tag));
        out.push(...h, ...body);
    }
    return new Uint8Array(out);
}
