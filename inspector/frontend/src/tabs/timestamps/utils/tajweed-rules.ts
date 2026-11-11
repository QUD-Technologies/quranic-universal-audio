/** Inspector presentation policy over the producer's 45 native rule IDs. */

import type { RuleDefinition, RuleVisual, StackLayer } from '@quranic-phonemizer/cells';
import * as m from '$lib/paraglide/messages';
import { i18n } from '$lib/i18n/locale.svelte';
import catalogue from '../data/rules.json';

export type RuleCategory = 'noon_meem' | 'madd' | 'other';

interface Metadata {
    id: string;
    name: string;
    arabic_name: string;
    summary: string;
}

interface Policy {
    legendKey: string;
    colorVar: string | null;
    stack: StackLayer;
    visual?: RuleVisual;
}

const metadata = new Map((catalogue as Metadata[]).map((rule) => [rule.id, rule]));
const colored = {
    ghunnah_mushaddadah: ['ghunnah', '--tj-ghunnah', 'base'],
    ikhfaa: ['ikhfaa', '--tj-ikhfaa', 'base'],
    iqlab: ['iqlab', '--tj-iqlab', 'base'],
    idgham_bi_ghunnah: ['idgham_ghunnah', '--tj-idgham-ghunnah', 'base'],
    idgham_bila_ghunnah: ['idgham_bila', '--tj-idgham-bila', 'base'],
    izhar: ['izhar', '--tj-izhar-halqi', 'base'],
    ikhfaa_shafawi: ['ikhfaa_shafawi', '--tj-ikhfaa-shafawi', 'base'],
    idgham_shafawi: ['idgham_shafawi', '--tj-idgham-shafawi', 'base'],
    izhar_shafawi: ['izhar_shafawi', '--tj-izhar-shafawi', 'base'],
    madd_lazim: ['madd_lazim', '--tj-madd-lazim', 'base'],
    madd_muttasil: ['madd_wajib', '--tj-madd-wajib', 'base'],
    madd_munfasil: ['madd_jaiz', '--tj-madd-jaiz', 'base'],
    madd_arid_lissukun: ['madd_arid', '--tj-madd-arid', 'base'],
    madd_leen: ['madd_leen', '--tj-madd-leen', 'base'],
    madd_tabii: ['madd_tabii', '--tj-madd-tabii', 'base'],
    tafkheem: ['tafkheem', '--tj-tafkheem', 'top'],
    qalqala_sughra: ['qalqala', '--tj-qalqala', 'base'],
    qalqala_kubra: ['qalqala', '--tj-qalqala', 'base', 'wrap'],
    qalqala_akbar: ['qalqala', '--tj-qalqala', 'base', 'wrap'],
    idgham_mutamathilayn: ['mutamathilayn', '--tj-mutamathilayn', 'merge'],
    idgham_mutaqaribayn: ['mutaqaribayn', '--tj-mutaqaribayn', 'merge'],
    idgham_mutajanisayn_kamil: ['mutajanisayn', '--tj-mutajanisayn', 'merge'],
    idgham_mutajanisayn_naqis: ['mutajanisayn', '--tj-mutajanisayn', 'merge'],
    imala: ['special', '--tj-special', 'base', 'border'],
    tashil: ['special', '--tj-special', 'base', 'border'],
    ishmam: ['special', '--tj-special', 'base', 'border'],
    ibdal_hamza: ['special', '--tj-special', 'base', 'border'],
} as const;

const hoverOnly = new Set([
    'madd_iwad', 'madd_badal', 'madd_silah', 'tarqeeq',
    'lam_shamsiyyah', 'lam_qamariyyah',
    'hamza_wasl_silent', 'hamza_wasl_fatha', 'hamza_wasl_kasra', 'hamza_wasl_damma',
    'iltiqa_haraka', 'iltiqa_shortening',
    'waqf_diacritic_drop', 'waqf_silah_drop', 'waqf_taa_marbuta',
    'pausal_alif', 'orthographic_silence', 'variant_silence',
]);

function policyOf(id: string): Policy {
    const row = (colored as Record<string, readonly string[]>)[id];
    if (row) return {
        legendKey: row[0]!,
        colorVar: row[1]!,
        stack: row[2] as StackLayer,
        visual: row[3] as RuleVisual | undefined,
    };
    if (hoverOnly.has(id)) return { legendKey: '', colorVar: null, stack: 'base' };
    throw new Error(`Unclassified producer rule: ${id}`);
}

export function ruleLabel(id: string): string {
    const rule = metadata.get(id);
    if (!rule) throw new Error(`Unknown producer rule: ${id}`);
    return ['ar', 'ckb'].includes(i18n.locale) ? rule.arabic_name : rule.name;
}

