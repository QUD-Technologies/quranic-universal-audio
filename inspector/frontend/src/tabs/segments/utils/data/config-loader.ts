import { fetchJsonOrNull } from '../../../../lib/api';
import { DEFAULT_RIWAYAH, type InspectorRiwayah } from '../../../../lib/riwayat';
import {
    SCROLL_ANIM_DEFAULT,
    SCROLL_ANIM_MODES,
    type ScrollAnimMode,
} from '../../../../lib/utils/constants';
import { resetSegConfig, segConfig } from '../../stores/config';

type SegConfigApiResponse = {
    seg_font_size?: string;
    seg_word_spacing?: string;
    seg_scroll_anim_mode?: string;
    trim_pad_left?: number;
    trim_pad_right?: number;
    trim_dim_alpha?: number;
    validation_categories?: string[];
    low_conf_default_threshold?: number;
    qalqala_letters?: string[];
    muqattaat_words?: Array<[number, number, number]>;
    accordion_context?: Record<string, string>;
    riwayah?: string;
};

const _validAnim = new Set<string>(Object.values(SCROLL_ANIM_MODES));

/**
 * Fetch `/api/seg/config` for one edition, push parsed values to `segConfig`,
 * and return CSS var strings `{ fontSize, wordSpacing }` so the tab can apply
 * them to its root element.
 *
 * The coordinate vocabularies are edition-specific — the muqattaat openings
 * move between riwayat — so a reciter switch across editions must re-fetch,
 * not reuse.
 *
 * Responses are not last-write-wins: the backend echoes the edition it answered
 * for, and a stale one is discarded. Two fetches race on every tab open, and on
 * a single-worker backend the Hafs one can land after the Warsh one.
 */
let _wanted: InspectorRiwayah = DEFAULT_RIWAYAH;

export function clearSegConfig(): void {
    _wanted = DEFAULT_RIWAYAH;
    resetSegConfig();
}

export async function loadSegConfig(
    riwayah: InspectorRiwayah = DEFAULT_RIWAYAH,
): Promise<{ fontSize: string; wordSpacing: string }> {
    _wanted = riwayah;
    try {
        const cfg = await fetchJsonOrNull<SegConfigApiResponse>(
            `/api/seg/config?riwayah=${encodeURIComponent(riwayah)}`,
        );
        if (!cfg) return { fontSize: '', wordSpacing: '' };
        if (_wanted !== riwayah) return { fontSize: '', wordSpacing: '' };
        segConfig.set({
            validationCategories: cfg.validation_categories ?? null,
            muqattaatWords: cfg.muqattaat_words ? new Set(cfg.muqattaat_words.map(([s, a, w]) => `${s}:${a}:${w}`)) : null,
            qalqalaLetters: cfg.qalqala_letters ? new Set(cfg.qalqala_letters) : null,
            lcDefaultThreshold: cfg.low_conf_default_threshold ?? 80,
            accordionContext: cfg.accordion_context ?? null,
            trimPadLeft: cfg.trim_pad_left ?? 500,
            trimPadRight: cfg.trim_pad_right ?? 500,
            trimDimAlpha: cfg.trim_dim_alpha ?? 0.45,
            scrollAnimMode: cfg.seg_scroll_anim_mode
                && _validAnim.has(cfg.seg_scroll_anim_mode)
                ? (cfg.seg_scroll_anim_mode as ScrollAnimMode)
                : SCROLL_ANIM_DEFAULT,
        });
        return {
            fontSize: cfg.seg_font_size ? String(cfg.seg_font_size) : '',
            wordSpacing: cfg.seg_word_spacing ? String(cfg.seg_word_spacing) : '',
        };
    } catch {
        return { fontSize: '', wordSpacing: '' };
    }
}
