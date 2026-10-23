/** Localized name of a special (a recited opener, Amin, …), for surfaces that label it in place of an ayah number or verse text. */

import * as m from '$lib/paraglide/messages';

import type { SpecialEntry } from '../types/generated/schemas';

/** A special's name as stored in `matched_ref` and `specials.json`. */
export type SpecialName = SpecialEntry['type'];

const LABELS: Record<SpecialName, () => string> = {
    "Isti'adha": () => m.common_filmstrip_special_istiadha(),
    Basmala: () => m.common_filmstrip_special_basmala(),
    Amin: () => m.common_special_amin(),
    Sadaqa: () => m.common_special_sadaqa(),
    Takbir: () => m.common_special_takbir(),
    Tahmeed: () => m.common_special_tahmeed(),
    Tasleem: () => m.common_special_tasleem(),
};

/** The special's name in the active locale. Call inside a locale-reactive scope. */
export function specialLabel(kind: SpecialName): string {
    return LABELS[kind]();
}
