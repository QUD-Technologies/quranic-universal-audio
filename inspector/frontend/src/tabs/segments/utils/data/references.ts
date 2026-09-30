import { get } from 'svelte/store';

import { quranRefs } from '../../../../lib/refs/quran-refs';
import type { Ref, VerseRef } from '../../../../lib/types/view-models';
import { _ARABIC_DIGITS } from '../constants';

/** Parsed canonical segment ref. */
export interface ParsedSegRef {
    surah: number;
    ayah_from: number;
    word_from: number;
    ayah_to: number;
    word_to: number;
}

type VerseWordCounts = Record<VerseRef, number>;

/** Read verse-word-counts from the shared Quran-refs bundle. */
export function getVerseWordCounts(): VerseWordCounts | undefined {
    return get(quranRefs)?.verse_word_counts as VerseWordCounts | undefined;
}

/**
 * The glyph that precedes an Arabic-Indic verse number in the loaded edition.
 *
 * U+06DD for Hafs, whose DigitalKhatt font expects the ornament sent alongside
 * the digits; empty for the three packaged QPC fonts, which decorate the digits
 * themselves — sending both renders two nested ornaments. The bundle carries it
 * rather than the FE assuming, so the two apps agree.
 *
 * Defaults to the Hafs ornament before the bundle loads: that is what every
 * pre-multi-riwayah render did, and the alternative (no marker at all) reads as
 * a rendering bug rather than as "still loading".
 */
export function verseMarkerPrefix(): string {
    return get(quranRefs)?.verse_marker_prefix ?? '۝';
}

export function isCrossVerse(ref: Ref | null | undefined): boolean {
    if (!ref) return false;
    const parts = ref.split('-');
    if (parts.length !== 2 || !parts[0] || !parts[1]) return false;
    const startAyah = parts[0].split(':')[1];
    const endAyah = parts[1].split(':')[1];
    return startAyah !== endAyah;
}

export function parseSegRef(ref: Ref | null | undefined): ParsedSegRef | null {
    if (!ref) return null;
    const parts = ref.split('-');
    if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
    const s = parts[0].split(':'), e = parts[1].split(':');
    if (s.length < 3 || e.length < 3) return null;
    if (s[0] == null || s[1] == null || s[2] == null || e[1] == null || e[2] == null) return null;
    return { surah: +s[0], ayah_from: +s[1], word_from: +s[2], ayah_to: +e[1], word_to: +e[2] };
}

/** `s:a:last` for every verse that ends inside `ref` (same-surah spans only). */
export function verseEndsIn(ref: Ref | null | undefined, vwc?: VerseWordCounts): string[] {
    const p = parseSegRef(ref);
    if (!p || !ref || ref.split('-')[0]!.split(':')[0] !== ref.split('-')[1]!.split(':')[0]) return [];
    const out: string[] = [];
    for (let a = p.ayah_from; a < p.ayah_to; a++) {
        const last = vwc?.[`${p.surah}:${a}`];
        if (last) out.push(`${p.surah}:${a}:${last}`);
    }
    return out;
}

export function countSegWords(ref: Ref | null | undefined, vwc?: VerseWordCounts): number {
    const p = parseSegRef(ref);
    if (!p) return 0;
    if (p.ayah_from === p.ayah_to) return p.word_to - p.word_from + 1;
    let total = 0;
    for (let a = p.ayah_from; a <= p.ayah_to; a++) {
        const key = `${p.surah}:${a}`;
        if (a === p.ayah_from)      total += (vwc?.[key] ?? p.word_from) - p.word_from + 1;
        else if (a === p.ayah_to)   total += p.word_to;
        else                        total += vwc?.[key] ?? 0;
    }
    return total;
}

export function _toArabicNumeral(n: number): string {
    return String(n).split('').map((d) => _ARABIC_DIGITS[+d] ?? d).join('');
}

