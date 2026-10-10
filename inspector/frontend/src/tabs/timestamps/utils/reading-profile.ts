/**
 * A recitation's reading profile: what it reads wherever Hafs allows a choice.
 *
 * `shardChoices` lifts the shown variant occurrences out of one raw v15 chapter
 * shard; `buildProfile` folds every chapter's occurrences into groups →
 * families → words → options, each option carrying the verses read that way.
 * A variant on a reading means its condition held there (stopped, joined or
 * started), so a waqf/wasl/ibtidaa selector appears only where it applied.
 * Picks made without evidence are left out: they say nothing about the reciter.
 */

import type { TsReadingVariant } from '../../../lib/types/generated/schemas';
import { CHOICES, FAMILIES, familyOf, GROUP_ORDER, type ChoiceGroup } from '../domain/reading-choices';

/** The slice of a raw shard the profile reads. */
export interface RawChoiceShard {
    _meta?: { schema_version?: number };
    readings?: Array<{
        render?: { w?: Array<[string, string, ...unknown[]]> };
        variants?: TsReadingVariant[] | null;
    }>;
}

/** One occurrence: a selector read with `option` over `wordRefs` (`s:v:w`). */
export interface ChoiceHit {
    selector: string;
    option: string;
    wordRefs: string[];
    texts: string[];
}

export interface VerseLink {
    surah: number;
    ayah: number;
    /** `s:v`, or `s:v1–v2` when the occurrence crosses a verse end. */
    label: string;
}

export interface ProfileOption {
    option: string;
    verses: VerseLink[];
}

export interface ProfileWord {
    key: string;
    texts: string[];
    options: ProfileOption[];
}

export interface ProfileFamily {
    family: string;
    words: ProfileWord[];
}

export interface ProfileGroup {
    group: ChoiceGroup;
    families: ProfileFamily[];
}

export const MIN_SCHEMA_VERSION = 15;

export function shardChoices(raw: RawChoiceShard): ChoiceHit[] {
    const hits: ChoiceHit[] = [];
    for (const reading of raw.readings ?? []) {
        const words = reading.render?.w ?? [];
        for (const variant of reading.variants ?? []) {
            if (!CHOICES[variant.id] || variant.by === 'default') continue;
            const picked = variant.words
                .map((id) => words[id])
                .filter((w): w is [string, string, ...unknown[]] => Boolean(w));
            if (picked.length !== variant.words.length) continue;
            hits.push({
                selector: variant.id,
                option: variant.chosen,
                wordRefs: picked.map((w) => w[0]),
                texts: picked.map((w) => w[1]),
            });
        }
    }
    return hits;
}

const refParts = (ref: string): number[] => ref.split(':').map(Number);

function compareRefs(a: string, b: string): number {
    const pa = refParts(a);
    const pb = refParts(b);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
        const d = (pa[i] ?? 0) - (pb[i] ?? 0);
        if (d) return d;
    }
    return 0;
}

function verseLink(wordRefs: string[]): VerseLink {
    const [surah, ayah] = refParts(wordRefs[0]!);
    const lastAyah = refParts(wordRefs[wordRefs.length - 1]!)[1]!;
    const label = lastAyah !== ayah ? `${surah}:${ayah}–${lastAyah}` : `${surah}:${ayah}`;
    return { surah: surah!, ayah: ayah!, label };
}

function wordKey(hit: ChoiceHit): string {
    const form = CHOICES[hit.selector]!.wordForm;
    return form ? `${hit.selector}/${form(hit.wordRefs[0]!)}` : hit.selector;
}

function foldWord(key: string, hits: ChoiceHit[]): ProfileWord {
    const selector = hits[0]!.selector;
    const options = new Map<string, Map<string, VerseLink>>(
        familyOf(selector)!.options.map((option) => [option, new Map()]),
    );
    for (const hit of hits) {
        const link = verseLink(hit.wordRefs);
        if (!options.has(hit.option)) options.set(hit.option, new Map());
        options.get(hit.option)!.set(link.label, link);
    }
    return {
        key,
        texts: hits[0]!.texts,
        options: [...options].map(([option, links]) => ({
            option,
            verses: [...links.values()].sort((a, b) => a.surah - b.surah || a.ayah - b.ayah),
        })),
    };
}

export function buildProfile(hits: ChoiceHit[]): ProfileGroup[] {
    const ordered = [...hits].sort((a, b) => compareRefs(a.wordRefs[0]!, b.wordRefs[0]!));
    const byWord = new Map<string, ChoiceHit[]>();
    for (const hit of ordered) {
        const key = wordKey(hit);
        byWord.set(key, [...(byWord.get(key) ?? []), hit]);
    }
    const byFamily = new Map<string, ProfileWord[]>();
    for (const [key, wordHits] of byWord) {
        const family = CHOICES[wordHits[0]!.selector]!.family;
        byFamily.set(family, [...(byFamily.get(family) ?? []), foldWord(key, wordHits)]);
    }
    return GROUP_ORDER.map((group) => ({
        group,
        families: [...byFamily]
            .filter(([family]) => FAMILIES[family]!.group === group)
            .map(([family, words]) => ({ family, words })),
    })).filter((group) => group.families.length > 0);
}
