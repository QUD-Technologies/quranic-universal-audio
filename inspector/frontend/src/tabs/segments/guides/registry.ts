import type { Locale } from '$lib/i18n/locale-store';

import { IssueRegistry } from '../domain/registry';

import basmalaAminGuide from './accordion/basmala_amin.guide';
import boundaryAdjGuide from './accordion/boundary_adj.guide';
import crossVerseGuide from './accordion/cross_verse.guide';
import failedGuide from './accordion/failed.guide';
import flaggingGuide from './accordion/flagging.guide';
import generalEditingGuide from './accordion/general_editing.guide';
import lowConfidenceGuide from './accordion/low_confidence.guide';
import lowConfidenceV2Guide from './accordion/low_confidence_v2.guide';
import missingVersesGuide from './accordion/missing_verses.guide';
import missingWordsGuide from './accordion/missing_words.guide';
import muqattaatGuide from './accordion/muqattaat.guide';
import overviewGuide from './accordion/overview.guide';
import qalqalaGuide from './accordion/qalqala.guide';
import repetitionsGuide from './accordion/repetitions.guide';

const accordionGuides: Readonly<Record<string, string>> = Object.freeze({
    overview: overviewGuide,
    general_editing: generalEditingGuide,
    failed: failedGuide,
    missing_verses: missingVersesGuide,
    missing_words: missingWordsGuide,
    low_confidence: lowConfidenceGuide,
    low_confidence_v2: lowConfidenceV2Guide,
    boundary_adj: boundaryAdjGuide,
    repetitions: repetitionsGuide,
    cross_verse: crossVerseGuide,
    qalqala: qalqalaGuide,
    muqattaat: muqattaatGuide,
    basmala_amin: basmalaAminGuide,
    flagging: flaggingGuide,
});

// Locale-variant guide sources (translate-as-documents). Each translated guide
// is a sibling `accordion/<category>.<locale>.guide.ts` that default-exports the
// same source-string shape as its English base, with only prose/headings
// translated (directives/`::example` ids/audio refs stay byte-identical). We
// discover them with an eager glob so Vite bundles whatever exists — a category
// with no `.ar` sibling simply falls back to English. To add a translation a
// translator only drops in the new file; no edit here is required.
//
// Glob keys look like `./accordion/muqattaat.ar.guide.ts`; we index by
// `{ locale: { category: source } }`.
const localeGuideModules = import.meta.glob<{ default: string }>(
    './accordion/*.*.guide.ts',
    { eager: true },
);

const accordionGuidesByLocale: Readonly<Record<string, Record<string, string>>> = Object.freeze(
    (() => {
        const byLocale: Record<string, Record<string, string>> = {};
        const re = /\.\/accordion\/(.+)\.([a-z]{2})\.guide\.ts$/;
        for (const [path, mod] of Object.entries(localeGuideModules)) {
            const match = re.exec(path);
            if (!match) continue;
            const [, category, locale] = match;
            if (!category || !locale) continue;
            (byLocale[locale] ??= {})[category] = mod.default;
        }
        return byLocale;
    })(),
);

/**
 * The guide source for `category`, in `locale` when a translated sibling exists,
 * else the English base (null if the category is unknown). Locale-aware callers
 * must invoke this reactively — resolve it at open time or gate a `$derived` on
 * the current locale — so a switch re-selects the source.
 */
export function getAccordionGuide(category: string, locale?: Locale): string | null {
    if (locale && locale !== 'en') {
        const localized = accordionGuidesByLocale[locale]?.[category];
        if (localized != null) return localized;
    }
    return accordionGuides[category] ?? null;
}

/** True iff a category has a guide — used to hide the help `?` otherwise. */
export function hasAccordionGuide(category: string): boolean {
    return category in accordionGuides;
}

// --- Read-tracking (unread border + first-edit onboarding gate) ------------
//
// A guide's *view key* is the identity we persist per user. `low_confidence`
// and `low_confidence_v2` share one guide (v2 re-exports the same source), so
// they collapse to a single required reading: opening either clears both, and
// storage never holds `low_confidence_v2`. Mirror this in the backend
// (`inspector/constants.py::guide_view_key`) when the mapping changes.

const GUIDE_VIEW_KEY_ALIASES: Readonly<Record<string, string>> = Object.freeze({
    low_confidence_v2: 'low_confidence',
});

/** Collapse a category to the key we persist (folds the low_confidence alias). */
export function guideViewKey(category: string): string {
    return GUIDE_VIEW_KEY_ALIASES[category] ?? category;
}

/**
 * Every guide key the backend will store a view for — mirrors
 * ``inspector/constants.py::GUIDE_VIEW_KEYS``. Superset of
 * `REQUIRED_GUIDE_KEYS`: an owner-only category's guide is still readable (and
 * its read still recorded) from the accordion `?`, it just doesn't onboard.
 * Order is the canonical reading order.
 */
export const ALL_GUIDE_KEYS: readonly string[] = Object.freeze([
    'overview',
    'general_editing',
    'failed',
    'missing_verses',
    'missing_words',
    'low_confidence',
    'boundary_adj',
    'repetitions',
    'cross_verse',
    'qalqala',
    'muqattaat',
    'basmala_amin',
    'flagging',
]);

/**
 * The distinct guides a reviewer must read once before editing — the gate set.
 * `ALL_GUIDE_KEYS` minus the guides for owner-only validation categories
 * (`IssueRegistry[cat].ownerOnly`), which are invisible to non-owners and so
 * must not gate anyone's first edit or show up in the guides checklist. A guide
 * that should NOT re-onboard established reviewers can be registered in
 * `accordionGuides` but left out of `ALL_GUIDE_KEYS` (badge-only).
 */
export const REQUIRED_GUIDE_KEYS: readonly string[] = Object.freeze(
    ALL_GUIDE_KEYS.filter((k) => !IssueRegistry[k]?.ownerOnly),
);

/** True iff this user has opened the guide for `category` (alias-aware). */
export function isGuideRead(guidesRead: readonly string[], category: string): boolean {
    return guidesRead.includes(guideViewKey(category));
}

/** True iff every required guide has been read — the edit-gate predicate. */
export function allGuidesRead(guidesRead: readonly string[]): boolean {
    return REQUIRED_GUIDE_KEYS.every((k) => guidesRead.includes(k));
}
