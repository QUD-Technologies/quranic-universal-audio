import { describe, expect, it } from 'vitest';

import { countryName } from '../countries';

describe('countryName', () => {
    it('renders ISO-2 codes in the requested locale', () => {
        expect(countryName('SA')).toBe('Saudi Arabia');
        expect(countryName('eg')).toBe('Egypt');
    });

    it('resolves legacy full-name rows instead of upper-casing them', () => {
        expect(countryName('Saudi Arabia')).toBe('Saudi Arabia');
        expect(countryName('Saudi Arabia', 'ar')).toBe(countryName('SA', 'ar'));
        expect(countryName('Saudi Arabia', 'ar')).not.toBe('SAUDI ARABIA');
    });

    it('returns unknown input trimmed and empty input as empty', () => {
        expect(countryName('  Atlantis ')).toBe('Atlantis');
        expect(countryName(null)).toBe('');
        expect(countryName('')).toBe('');
    });
});
