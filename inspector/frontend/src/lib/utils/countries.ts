/**
 * ISO-3166-1 alpha-2 country list.
 *
 * Embedded at build time so the request form's country picker works without
 * a network round-trip. ~250 entries; ~12KB raw. Names are common English
 * short-names; codes are the uppercased ISO alpha-2 stored in the catalog.
 *
 * Use the `<input list="...">` + `<datalist>` pattern from RequestForm.svelte
 * for free searchable autocomplete — typing either the code or the name
 * narrows the dropdown.
 */

export interface Country {
    code: string;   // ISO-3166-1 alpha-2 (uppercase)
    name: string;
}

export const COUNTRIES: readonly Country[] = [
    { code: 'AD', name: 'Andorra' },
    { code: 'AE', name: 'United Arab Emirates' },
    { code: 'AF', name: 'Afghanistan' },
    { code: 'AG', name: 'Antigua and Barbuda' },
    { code: 'AI', name: 'Anguilla' },
    { code: 'AL', name: 'Albania' },
    { code: 'AM', name: 'Armenia' },
    { code: 'AO', name: 'Angola' },
    { code: 'AQ', name: 'Antarctica' },
    { code: 'AR', name: 'Argentina' },
    { code: 'AS', name: 'American Samoa' },
    { code: 'AT', name: 'Austria' },
    { code: 'AU', name: 'Australia' },
    { code: 'AW', name: 'Aruba' },
    { code: 'AX', name: 'Åland Islands' },
    { code: 'AZ', name: 'Azerbaijan' },
    { code: 'BA', name: 'Bosnia and Herzegovina' },
    { code: 'BB', name: 'Barbados' },
    { code: 'BD', name: 'Bangladesh' },
    { code: 'BE', name: 'Belgium' },
    { code: 'BF', name: 'Burkina Faso' },
    { code: 'BG', name: 'Bulgaria' },
    { code: 'BH', name: 'Bahrain' },
    { code: 'BI', name: 'Burundi' },
    { code: 'BJ', name: 'Benin' },
    { code: 'BL', name: 'Saint Barthélemy' },
    { code: 'BM', name: 'Bermuda' },
    { code: 'BN', name: 'Brunei' },
    { code: 'BO', name: 'Bolivia' },
    { code: 'BQ', name: 'Caribbean Netherlands' },
    { code: 'BR', name: 'Brazil' },
    { code: 'BS', name: 'Bahamas' },
    { code: 'BT', name: 'Bhutan' },
    { code: 'BV', name: 'Bouvet Island' },
    { code: 'BW', name: 'Botswana' },
    { code: 'BY', name: 'Belarus' },
    { code: 'BZ', name: 'Belize' },
    { code: 'CA', name: 'Canada' },
    { code: 'CC', name: 'Cocos (Keeling) Islands' },
    { code: 'CD', name: 'Congo (Kinshasa)' },
    { code: 'CF', name: 'Central African Republic' },
    { code: 'CG', name: 'Congo (Brazzaville)' },
    { code: 'CH', name: 'Switzerland' },
    { code: 'CI', name: 'Côte d’Ivoire' },
    { code: 'CK', name: 'Cook Islands' },
    { code: 'CL', name: 'Chile' },
    { code: 'CM', name: 'Cameroon' },
    { code: 'CN', name: 'China' },
    { code: 'CO', name: 'Colombia' },
    { code: 'CR', name: 'Costa Rica' },
    { code: 'CU', name: 'Cuba' },
    { code: 'CV', name: 'Cape Verde' },
    { code: 'CW', name: 'Curaçao' },
    { code: 'CX', name: 'Christmas Island' },
    { code: 'CY', name: 'Cyprus' },
    { code: 'CZ', name: 'Czechia' },
    { code: 'DE', name: 'Germany' },
    { code: 'DJ', name: 'Djibouti' },
    { code: 'DK', name: 'Denmark' },
    { code: 'DM', name: 'Dominica' },
    { code: 'DO', name: 'Dominican Republic' },
    { code: 'DZ', name: 'Algeria' },
    { code: 'EC', name: 'Ecuador' },
    { code: 'EE', name: 'Estonia' },
    { code: 'EG', name: 'Egypt' },
    { code: 'EH', name: 'Western Sahara' },
    { code: 'ER', name: 'Eritrea' },
    { code: 'ES', name: 'Spain' },
    { code: 'ET', name: 'Ethiopia' },
    { code: 'FI', name: 'Finland' },
    { code: 'FJ', name: 'Fiji' },
    { code: 'FK', name: 'Falkland Islands' },
    { code: 'FM', name: 'Micronesia' },
    { code: 'FO', name: 'Faroe Islands' },
    { code: 'FR', name: 'France' },
    { code: 'GA', name: 'Gabon' },
    { code: 'GB', name: 'United Kingdom' },
    { code: 'GD', name: 'Grenada' },
    { code: 'GE', name: 'Georgia' },
    { code: 'GF', name: 'French Guiana' },
    { code: 'GG', name: 'Guernsey' },
    { code: 'GH', name: 'Ghana' },
    { code: 'GI', name: 'Gibraltar' },
    { code: 'GL', name: 'Greenland' },
    { code: 'GM', name: 'Gambia' },
    { code: 'GN', name: 'Guinea' },
    { code: 'GP', name: 'Guadeloupe' },
    { code: 'GQ', name: 'Equatorial Guinea' },
    { code: 'GR', name: 'Greece' },
    { code: 'GS', name: 'South Georgia and South Sandwich Islands' },
    { code: 'GT', name: 'Guatemala' },
    { code: 'GU', name: 'Guam' },
    { code: 'GW', name: 'Guinea-Bissau' },
    { code: 'GY', name: 'Guyana' },
    { code: 'HK', name: 'Hong Kong' },
    { code: 'HM', name: 'Heard Island and McDonald Islands' },
    { code: 'HN', name: 'Honduras' },
    { code: 'HR', name: 'Croatia' },
    { code: 'HT', name: 'Haiti' },
    { code: 'HU', name: 'Hungary' },
    { code: 'ID', name: 'Indonesia' },
    { code: 'IE', name: 'Ireland' },
    { code: 'IM', name: 'Isle of Man' },
    { code: 'IN', name: 'India' },
    { code: 'IO', name: 'British Indian Ocean Territory' },
    { code: 'IQ', name: 'Iraq' },
    { code: 'IR', name: 'Iran' },
    { code: 'IS', name: 'Iceland' },
    { code: 'IT', name: 'Italy' },
    { code: 'JE', name: 'Jersey' },
    { code: 'JM', name: 'Jamaica' },
    { code: 'JO', name: 'Jordan' },
    { code: 'JP', name: 'Japan' },
    { code: 'KE', name: 'Kenya' },
    { code: 'KG', name: 'Kyrgyzstan' },
    { code: 'KH', name: 'Cambodia' },
    { code: 'KI', name: 'Kiribati' },
    { code: 'KM', name: 'Comoros' },
    { code: 'KN', name: 'Saint Kitts and Nevis' },
    { code: 'KP', name: 'North Korea' },
    { code: 'KR', name: 'South Korea' },
    { code: 'KW', name: 'Kuwait' },
    { code: 'KY', name: 'Cayman Islands' },
    { code: 'KZ', name: 'Kazakhstan' },
    { code: 'LA', name: 'Laos' },
    { code: 'LB', name: 'Lebanon' },
    { code: 'LC', name: 'Saint Lucia' },
    { code: 'LI', name: 'Liechtenstein' },
    { code: 'LK', name: 'Sri Lanka' },
    { code: 'LR', name: 'Liberia' },
    { code: 'LS', name: 'Lesotho' },
    { code: 'LT', name: 'Lithuania' },
    { code: 'LU', name: 'Luxembourg' },
    { code: 'LV', name: 'Latvia' },
    { code: 'LY', name: 'Libya' },
    { code: 'MA', name: 'Morocco' },
    { code: 'MC', name: 'Monaco' },
    { code: 'MD', name: 'Moldova' },
    { code: 'ME', name: 'Montenegro' },
    { code: 'MF', name: 'Saint Martin (French)' },
    { code: 'MG', name: 'Madagascar' },
    { code: 'MH', name: 'Marshall Islands' },
    { code: 'MK', name: 'North Macedonia' },
    { code: 'ML', name: 'Mali' },
    { code: 'MM', name: 'Myanmar' },
    { code: 'MN', name: 'Mongolia' },
    { code: 'MO', name: 'Macao' },
    { code: 'MP', name: 'Northern Mariana Islands' },
    { code: 'MQ', name: 'Martinique' },
    { code: 'MR', name: 'Mauritania' },
    { code: 'MS', name: 'Montserrat' },
    { code: 'MT', name: 'Malta' },
    { code: 'MU', name: 'Mauritius' },
    { code: 'MV', name: 'Maldives' },
    { code: 'MW', name: 'Malawi' },
    { code: 'MX', name: 'Mexico' },
    { code: 'MY', name: 'Malaysia' },
    { code: 'MZ', name: 'Mozambique' },
    { code: 'NA', name: 'Namibia' },
    { code: 'NC', name: 'New Caledonia' },
    { code: 'NE', name: 'Niger' },
    { code: 'NF', name: 'Norfolk Island' },
    { code: 'NG', name: 'Nigeria' },
    { code: 'NI', name: 'Nicaragua' },
    { code: 'NL', name: 'Netherlands' },
    { code: 'NO', name: 'Norway' },
    { code: 'NP', name: 'Nepal' },
    { code: 'NR', name: 'Nauru' },
    { code: 'NU', name: 'Niue' },
    { code: 'NZ', name: 'New Zealand' },
    { code: 'OM', name: 'Oman' },
    { code: 'PA', name: 'Panama' },
    { code: 'PE', name: 'Peru' },
    { code: 'PF', name: 'French Polynesia' },
    { code: 'PG', name: 'Papua New Guinea' },
    { code: 'PH', name: 'Philippines' },
    { code: 'PK', name: 'Pakistan' },
    { code: 'PL', name: 'Poland' },
    { code: 'PM', name: 'Saint Pierre and Miquelon' },
    { code: 'PN', name: 'Pitcairn Islands' },
    { code: 'PR', name: 'Puerto Rico' },
    { code: 'PS', name: 'Palestine' },
    { code: 'PT', name: 'Portugal' },
    { code: 'PW', name: 'Palau' },
    { code: 'PY', name: 'Paraguay' },
    { code: 'QA', name: 'Qatar' },
    { code: 'RE', name: 'Réunion' },
    { code: 'RO', name: 'Romania' },
    { code: 'RS', name: 'Serbia' },
    { code: 'RU', name: 'Russia' },
    { code: 'RW', name: 'Rwanda' },
    { code: 'SA', name: 'Saudi Arabia' },
    { code: 'SB', name: 'Solomon Islands' },
    { code: 'SC', name: 'Seychelles' },
    { code: 'SD', name: 'Sudan' },
    { code: 'SE', name: 'Sweden' },
    { code: 'SG', name: 'Singapore' },
    { code: 'SH', name: 'Saint Helena' },
    { code: 'SI', name: 'Slovenia' },
    { code: 'SJ', name: 'Svalbard and Jan Mayen' },
    { code: 'SK', name: 'Slovakia' },
    { code: 'SL', name: 'Sierra Leone' },
    { code: 'SM', name: 'San Marino' },
    { code: 'SN', name: 'Senegal' },
    { code: 'SO', name: 'Somalia' },
    { code: 'SR', name: 'Suriname' },
    { code: 'SS', name: 'South Sudan' },
    { code: 'ST', name: 'São Tomé and Príncipe' },
    { code: 'SV', name: 'El Salvador' },
    { code: 'SX', name: 'Sint Maarten' },
    { code: 'SY', name: 'Syria' },
    { code: 'SZ', name: 'Eswatini' },
    { code: 'TC', name: 'Turks and Caicos Islands' },
    { code: 'TD', name: 'Chad' },
    { code: 'TF', name: 'French Southern Territories' },
    { code: 'TG', name: 'Togo' },
    { code: 'TH', name: 'Thailand' },
    { code: 'TJ', name: 'Tajikistan' },
    { code: 'TK', name: 'Tokelau' },
    { code: 'TL', name: 'Timor-Leste' },
    { code: 'TM', name: 'Turkmenistan' },
    { code: 'TN', name: 'Tunisia' },
    { code: 'TO', name: 'Tonga' },
    { code: 'TR', name: 'Türkiye' },
    { code: 'TT', name: 'Trinidad and Tobago' },
    { code: 'TV', name: 'Tuvalu' },
    { code: 'TW', name: 'Taiwan' },
    { code: 'TZ', name: 'Tanzania' },
    { code: 'UA', name: 'Ukraine' },
    { code: 'UG', name: 'Uganda' },
    { code: 'UM', name: 'United States Minor Outlying Islands' },
    { code: 'US', name: 'United States' },
    { code: 'UY', name: 'Uruguay' },
    { code: 'UZ', name: 'Uzbekistan' },
    { code: 'VA', name: 'Vatican City' },
    { code: 'VC', name: 'Saint Vincent and the Grenadines' },
    { code: 'VE', name: 'Venezuela' },
    { code: 'VG', name: 'British Virgin Islands' },
    { code: 'VI', name: 'U.S. Virgin Islands' },
    { code: 'VN', name: 'Vietnam' },
    { code: 'VU', name: 'Vanuatu' },
    { code: 'WF', name: 'Wallis and Futuna' },
    { code: 'WS', name: 'Samoa' },
    { code: 'XK', name: 'Kosovo' },
    { code: 'YE', name: 'Yemen' },
    { code: 'YT', name: 'Mayotte' },
    { code: 'ZA', name: 'South Africa' },
    { code: 'ZM', name: 'Zambia' },
    { code: 'ZW', name: 'Zimbabwe' },
];

