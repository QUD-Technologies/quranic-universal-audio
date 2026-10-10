/**
 * Read-only variant controls for one native reading (shard v15).
 *
 * Maps a reading's `variants` and the chapter's definitions onto the cells
 * renderer's `VariantControls`: the numbers show the face this recitation was
 * read with, hovering one spotlights the cells another face would change, and
 * nothing is selectable. How a face was picked stays out of this public view.
 * `variantTipLines` is the host tooltip for a hovered number.
 */

import {
    optionName,
    type VariantAffectedCells,
    type VariantControlDefinition,
    type VariantControlOccurrence,
    type VariantControls,
} from '@quranic-phonemizer/cells';

import * as m from '$lib/paraglide/messages';
import type { TsReadingVariant, TsVariantCells, TsVariantDefinition } from '../../../lib/types/generated/schemas';
import type { TsShardReading } from '../../../lib/types/ts-client';

export type VariantCatalogue = Record<string, TsVariantDefinition>;

type HoverHandler = NonNullable<VariantControls['onhover']>;

const cellsOf = (cells: TsVariantCells): VariantAffectedCells => ({
    column_ids: cells.c,
    sound_ids: cells.s,
    boundary_ids: cells.b,
});

function definitionOf(id: string, spec: TsVariantDefinition): VariantControlDefinition {
    return {
        id,
        display_name: spec.name,
        options: [...spec.options],
        default: spec.default,
        ...(spec.description ? { description: spec.description } : {}),
    };
}

function occurrenceOf(variant: TsReadingVariant): VariantControlOccurrence {
    return {
        variant_id: variant.id,
        selected: variant.chosen,
        word_ids: [...variant.words],
        target_word_ids: [...variant.targets],
        anchor: variant.anchor,
        anchor_word_id: variant.words[0],
        anchor_boundary_id: variant.boundary,
        active: true,
        masked: false,
        affected: Object.fromEntries(
            Object.entries(variant.affected).map(([option, cells]) => [option, cellsOf(cells)]),
        ),
    };
}

/**
 * The renderer's read-only controls for `reading`, or undefined for a reading
 * without variants (the row then renders exactly as before v15).
 */
export function variantControlsFor(
    catalogue: VariantCatalogue | undefined,
    reading: Pick<TsShardReading, 'variants'>,
    onhover?: HoverHandler,
): VariantControls | undefined {
    const shown = (reading.variants ?? []).filter((variant) => catalogue?.[variant.id]);
    if (!catalogue || !shown.length) return undefined;
    const ids = [...new Set(shown.map((variant) => variant.id))];
    return {
        definitions: Object.fromEntries(ids.map((id) => [id, definitionOf(id, catalogue[id]!)])),
        occurrences: shown.map(occurrenceOf),
        readOnly: true,
        ...(onhover ? { onhover } : {}),
    };
}

/** Tooltip lines for a hovered number: face, whose reading it is, scope. */
export function variantTipLines(
    definition: VariantControlDefinition,
    occurrence: VariantControlOccurrence,
    option: string,
): string[] {
    const chosen = option === occurrence.selected;
    return [
        `${definition.display_name}: ${optionName(option)}`,
        chosen ? m.ts_variant_this_recitation() : m.ts_variant_other_reading(),
        definition.description,
    ].filter((line): line is string => Boolean(line));
}
