/**
 * Locale lookup for validation-accordion titles, keyed by `IssueRegistry` kind.
 *
 * `registry.ts` `displayTitle` is parity-pinned against the Python registry
 * (`__tests__/registry/parity.test.ts` asserts the literal English string) —
 * it must never be replaced with a message call. This module is the render-site
 * indirection: consumers look up `VALIDATION_TITLE[kind]()` instead of reading
 * `displayTitle` directly, so the accordion UI localizes without touching the
 * parity anchor. The English values here mirror `displayTitle` verbatim.
 */
import * as m from '../../../lib/paraglide/messages';

export const VALIDATION_TITLE: Readonly<Record<string, () => string>> = Object.freeze({
    failed: m.segments_validation_failed_title,
    missing_verses: m.segments_validation_missing_verses_title,
    missing_words: m.segments_validation_missing_words_title,
    structural_errors: m.segments_validation_structural_errors_title,
    low_confidence: m.segments_validation_low_confidence_title,
    low_confidence_v2: m.segments_validation_low_confidence_v2_title,
    repetitions: m.segments_validation_repetitions_title,
    audio_bleeding: m.segments_validation_audio_bleeding_title,
    boundary_adj: m.segments_validation_boundary_adj_title,
    cross_verse: m.segments_validation_cross_verse_title,
    missed_waqf: m.segments_validation_missed_waqf_title,
    qalqala: m.segments_validation_qalqala_title,
    muqattaat: m.segments_validation_muqattaat_title,
    basmala_amin: m.segments_validation_basmala_amin_title,
    hidden_pause: m.segments_validation_hidden_pause_title,
    false_split: m.segments_validation_false_split_title,
    unmarked_wasl: m.segments_validation_unmarked_wasl_title,
});