/** Lookup by ISO-2 code (case-insensitive). Returns null when unknown. */
export function countryByCode(code: string | null | undefined): Country | null {
    if (!code) return null;
    const upper = code.toUpperCase();
    return COUNTRIES.find((c) => c.code === upper) ?? null;
}


/**
 * Reverse index of localized (e.g. Arabic) display name → ISO-2 code, built
 * lazily per locale so a name typed/selected in the active locale still resolves
 * to its canonical code. English names live in `COUNTRIES`; other locales come
 * from `Intl.DisplayNames` via `countryName`.
 */
const _localizedIndex = new Map<string, Map<string, string>>();
function localizedNameIndex(locale: string): Map<string, string> {
    const cached = _localizedIndex.get(locale);
    if (cached) return cached;
    const map = new Map<string, string>();
    for (const c of COUNTRIES) {
        const label = countryName(c.code, locale);
        if (label) map.set(label.trim().toLowerCase(), c.code);
    }
    _localizedIndex.set(locale, map);
    return map;
}

/**
 * Country display name in `locale` (default English). Accepts the ISO-2 code
 * the catalog stores today AND the legacy full English name some rows still
 * carry ("Saudi Arabia") — `Intl.DisplayNames.of` throws on the latter, which
 * used to fall through to an upper-cased raw string ("SAUDI ARABIA").
 * Unresolvable input is returned trimmed, as-is. Instances cached per locale. */
