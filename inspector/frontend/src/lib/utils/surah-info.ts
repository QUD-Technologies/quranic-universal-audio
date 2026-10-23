/**
 * Shared surah info: fetched once at import time, available across all tabs.
 */

import { fetchJson } from '../api';
import type { SurahInfoResponse } from '../types/ts-client';

let _surahInfo: SurahInfoResponse = {};

export const surahInfoReady = fetchJson<SurahInfoResponse>('/api/surah-info').then((data) => {
    _surahInfo = data;
});

/** Return the cached surah info map. Await `surahInfoReady` before first call. */
export function getSurahInfo(): SurahInfoResponse {
    return _surahInfo;
}

/** Arabic surah name with the leading "سُورَةُ" stripped, falling back to English. */
export function surahName(num: number | string, locale?: string): string {
    const info = _surahInfo[String(num)];
    if (!info) return String(num);
    if (['ar', 'ckb'].includes(locale ?? 'en') && info.name_ar) return info.name_ar.replace(/^سُورَةُ\s*/, '');
    return info.name_en;
}

/**
 * Option text for surah pickers. In `ar` shows "{num} {name_ar}"; otherwise
 * "{num} {name_en}". With no locale the English form is used (legacy callers).
 */
export function surahOptionText(num: number | string, locale?: string): string {
    const info = _surahInfo[String(num)];
    if (!info) return String(num);
    if (['ar', 'ckb'].includes(locale ?? 'en') && info.name_ar) {
        return `${num} ${info.name_ar.replace(/^سُورَةُ\s*/, '')}`;
    }
    return `${num} ${info.name_en}`;
}