/** Normalize a short ref to canonical surah:ayah:word-surah:ayah:word format. */
export function _normalizeRef(ref: Ref | null | undefined, vwc?: VerseWordCounts): Ref | null | undefined {
    if (!ref) return ref;
    const parts = ref.split('-');
    if (parts.length === 2 && parts[0] && parts[1]) {
        const s = parts[0].split(':'), e = parts[1].split(':');
        if (s.length === 3 && e.length === 3) return ref; // already canonical
        if (s.length === 2 && e.length === 2) {
            const n = vwc?.[`${e[0]}:${e[1]}`] || 1;
            return `${s[0]}:${s[1]}:1-${e[0]}:${e[1]}:${n}`;
        }
    } else if (parts.length === 1) {
        const c = ref.split(':');
        if (c.length === 2) {
            const n = vwc?.[`${c[0]}:${c[1]}`] || 1;
            return `${c[0]}:${c[1]}:1-${c[0]}:${c[1]}:${n}`;
        }
        if (c.length === 3) return `${ref}-${ref}`;
    }
    return ref;
}

/**
 * Build Digital Khatt display text for a canonical word-range ref.
 *
 * TS port of `services/data_loader.py::dk_text_for_ref`. Walks `surah:ayah:word`
 * keys from start endpoint through end endpoint (inclusive), advancing the
 * word counter through the verse-word-counts map at each boundary. Returns ''
 * for malformed / missing inputs so callers can decide a fallback string.
 *
 * Parity with the Python implementation is enforced at the call site —
 * the response root carries the same `dkWords` map the server would use.
 */
export function dkTextForRef(
    ref: Ref | null | undefined,
    dkWords: Record<string, string> | undefined,
    vwc: VerseWordCounts | undefined,
): string {
    if (!ref || !dkWords) return '';
    const parts = ref.split('-');
    if (parts.length !== 2 || !parts[0] || !parts[1]) return '';
    const sP = parts[0].split(':');
    const eP = parts[1].split(':');
    if (sP.length !== 3 || eP.length !== 3) return '';
    const sSu = +sP[0]!, sAy = +sP[1]!, sW = +sP[2]!;
    const eSu = +eP[0]!, eAy = +eP[1]!, eW = +eP[2]!;
    if (![sSu, sAy, sW, eSu, eAy, eW].every(Number.isFinite)) return '';

    // Segments never cross surahs in practice — Python `dk_text_for_ref`
    // shares this assumption. If `sSu !== eSu` the ref is malformed and the
    // ayah-overflow guard below short-circuits the walk.
    const su = sSu;
    const words: string[] = [];
    let ay = sAy, w = sW;
    // Lexicographic (su, ay, w) ≤ (eSu, eAy, eW) — mirrors Python tuple compare.
    const lte = (): boolean =>
        su < eSu
        || (su === eSu && (ay < eAy || (ay === eAy && w <= eW)));
    while (lte()) {
        const t = dkWords[`${su}:${ay}:${w}`];
        if (t) words.push(t);
        w++;
        const max = vwc?.[`${su}:${ay}`] ?? 0;
        if (w > max) {
            w = 1;
            ay++;
            // Mirrors MAX_AYAH_BOUNDARY_CHECK (formerly in config.py).
            // Runaway ayah means malformed input.
            if (ay > 300) break;
        }
    }
    return words.join(' ');
}

/** Insert verse end markers at verse boundaries within segment text. */
export function _addVerseMarkers(text: string | null | undefined, ref: Ref | null | undefined, vwc?: VerseWordCounts): string {
    if (!text || !ref) return text ?? '';
    const normalized = _normalizeRef(ref, vwc);
    const p = parseSegRef(normalized);
    if (!p || !vwc) return text;

    const words = text.split(/\s+/).filter(Boolean);
    const out: string[] = [];
    let ay = p.ayah_from, w = p.word_from;

    for (let i = 0; i < words.length; i++) {
        const word = words[i];
        if (word == null) continue;
        out.push(word);
        if (!/[\u0600-\u066F]/.test(word)) continue;
        const total = vwc[`${p.surah}:${ay}`] || 0;
        if (total > 0 && w >= total) {
            out.push(verseMarkerPrefix() + _toArabicNumeral(ay));
            ay++;
            w = 1;
        } else {
            w++;
        }
    }
    return out.join(' ');
}

