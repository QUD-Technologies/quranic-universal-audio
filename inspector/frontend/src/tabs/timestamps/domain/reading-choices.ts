/**
 * The public catalogue of Hafs reading choices a recitation can show.
 *
 * Each shown selector belongs to a family: selectors that share one localized
 * title, description, group and option order (the four seen/ṣād words, the
 * three sakt pairs). A selector absent from `CHOICES` is never shown publicly
 * (the raa weights, the nasal places and tamanna_noon). `wordForm` splits a
 * selector whose occurrences are different words into one row per word.
 */

import * as m from '$lib/paraglide/messages';

export type ChoiceGroup = 'hamza' | 'letters' | 'joining' | 'sakt' | 'stopping';

/** Selectors sharing one explanation and one option order, shown as one block of word rows. */
export interface ChoiceFamily {
    group: ChoiceGroup;
    /** Options in display order. */
    options: readonly string[];
    title: () => string;
    description: () => string;
}

export interface ChoiceSpec {
    family: string;
    /** Row key of an occurrence's first word ref, for selectors spanning different words. */
    wordForm?: (wordRef: string) => string;
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
    istifham: {
        group: 'hamza', options: ['ibdal', 'tashil'],
        title: m.ts_readings_istifham_title, description: m.ts_readings_istifham_desc,
    },
    seen_saad: {
        group: 'letters', options: ['seen', 'saad'],
        title: m.ts_readings_seen_saad_title, description: m.ts_readings_seen_saad_desc,
    },
    daaf: {
        group: 'letters', options: ['fatha', 'damma'],
        title: m.ts_readings_daaf_title, description: m.ts_readings_daaf_desc,
    },
    opening_letters: {
        group: 'joining', options: ['izhar', 'idgham'],
        title: m.ts_readings_opening_letters_title, description: m.ts_readings_opening_letters_desc,
    },
    merge: {
        group: 'joining', options: ['idgham', 'izhar'],
        title: m.ts_readings_merge_title, description: m.ts_readings_merge_desc,
    },
    sakt: {
        group: 'sakt', options: ['sakt', 'idraj'],
        title: m.ts_readings_sakt_title, description: m.ts_readings_sakt_desc,
    },
    maliyah: {
        group: 'sakt', options: ['sakt', 'idgham'],
        title: m.ts_readings_maliyah_title, description: m.ts_readings_maliyah_desc,
    },
    final_letter: {
        group: 'stopping', options: ['ithbat', 'hadhf'],
        title: m.ts_readings_final_letter_title, description: m.ts_readings_final_letter_desc,
    },
    alism: {
        group: 'stopping', options: ['hamza', 'lam'],
        title: m.ts_readings_alism_title, description: m.ts_readings_alism_desc,
    },
};

const ISTIFHAM_FORMS: Record<string, string> = {
    '6:143:10': 'aldhakarayn',
    '6:144:8': 'aldhakarayn',
    '10:51:7': 'alaan',
    '10:91:1': 'alaan',
    '10:59:14': 'allah',
    '27:59:9': 'allah',
};

export const CHOICES: Record<string, ChoiceSpec> = {
    istifham_article: { family: 'istifham', wordForm: (ref) => ISTIFHAM_FORMS[ref] ?? ref },
    yabsut: { family: 'seen_saad' },
    bastah: { family: 'seen_saad' },
    almusaytirun: { family: 'seen_saad' },
    bimusaytir: { family: 'seen_saad' },
    daaf_haraka: { family: 'daaf' },
    noon_wasl: { family: 'opening_letters' },
    yaseen_wasl: { family: 'opening_letters' },
    irkab_maana: { family: 'merge' },
    yalhath_dhalik: { family: 'merge' },
    iwaja_qayyima: { family: 'sakt' },
    man_raq: { family: 'sakt' },
    bal_ran: { family: 'sakt' },
    maliyah_halak: { family: 'maliyah' },
    yaa_aatani_waqf: { family: 'final_letter' },
    salasila_waqf: { family: 'final_letter' },
    alism_ibtidaa: { family: 'alism' },
};

/** The family of a shown selector. */
export function familyOf(selector: string): ChoiceFamily | undefined {
    const spec = CHOICES[selector];
    return spec ? FAMILIES[spec.family] : undefined;
}

/** Chapters holding at least one shown selector — the only shards the profile reads. */
export const CHOICE_CHAPTERS: readonly number[] = [2, 6, 7, 10, 11, 18, 27, 30, 36, 49, 52, 68, 69, 75, 76, 83, 88];

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

/** The Arabic term of each option — a tajweed term, shown beside the English label. */
export const OPTION_TERM_AR: Record<string, string> = {
    ibdal: 'إبدال',
    tashil: 'تسهيل',
    fatha: 'فتح',
    damma: 'ضم',
    seen: 'سين',
    saad: 'صاد',
    izhar: 'إظهار',
    idgham: 'إدغام',
    sakt: 'سكت',
    idraj: 'إدراج',
    ithbat: 'إثبات',
    hadhf: 'حذف',
    hamza: 'همزة',
    lam: 'لام',
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
