/**
 * Display-name helpers for catalog vocab slugs.
 * Centralized so slugs never leak to the UI.
 */
import * as m from '$lib/paraglide/messages';
import type { PublicDelivery } from '../types/generated/schemas';

const TITLE_CASE_OVERRIDES: Record<string, string> = {
    mp3quran: 'mp3quran',
    quranicaudio: 'quranicaudio',
    qul: 'qul',
    archive_org: 'archive.org',
    tvquran: 'tvquran',
    everyayah: 'everyayah',
    tarteel: 'tarteel',
};

export function titleCaseSlug(slug: string | null | undefined): string {
    if (!slug) return '';
    const override = TITLE_CASE_OVERRIDES[slug];
    if (override) return override;
    return slug
        .split(/[_-]/)
        .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
        .join(' ');
}

/** DB enum vocab kinds that carry a `vocab_<kind>_<value>` translation. */
export type VocabKind = 'riwayah' | 'style' | 'context' | 'coverage';

/**
 * Locale-aware label for a DB enum value (riwayah / style / recording context /
 * coverage). Returns the `vocab_<kind>_<value>` message in the ambient locale, or
 * `titleCaseSlug(value)` when no translation is registered. Channels stay Latin —
 * use `channelDisplay()` for those, not this.
 */
export function vocabLabel(kind: VocabKind, value: string | null | undefined): string {
    if (!value) return '';
    const fn = (m as unknown as Record<string, (() => string) | undefined>)[`vocab_${kind}_${value}`];
    return fn ? fn() : titleCaseSlug(value);
}

const BITRATE_MODE_LABEL: Record<string, () => string> = {
    cbr: m.common_delivery_bitrate_cbr,
    vbr: m.common_delivery_bitrate_vbr,
    mixed: m.common_delivery_bitrate_mixed,
    unknown: () => '',
};

/** Field separator used across all combination display surfaces. */
export const SEP = ' · ';

export function bitrateLabel(d: PublicDelivery): string {
    const mode = (d.bitrate_mode || '').toLowerCase();
    const kbps = d.bitrate_kbps_nominal;
    const modeText = BITRATE_MODE_LABEL[mode]?.() ?? mode.replace(/_/g, ' ');
    if (kbps == null) return modeText || '—';
    if (!modeText) return `${kbps} kbps`;
    return `${kbps} kbps${SEP}${modeText}`;
}

/** Compact coverage badge for the picker — "Full" or "47/114". */
export function compactCoverageLabel(d: PublicDelivery): string {
    if (d.coverage_kind === 'full') return m.common_delivery_coverage_full();
    return `${d.chapter_count}/114`;
}

/** Compact hours badge for the picker — "3.5h", "45m", or "—". */
export function compactHoursLabel(d: PublicDelivery): string {
    if (d.total_duration_sec == null) return '—';
    const h = d.total_duration_sec / 3600;
    if (h < 1) return `${Math.round(h * 60)}m`;
    return `${h.toFixed(1)}h`;
}

/** "x ayahs" if by_ayah, "x surahs" if by_surah. */
export function coverageLabel(d: PublicDelivery): string {
    const n = d.chapter_count;
    return d.audio_category === 'by_ayah'
        ? m.common_delivery_ayah_count({ count: n })
        : m.common_delivery_surah_count({ count: n });
}

export function categoryLabel(d: PublicDelivery): string {
    return d.audio_category === 'by_ayah' ? m.common_delivery_category_ayah() : m.common_delivery_category_surah();
}

export function channelDisplay(d: PublicDelivery): string {
    return d.channel_name || titleCaseSlug(d.channel);
}

/** Total hours from total_duration_sec. */
export function totalHoursLabel(d: PublicDelivery): string {
    const s = d.total_duration_sec;
    if (s == null || s <= 0) return '—';
    const totalMin = Math.round(s / 60);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (h === 0) return `${m}m`;
    return `${h}h ${m.toString().padStart(2, '0')}m`;
}

/**
 * Compact one-line combination label for the bottom-player chip.
 * "Hafs · Murattal · QDC Official"
 */
export function combinationCompact(d: PublicDelivery): string {
    return [vocabLabel('riwayah', d.riwayah), vocabLabel('style', d.style), channelDisplay(d)]
        .filter(Boolean)
        .join(SEP);
}

/**
 * Two-line combination label for the player's combination-switcher dropup.
 * line 1: Riwayah · Style · [Context ·] Channel
 * line 2: Coverage · Bitrate · [Hours]
 */
export function combinationStandard(d: PublicDelivery): { line1: string; line2: string } {
    const line1Parts: string[] = [vocabLabel('riwayah', d.riwayah), vocabLabel('style', d.style)];
    if (d.recording_context) line1Parts.push(vocabLabel('context', d.recording_context));
    line1Parts.push(channelDisplay(d));

    const line2Parts: string[] = [coverageLabel(d), bitrateLabel(d)];
    const hours = totalHoursLabel(d);
    if (hours && hours !== '—') line2Parts.push(hours);

    return {
        line1: line1Parts.filter(Boolean).join(SEP),
        line2: line2Parts.filter(Boolean).join(SEP),
    };
}

// Country display names live in countries.ts (they need its name→code
// table to resolve legacy full-name catalog rows); re-exported for callers.
export { countryName } from './countries';

/**
 * Render an ISO-3166-1 alpha-2 country code as its flag emoji using the
 * Regional Indicator Symbols code-point trick (A=0x1F1E6). Returns an
 * empty string when the code is missing or not a 2-letter shape, so the
 * UI can decide whether to fall back to text. */
export function countryFlag(iso2: string | null | undefined): string {
    if (!iso2) return '';
    const code = iso2.trim().toUpperCase();
    if (code.length !== 2) return '';
    const A = 0x1F1E6;
    const baseA = 'A'.charCodeAt(0);
    if (code.charCodeAt(0) < baseA || code.charCodeAt(0) > baseA + 25) return '';
    if (code.charCodeAt(1) < baseA || code.charCodeAt(1) > baseA + 25) return '';
    return String.fromCodePoint(
        A + code.charCodeAt(0) - baseA,
        A + code.charCodeAt(1) - baseA,
    );
}