export function formatRef(ref: Ref | null | undefined, vwc?: VerseWordCounts): string {
    if (!ref) return '(no match)';
    if (!vwc) return ref;
    const parts = ref.split('-');
    if (parts.length !== 2 || !parts[0] || !parts[1]) return ref;
    const start = parts[0].split(':');
    const end = parts[1].split(':');
    if (start.length !== 3 || end.length !== 3) return ref;
    if (start[0] === end[0] && start[1] === end[1] && start[2] === '1') {
        const key = `${start[0]}:${start[1]}`;
        const totalWords = vwc[key];
        if (totalWords && end[2] != null && parseInt(end[2]) === totalWords) {
            return key;
        }
    }
    return ref;
}

export function formatTimeMs(ms: number): string {
    if (!isFinite(ms)) return '0:00';
    const totalSec = ms / 1000;
    const mins = Math.floor(totalSec / 60);
    const secs = Math.floor(totalSec % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/**
 * Format ms as fixed-width `hh:mm:ss.MMM`. Used by the SegmentRow time-edit
 * widget where monospace alignment and per-digit-group addressing matter.
 * Negative or non-finite inputs collapse to `00:00:00.000`.
 */
export function formatHmsMs(ms: number): string {
    if (!isFinite(ms) || ms < 0) ms = 0;
    const total = Math.floor(ms);
    const hh = Math.floor(total / 3600000);
    const mm = Math.floor((total % 3600000) / 60000);
    const ss = Math.floor((total % 60000) / 1000);
    const mmm = total % 1000;
    const pad2 = (n: number): string => n.toString().padStart(2, '0');
    const pad3 = (n: number): string => n.toString().padStart(3, '0');
    return `${pad2(hh)}:${pad2(mm)}:${pad2(ss)}.${pad3(mmm)}`;
}

/** Compose ms from four digit-group integers. Caller is responsible for
 *  passing values already validated against their per-group ranges. */
export function composeHmsMs(hh: number, mm: number, ss: number, mmm: number): number {
    return hh * 3600000 + mm * 60000 + ss * 1000 + mmm;
}

/** Split ms into its four digit-group integers (hh/mm/ss/MMM). */
export function splitHmsMs(ms: number): { hh: number; mm: number; ss: number; mmm: number } {
    const total = Math.max(0, Math.floor(isFinite(ms) ? ms : 0));
    return {
        hh: Math.floor(total / 3600000),
        mm: Math.floor((total % 3600000) / 60000),
        ss: Math.floor((total % 60000) / 1000),
        mmm: total % 1000,
    };
}

export function formatDurationMs(ms: number): string {
    if (!isFinite(ms) || ms === 0) return '0s';
    const seconds = ms / 1000;
    if (seconds < 60) return seconds.toFixed(1) + 's';
    const mins = Math.floor(seconds / 60);
    const secs = (seconds % 60).toFixed(0);
    return `${mins}m ${secs}s`;
}

/** A single ref endpoint — surah:ayah:word triple. */
export interface RefPoint {
    surah: number;
    ayah: number;
    word: number;
}

/**
 * Advance a ref endpoint by one word.
 *
 * - Mid-verse: word + 1.
 * - Last word of verse: roll over to {ayah+1, word=1} if `s:ayah+1` is in vwc.
 * - Last verse of surah (or vwc unavailable for next verse): return null.
 *   Segments don't cross surahs in practice, so no cross-surah rollover.
 */
export function _advanceRefByOneWord(point: RefPoint, vwc?: VerseWordCounts): RefPoint | null {
    if (!vwc) return null;
    const verseKey = `${point.surah}:${point.ayah}`;
    const verseSize = vwc[verseKey];
    if (verseSize == null) return null;
    if (point.word < verseSize) {
        return { surah: point.surah, ayah: point.ayah, word: point.word + 1 };
    }
    const nextKey = `${point.surah}:${point.ayah + 1}`;
    if (vwc[nextKey] == null) return null;
    return { surah: point.surah, ayah: point.ayah + 1, word: 1 };
}

/**
 * Clamp word overshoot within known verses. If `word_from > vwc[s:ayah_from]`
 * (and analogously for end), clamps to that verse's word count. Returns the
 * clamped ref + a flag. If either endpoint's verse is unknown to vwc, returns
 * {clamped: false} — that's an `unknown_verse` case, not a recoverable
 * overshoot, and should be reported as invalid by the caller.
 */
export function _clampRefWordOvershoot(
    parsed: ParsedSegRef,
    vwc?: VerseWordCounts,
): { clampedRef: Ref; clamped: boolean } | null {
    if (!vwc) return null;
    const fromKey = `${parsed.surah}:${parsed.ayah_from}`;
    const toKey = `${parsed.surah}:${parsed.ayah_to}`;
    const fromMax = vwc[fromKey];
    const toMax = vwc[toKey];
    if (fromMax == null || toMax == null) return null;
    let wf = parsed.word_from;
    let wt = parsed.word_to;
    let clamped = false;
    if (wf > fromMax) { wf = fromMax; clamped = true; }
    if (wt > toMax) { wt = toMax; clamped = true; }
    const clampedRef: Ref =
        `${parsed.surah}:${parsed.ayah_from}:${wf}-${parsed.surah}:${parsed.ayah_to}:${wt}`;
    return { clampedRef, clamped };
}

export type RefValidation =
    | { ok: true; ref: Ref }
    | { ok: false; reason: 'malformed' | 'unknown_verse' | 'word_overshoot'; clamped?: Ref };

/**
 * Structural validation of a ref against vwc. Caller is expected to have run
 * `_normalizeRef` first so shorthand is expanded.
 *
 * - `malformed`: parseSegRef fails (bad format, non-numeric, missing parts).
 * - `unknown_verse`: any of the verse keys (`s:ayah_from`, `s:ayah_to`) is
 *   absent from vwc — out-of-range surah/verse, not a recoverable typo.
 * - `word_overshoot`: structure is fine and verses are known, but word count
 *   exceeds the verse size; `clamped` carries the corrected ref so the caller
 *   can opt into the silent-fix path.
 * - `ok`: passes all checks; `ref` is the canonical form.
 */
export function _validateRefStructural(ref: Ref | null | undefined, vwc?: VerseWordCounts): RefValidation {
    if (!ref) return { ok: false, reason: 'malformed' };
    const p = parseSegRef(ref);
    if (!p || !Number.isFinite(p.surah) || !Number.isFinite(p.ayah_from) ||
        !Number.isFinite(p.word_from) || !Number.isFinite(p.ayah_to) ||
        !Number.isFinite(p.word_to)) {
        return { ok: false, reason: 'malformed' };
    }
    if (p.surah < 1 || p.ayah_from < 1 || p.word_from < 1 ||
        p.ayah_to < p.ayah_from || p.word_to < 1 ||
        (p.ayah_from === p.ayah_to && p.word_to < p.word_from)) {
        return { ok: false, reason: 'malformed' };
    }
    if (vwc) {
        const fromKey = `${p.surah}:${p.ayah_from}`;
        const toKey = `${p.surah}:${p.ayah_to}`;
        if (vwc[fromKey] == null || vwc[toKey] == null) {
            return { ok: false, reason: 'unknown_verse' };
        }
        const clamp = _clampRefWordOvershoot(p, vwc);
        if (clamp?.clamped) {
            return { ok: false, reason: 'word_overshoot', clamped: clamp.clampedRef };
        }
    }
    return { ok: true, ref };
}

/**
 * Suggest per-verse refs for the two halves when splitting a cross-verse segment.
 * Returns {first, second} ref strings or null if single-verse or data unavailable.
 */
export function _suggestSplitRefs(ref: Ref, vwc?: VerseWordCounts): { first: Ref; second: Ref } | null {
    const p = parseSegRef(ref);
    if (!p || p.ayah_from === p.ayah_to) return null;
    if (!vwc) return null;
    const firstVerseKey = `${p.surah}:${p.ayah_from}`;
    const firstEnd = vwc[firstVerseKey];
    if (!firstEnd) return null;

    const first: Ref = `${p.surah}:${p.ayah_from}:${p.word_from}-${p.surah}:${p.ayah_from}:${firstEnd}`;

    const nextAyah = p.ayah_from + 1;
    const second: Ref = (nextAyah === p.ayah_to)
        ? `${p.surah}:${p.ayah_to}:1-${p.surah}:${p.ayah_to}:${p.word_to}`
        : `${p.surah}:${nextAyah}:1-${p.surah}:${p.ayah_to}:${p.word_to}`;

    return { first, second };
}