export function defineInspectorRule(id: string): RuleDefinition {
    if (!metadata.has(id)) throw new Error(`Unknown producer rule: ${id}`);
    const policy = policyOf(id);
    return {
        id,
        label: ruleLabel(id),
        legend_key: policy.legendKey,
        color_var: policy.colorVar,
        stack: policy.stack,
        visual: policy.visual,
    };
}

for (const id of metadata.keys()) policyOf(id);
if (metadata.size !== 45) throw new Error(`Expected 45 producer rules, got ${metadata.size}`);

export interface TjBadge {
    legendKey: string;
    colorVar: string;
    tooltip: () => string;
    stack: StackLayer;
    kubra: boolean;
}

export function badgeForTag(id: string | null | undefined): TjBadge | null {
    if (!id) return null;
    const policy = policyOf(id);
    if (!policy.colorVar) return null;
    return {
        legendKey: policy.legendKey,
        colorVar: policy.colorVar,
        tooltip: () => ruleLabel(id),
        stack: policy.stack,
        kubra: id === 'qalqala_kubra' || id === 'qalqala_akbar',
    };
}

export const silentTooltip = (id: string | null | undefined): string | null =>
    id && metadata.has(id) && !policyOf(id).colorVar ? ruleLabel(id) : null;

export const ruleHasLabel = (id: string | null | undefined): boolean =>
    Boolean(id && metadata.has(id));

export const tagsForLegend = (legendKey: string): Set<string> => new Set(
    [...metadata.keys()].filter((id) => policyOf(id).legendKey === legendKey),
);

export interface LegendRow {
    legendKey: string;
    label: () => string;
    colorVar: string;
    duration?: string;
    kubra?: boolean;
    border?: boolean;
}

export interface LegendSubgroup {
    title: () => string;
    rows: LegendRow[];
}

export interface LegendGroup {
    category: RuleCategory;
    title: () => string;
    rows?: LegendRow[];
    subgroups?: LegendSubgroup[];
}

const row = (
    id: string,
    duration?: string,
    extra: Pick<LegendRow, 'kubra' | 'border'> = {},
): LegendRow => {
    const policy = policyOf(id);
    if (!policy.colorVar) throw new Error(`${id} has no legend colour`);
    return {
        legendKey: policy.legendKey,
        label: () => ruleLabel(id),
        colorVar: policy.colorVar,
        duration,
        ...extra,
    };
};

export const LEGEND: LegendGroup[] = [
    {
        category: 'noon_meem',
        title: m.ts_tajweed_panel_group_title_noon_meem,
        subgroups: [
            {
                title: m.ts_tajweed_panel_group_title_noon,
                rows: [
                    row('ghunnah_mushaddadah', '2'), row('ikhfaa', '2'), row('iqlab', '2'),
                    row('idgham_bi_ghunnah', '2'), row('idgham_bila_ghunnah', '1'),
                    row('izhar', '1'),
                ],
            },
            {
                title: m.ts_tajweed_panel_group_title_meem,
                rows: [
                    row('ghunnah_mushaddadah', '2'), row('ikhfaa_shafawi', '2'),
                    row('idgham_shafawi', '2'), row('izhar_shafawi', '1'),
                ],
            },
        ],
    },
    {
        category: 'madd',
        title: m.ts_tajweed_panel_group_title_madd,
        rows: [
            row('madd_lazim', '6'), row('madd_muttasil', '4/5'),
            row('madd_munfasil', '2/4/5'), row('madd_arid_lissukun', '2/4/6'),
            row('madd_leen', '2/4/6'), row('madd_tabii', '2'),
        ],
    },
    {
        category: 'other',
        title: m.ts_tajweed_panel_group_title_other,
        rows: [
            row('tafkheem'), row('qalqala_sughra'), row('qalqala_kubra', undefined, { kubra: true }),
            row('idgham_mutamathilayn'), row('idgham_mutaqaribayn'),
            {
                ...row('idgham_mutajanisayn_kamil'),
                label: m.ts_tajweed_rule_idgham_mutajanisayn,
            },
            {
                ...row('imala', undefined, { border: true }),
                label: m.ts_tajweed_rule_special,
            },
        ],
    },
];

export const legendRows = (group: LegendGroup): LegendRow[] =>
    group.rows ?? group.subgroups?.flatMap((subgroup) => subgroup.rows) ?? [];

export const LEGEND_KEYS = [
    ...new Set(LEGEND.flatMap((group) => legendRows(group).map((one) => one.legendKey))),
];

const defaultOff = new Set(['izhar', 'izhar_shafawi', 'madd_tabii']);
export const DEFAULT_ENABLED = Object.fromEntries(
    LEGEND_KEYS.map((key) => [key, !defaultOff.has(key)]),
);

export function tajweedColorVar(id: string | null | undefined): string | null {
    if (!id || !metadata.has(id)) return null;
    const color = policyOf(id).colorVar;
    return color ? `var(${color})` : null;
}