const _countryDisplay = new Map<string, Intl.DisplayNames | null>();
function countryDisplayInstance(locale: string): Intl.DisplayNames | null {
    const cached = _countryDisplay.get(locale);
    if (cached !== undefined) return cached;
    let dn: Intl.DisplayNames | null;
    try {
        dn = new Intl.DisplayNames([locale], { type: 'region' });
    } catch {
        dn = null;
    }
    _countryDisplay.set(locale, dn);
    return dn;
}

export function countryName(raw: string | null | undefined, locale = 'en'): string {
    if (!raw) return '';
    const trimmed = raw.trim();
    if (!trimmed) return '';
    const known =
        countryByCode(trimmed) ?? COUNTRIES.find((c) => c.name.toLowerCase() === trimmed.toLowerCase()) ?? null;
    if (!known) return trimmed;
    const dn = countryDisplayInstance(locale);
    if (!dn) return known.name;
    try {
        return dn.of(known.code) ?? known.name;
    } catch {
        return known.name;
    }
}

/**
 * Lookup by country name (case-insensitive), matching the English short-name, the
 * localized name in `locale` (default Arabic when a non-en locale is given), OR the
 * ISO-2 code. Returns null when no match — so a value chosen or typed in the active
 * locale still resolves to its canonical entry.
 */
