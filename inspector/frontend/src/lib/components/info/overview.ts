/**
 * Parsed project-overview document — parsed once at module load and shared by
 * every consumer (the dashboard `InfoModal` for its title + body, and
 * `OverviewContent` for the rendered blocks). Edit wording in `overview.md`.
 *
 * Translated variants are authored as `overview.<locale>.md` siblings with the
 * identical structure (heading markers, `- ` bullets, `::lifecycle` + verbatim
 * state keys, link hrefs); `getOverviewDoc(locale)` selects them and falls back
 * to English until a translation exists. The variants are discovered via an
 * eager glob so a translator only drops the file in — no edit here.
 */
import type { Locale } from '$lib/i18n/locale-store';

import { type InfoDoc, parseInfoDoc } from './info-doc';
import OVERVIEW_MD from './overview.md?raw';

/** English (base) doc — the default for any un-migrated consumer. */
export const overviewDoc = parseInfoDoc(OVERVIEW_MD);

const localeMd = import.meta.glob<string>('./overview.*.md', {
    eager: true,
    query: '?raw',
    import: 'default',
});

const overviewDocByLocale: Record<string, InfoDoc> = {};
{
    const re = /^\.\/overview\.([a-z]{2,3})\.md$/;
    for (const [path, raw] of Object.entries(localeMd)) {
        const match = re.exec(path);
        if (!match || !match[1]) continue;
        overviewDocByLocale[match[1]] = parseInfoDoc(raw);
    }
}

/**
 * Parsed overview doc for `locale`, or English when no translation exists.
 * Locale-aware consumers call this reactively (gate a `$derived` on the current
 * locale / read `$localeStore`) so a switch re-selects the doc.
 */
export function getOverviewDoc(locale: Locale): InfoDoc {
    return overviewDocByLocale[locale] ?? overviewDoc;
}
