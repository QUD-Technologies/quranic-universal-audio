/**
 * The public catalogue of Hafs reading choices a recitation can show.
 *
 * Each shown selector belongs to a family: selectors that share one group,
 * one option order and one localized title (the four seen/ṣād words, the three
 * sakt pairs). The readings panel lays rows out by group and family; the
 * per-row variant hover names a selector by its family title. A selector absent
 * from `CHOICES` is never shown publicly (the raa weights, the nasal places
 * and tamanna_noon). Option labels follow the UI locale only.
 */

import * as m from '$lib/paraglide/messages';

export type ChoiceGroup = 'hamza' | 'letters' | 'joining' | 'sakt' | 'stopping';

export interface ChoiceFamily {
    group: ChoiceGroup;
    /** Options in display order. */
    options: readonly string[];
    title: () => string;
}

export const GROUP_ORDER: readonly ChoiceGroup[] = ['hamza', 'letters', 'joining', 'sakt', 'stopping'];

export const GROUP_TITLE: Record<ChoiceGroup, () => string> = {
    hamza: m.ts_readings_group_hamza,
    letters: m.ts_readings_group_letters,
    joining: m.ts_readings_group_joining,
    sakt: m.ts_readings_group_sakt,
    stopping: m.ts_readings_group_stopping,
};

export const FAMILIES: Record<string, ChoiceFamily> = {
    istifham: { group: 'hamza', options: ['ibdal', 'tashil'], title: m.ts_readings_istifham_title },
    seen_saad: { group: 'letters', options: ['seen', 'saad'], title: m.ts_readings_seen_saad_title },
    daaf: { group: 'letters', options: ['fatha', 'damma'], title: m.ts_readings_daaf_title },
    opening_letters: {
        group: 'joining', options: ['izhar', 'idgham'], title: m.ts_readings_opening_letters_title,
    },
    merge: { group: 'joining', options: ['idgham', 'izhar'], title: m.ts_readings_merge_title },
    sakt: { group: 'sakt', options: ['sakt', 'idraj'], title: m.ts_readings_sakt_title },
    maliyah: { group: 'sakt', options: ['sakt', 'idgham'], title: m.ts_readings_maliyah_title },
    final_letter: {
        group: 'stopping', options: ['ithbat', 'hadhf'], title: m.ts_readings_final_letter_title,
    },
    alism: { group: 'stopping', options: ['hamza', 'lam'], title: m.ts_readings_alism_title },
};

/** Shown selector → its family. */
export const CHOICES: Record<string, string> = {
    istifham_article: 'istifham',
    yabsut: 'seen_saad',
    bastah: 'seen_saad',
    almusaytirun: 'seen_saad',
    bimusaytir: 'seen_saad',
    daaf_haraka: 'daaf',
    noon_wasl: 'opening_letters',
    yaseen_wasl: 'opening_letters',
    irkab_maana: 'merge',
    yalhath_dhalik: 'merge',
    iwaja_qayyima: 'sakt',
    man_raq: 'sakt',
    bal_ran: 'sakt',
    maliyah_halak: 'maliyah',
    yaa_aatani_waqf: 'final_letter',
    salasila_waqf: 'final_letter',
    alism_ibtidaa: 'alism',
};

/** The family of a shown selector. */
export function familyOf(selector: string): ChoiceFamily | undefined {
    const family = CHOICES[selector];
    return family ? FAMILIES[family] : undefined;
}

const OPTION_LABEL: Record<string, () => string> = {
    ibdal: m.ts_readings_option_ibdal,
    tashil: m.ts_readings_option_tashil,
    fatha: m.ts_readings_option_fatha,
    damma: m.ts_readings_option_damma,
    seen: m.ts_readings_option_seen,
    saad: m.ts_readings_option_saad,
    izhar: m.ts_readings_option_izhar,
    idgham: m.ts_readings_option_idgham,
    sakt: m.ts_readings_option_sakt,
    idraj: m.ts_readings_option_idraj,
    ithbat: m.ts_readings_option_ithbat,
    hadhf: m.ts_readings_option_hadhf,
    hamza: m.ts_readings_option_hamza,
    lam: m.ts_readings_option_lam,
};

/** The option's label in the viewer's locale; an unknown option is title-cased. */
export function optionLabel(option: string): string {
    const label = OPTION_LABEL[option];
    return label ? label() : option.charAt(0).toUpperCase() + option.slice(1);
}

/** The selector's public title, or undefined for a selector never shown publicly. */
export function choiceTitle(selector: string): string | undefined {
    return familyOf(selector)?.title();
}