export function countryByName(name: string | null | undefined, locale?: string): Country | null {
    if (!name) return null;
    const trimmed = name.trim();
    const lower = trimmed.toLowerCase();
    if (!lower) return null;
    const byEn = COUNTRIES.find((c) => c.name.toLowerCase() === lower);
    if (byEn) return byEn;
    const byCode = countryByCode(trimmed);
    if (byCode) return byCode;
    for (const loc of locale && locale !== 'en' ? [locale] : ['ar']) {
        const code = localizedNameIndex(loc).get(lower);
        if (code) return countryByCode(code);
    }
    return null;
}

export interface CountryOption {
    code: string;
    /** Display label in the requested locale. */
    label: string;
}

/**
 * Filtered, localized country options for a combobox. Matches the query against
 * the ISO-2 code, the English short-name, and the localized name; each option's
 * `label` is the name in `locale`. An empty query returns the full list.
 */
export function filterCountries(query: string | null | undefined, locale = 'en'): CountryOption[] {
    const q = (query ?? '').trim().toLowerCase();
    return COUNTRIES.filter((c) => {
        if (!q) return true;
        const ar = locale !== 'en' ? countryName(c.code, locale).toLowerCase() : '';
        return (
            c.code.toLowerCase().includes(q) ||
            c.name.toLowerCase().includes(q) ||
            (!!ar && ar.includes(q))
        );
    }).map((c) => ({ code: c.code, label: countryName(c.code, locale) || c.name }));
}


/**
 * Normalize an arbitrary catalog ``country`` value to its ISO-2 code.
 *
 * Legacy catalog rows wrote the full country name (e.g. ``"Saudi Arabia"``)
 * instead of the ISO-2 code the schema documents (``"SA"``). This resolver
 * accepts either shape and returns the canonical ISO-2 code when it can —
 * the request form prefills with the resolved code so the dropdown
 * recognizes it, and on submit we always write ISO-2 back. The catalog
 * migrates naturally as edits land; no backfill required.
 *
 * Returns the original input (trimmed) when nothing matches, so unknown
 * strings still round-trip rather than getting silently dropped.
 */
export function normalizeCountry(raw: string | null | undefined): string {
    if (!raw) return '';
    const trimmed = raw.trim();
    if (!trimmed) return '';
    const byCode = countryByCode(trimmed);
    if (byCode) return byCode.code;
    const byName = countryByName(trimmed);
    if (byName) return byName.code;
    return trimmed;
}
