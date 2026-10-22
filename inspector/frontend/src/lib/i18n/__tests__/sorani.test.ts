import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';

import { getOverviewDoc } from '$lib/components/info/overview';
import { i18n } from '$lib/i18n/locale.svelte';
import { localizeDigits } from '$lib/i18n/format';
import { getLocale } from '$lib/paraglide/runtime';
import { getMarkReadyCopy } from '../../../tabs/segments/copy/mark-ready';
import { formatHistDate } from '../../../tabs/segments/utils/history/items';
import { parseGuideSource } from '../../../tabs/segments/guides/parser';
import { ALL_GUIDE_KEYS, getAccordionGuide } from '../../../tabs/segments/guides/registry';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../');
const settings = JSON.parse(readFileSync(join(root, 'project.inlang/settings.json'), 'utf8'));

// Validate the consumer-facing inputs rather than copying the translation text
// into a test. Paraglide accepts missing inputs and silently falls back on gaps.
function inputs(value: unknown): string[] {
    const text = typeof value === 'string'
        ? value
        : (value as { match: Record<string, string> }[]).flatMap((v) => Object.values(v.match)).join(' ');
    return [...new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]!))].sort();
}

describe('Sorani localisation coverage', () => {
    for (const pattern of settings['plugin.inlang.messageFormat'].pathPattern as string[]) {
        it(`${pattern} preserves all message inputs`, () => {
            const read = (locale: string) => JSON.parse(readFileSync(join(root, pattern.replace('{locale}', locale)), 'utf8'));
            const en = read('en');
            const ckb = read('ckb');
            for (const key of Object.keys(en).filter((key) => !key.startsWith('$'))) {
                expect(ckb[key], key).toBeDefined();
                expect(inputs(ckb[key]), key).toEqual(inputs(en[key]));
            }
        });
    }

    for (const category of [...ALL_GUIDE_KEYS, 'low_confidence_v2']) {
        it(`loads the Sorani ${category} guide with unchanged examples/components`, () => {
            const en = getAccordionGuide(category, 'en');
            const ckb = getAccordionGuide(category, 'ckb');
            expect(ckb).toBeTruthy();
            expect(ckb).not.toBe(en);
            const directives = (source: string) => parseGuideSource(source).filter((block) =>
                block.type === 'example' || block.type === 'component' || block.type === 'missing');
            expect(directives(ckb!)).toEqual(directives(en!));
            expect(ckb).toMatch(/[ڕڵۆێە]/u);
        });
    }

    it('loads the full Sorani About document and preserves the lifecycle', () => {
        const en = getOverviewDoc('en');
        const ckb = getOverviewDoc('ckb');
        expect(ckb).not.toBe(en);
        expect(ckb.title).toContain('دەربارەی');
        expect(ckb.blocks.map((b) => b.type)).toEqual(en.blocks.map((b) => b.type));
        const states = (doc: typeof en) => doc.blocks.flatMap((b) => b.type === 'lifecycle' ? b.rows.map((r) => r.state) : []);
        expect(states(ckb)).toEqual(states(en));
    });

    it('loads all mark-ready sections instead of falling back to English', () => {
        const en = getMarkReadyCopy('en');
        const ckb = getMarkReadyCopy('ckb');
        expect(ckb).not.toBe(en);
        expect(ckb.checklist.map((item) => item.key)).toEqual(en.checklist.map((item) => item.key));
        expect(ckb.form.submit).toBe('ئامادە نیشانکردن');
        expect(ckb.comments.checks.placeholder).not.toBe(en.comments.checks.placeholder);
        expect(ckb.comments.issues.placeholder).not.toBe(en.comments.issues.placeholder);
    });
});

describe('Sorani locale switching', () => {
    afterEach(() => i18n.setMode('auto'));

    it('cycles through Sorani, pins it, and applies RTL without reloading', () => {
        i18n.set('ar');
        i18n.cycle();
        expect(i18n.locale).toBe('ckb');
        expect(getLocale()).toBe('ckb');
        expect(document.documentElement.lang).toBe('ckb');
        expect(document.documentElement.dir).toBe('rtl');
        expect(localStorage.getItem('insp_locale')).toBe('ckb');
        i18n.cycle();
        expect(i18n.mode).toBe('auto');
        expect(localStorage.getItem('insp_locale')).toBeNull();
        i18n.set('en');
        expect(document.documentElement.dir).toBe('ltr');
    });

    it('formats history dates in the selected language', () => {
        const iso = '2026-10-22T03:05:00Z';
        const date = new Date(iso);
        i18n.set('ckb');
        expect(formatHistDate(iso)).toBe(
            date.toLocaleDateString('ckb', { month: 'short', day: 'numeric' }) + ' ' +
            date.toLocaleTimeString('ckb', { hour: '2-digit', minute: '2-digit' }),
        );
        expect(formatHistDate(null)).toBe('چاوەڕوان');
    });

    it('localizes display digits without changing separators or units', () => {
        expect(localizeDigits('47/114 · 1.5×', 'ckb')).toBe('٤٧/١١٤ · ١.٥×');
        expect(localizeDigits('47/114 · 1.5×', 'en')).toBe('47/114 · 1.5×');
    });
});
