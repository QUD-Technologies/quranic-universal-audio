/**
 * Localized names for the recitation profile: its sections, each measure (madd type,
 * ghunnah, pauses) and each madd length with its count of ḥarakāt. Labels
 * follow the UI locale only.
 */

import * as m from '$lib/paraglide/messages';
import type { MaddLength, ProfileMeasure, ProfileSection } from '../utils/recitation-profile';

export const SECTION_TITLE: Record<ProfileSection['section'], () => string> = {
    madd: m.ts_profile_madd_title,
    sound: m.ts_profile_sound_title,
};

export const MEASURE_LABEL: Record<ProfileMeasure, () => string> = {
    tabii: m.ts_profile_madd_tabii,
    munfasil: m.ts_profile_madd_munfasil,
    muttasil: m.ts_profile_madd_muttasil,
    lazim: m.ts_profile_madd_lazim,
    arid: m.ts_profile_madd_arid,
    leen: m.ts_profile_madd_leen,
    ghunnah: m.ts_profile_ghunnah,
    pauses: m.ts_profile_pauses,
};

export const LENGTH_LABEL: Record<MaddLength, () => string> = {
    qasr: m.ts_profile_length_qasr,
    tawassut: m.ts_profile_length_tawassut,
    ishbaa: m.ts_profile_length_ishbaa,
};

const LENGTH_COUNTS_LABEL: Record<MaddLength, () => string> = {
    qasr: m.ts_profile_counts_qasr,
    tawassut: m.ts_profile_counts_tawassut,
    ishbaa: m.ts_profile_counts_ishbaa,
};

/** "Tawassut · 4 counts" / "توسط · ٤ حركات". */
export function lengthOption(length: MaddLength): string {
    return m.ts_profile_length_option({ length: LENGTH_LABEL[length](), counts: LENGTH_COUNTS_LABEL[length]() });
}
