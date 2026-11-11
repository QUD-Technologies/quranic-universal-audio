/**
 * A recitation's reading profile laid out for the readings panel.
 *
 * The server folds the shards into `TsReadingsDoc` rows (one per selector and
 * word, in mushaf order, every option with the verses read that way — see
 * `inspector/services/reference/readings.py`). `buildProfile` groups those rows
 * into groups → words, each word's options in its family's display order.
 * Rows of selectors the public catalogue does not show are dropped.
 */

import type { TsReadingRow, TsReadingVerse } from '../../../lib/types/generated/schemas';
import { familyOf, GROUP_ORDER, type ChoiceGroup } from '../domain/reading-choices';

export interface ProfileOption {
    option: string;
    verses: TsReadingVerse[];
}

export interface ProfileWord {
    key: string;
    selector: string;
    texts: string[];
    options: ProfileOption[];
}

export interface ProfileGroup {
    group: ChoiceGroup;
    words: ProfileWord[];
}

function profileWord(row: TsReadingRow): ProfileWord {
    const family = familyOf(row.selector)!;
    const verses = new Map(row.options.map((opt) => [opt.option, opt.verses ?? []]));
    const order = [...family.options, ...row.options.map((opt) => opt.option)];
    return {
        key: row.key,
        selector: row.selector,
        texts: row.texts,
        options: [...new Set(order)].map((option) => ({ option, verses: verses.get(option) ?? [] })),
    };
}

export function buildProfile(rows: TsReadingRow[]): ProfileGroup[] {
    const shown = rows.filter((row) => familyOf(row.selector));
    return GROUP_ORDER.map((group) => ({
        group,
        words: shown.filter((row) => familyOf(row.selector)!.group === group).map(profileWord),
    })).filter((group) => group.words.length > 0);
}
