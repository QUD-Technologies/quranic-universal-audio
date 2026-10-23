/** Localized name of a recited opener, for surfaces that label it in place of an ayah number. */

import * as m from '$lib/paraglide/messages';

import type { SpecialKind } from '../types/ts-client';

const LABELS: Record<SpecialKind, () => string> = {
    "Isti'adha": () => m.common_filmstrip_special_istiadha(),
    Basmala: () => m.common_filmstrip_special_basmala(),
};

/** The opener's name in the active locale. Call inside a locale-reactive scope. */
export function specialLabel(kind: SpecialKind): string {
    return LABELS[kind]();
}
