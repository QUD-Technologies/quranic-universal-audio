/**
 * Locale-aware number display.
 *
 * `localizeDigits` maps the ASCII digits in a value to the ambient locale's
 * digit set (Arabic-Indic ٠..٩ under `ar`), leaving every non-digit character
 * untouched — so decimals, separators, and units ("1.5×", "47/114", "3h 45m",
 * "192 kbps") localize in place. It's a no-op for every non-Arabic locale.
 *
 * The value is always correct at call time (it reads Paraglide's ambient locale
 * by default). Reactivity is the caller's job, exactly like `m.*()` messages:
 *   - runes: use the `fmtNum` sugar re-exported from `./locale.svelte.ts`, which
 *     reads the reactive `i18n.locale` so the number re-renders on switch;
 *   - legacy Svelte-4 `$:` blocks: `tr($localeStore, localizeDigits(n))`.
 * Pure `.ts` string builders (e.g. `lib/utils/delivery-label.ts`) call it with
 * the ambient default and inherit their consumer's locale reactivity.
 */
import { getLocale } from '$lib/paraglide/runtime';

import { toArabicNumeral } from '$lib/utils/arabic-text';

export function localizeDigits(value: string | number, locale: string = getLocale()): string {
    return ['ar', 'ckb'].includes(locale) ? toArabicNumeral(value) : String(value);
}
