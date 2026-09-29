/**
 * Mushaf-view webfonts, loaded on first use through the FontFace API.
 *
 * Hafs' Digital Khatt v2 is already inlined in `styles/base.css` ('DigitalKhatt')
 * and serves the 1421H / 1441H layouts. The 1405H print pairs with Digital
 * Khatt v1; the surah-name ligatures and the surah-header frame have their own
 * fonts. Those three are served from the bucket via `/api/static/mushaf-font/`.
 */
import type { MushafYear } from '../stores/mushaf';

const FONT_URL = '/api/static/mushaf-font/';

export const SURAH_NAME_FAMILY = 'MushafSurahName';
export const SURAH_FRAME_FAMILY = 'MushafSurahFrame';
const DK_V1_FAMILY = 'DigitalKhattV1';
const DK_V2_FAMILY = 'DigitalKhatt';

const FILES: Record<string, string> = {
    [SURAH_NAME_FAMILY]: 'surah-name-v2.woff2',
    [SURAH_FRAME_FAMILY]: 'juz-font.woff2',
    [DK_V1_FAMILY]: 'digital-khatt-madani-v1.woff2',
};

const loading = new Map<string, Promise<boolean>>();

/** Load + register `family` once; resolves false (text falls back) on failure. */
export function ensureMushafFont(family: string): Promise<boolean> {
    let p = loading.get(family);
    if (!p) {
        const file = FILES[family];
        p = file
            ? new FontFace(family, `url(${FONT_URL}${file})`)
                  .load()
                  .then((face) => {
                      document.fonts.add(face);
                      return true;
                  })
                  .catch((e: unknown) => {
                      console.error(`Mushaf: font ${family} failed to load`, e);
                      loading.delete(family);
                      return false;
                  })
            : document.fonts.load(`32px "${family}"`).then(() => true, () => false);
        loading.set(family, p);
    }
    return p;
}

/** Text font family for a print year. */
export function textFamily(year: MushafYear): string {
    return year === '1405' ? DK_V1_FAMILY : DK_V2_FAMILY;
}

/** CSS font stack for a print year. */
export function textFontStack(year: MushafYear): string {
    return `'${textFamily(year)}', 'DigitalKhatt', serif`;
}
