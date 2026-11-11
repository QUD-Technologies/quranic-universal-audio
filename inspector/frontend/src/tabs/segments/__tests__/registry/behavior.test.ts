// Parametrized behavioral tests over ALL_CATEGORIES.

import { describe, expect, it } from 'vitest';

import { applyCommand } from '../../domain/apply-command';
import type { EditReferenceCommand } from '../../domain/command';
import { IssueRegistry } from '../../domain/registry';
import { ALL_CATEGORIES, AUTO_SUPPRESS_CATEGORIES, CAN_IGNORE_CATEGORIES } from '../helpers/categories';
import { makeSegment } from '../helpers/make-segment';

describe('registry behavior — parametrized', () => {
  for (const cat of ALL_CATEGORIES) {
    it(`Ignore button visible iff registry.canIgnore (${cat})`, () => {
      const row = IssueRegistry[cat]!;
      const wantsButton = !!row.canIgnore;
      const isInCanIgnoreList = CAN_IGNORE_CATEGORIES.includes(cat as any);
      expect(wantsButton).toBe(isInCanIgnoreList);
    });

    it(`auto_suppress flag is read from registry (${cat})`, () => {
      const row = IssueRegistry[cat]!;
      const wants = !!row.autoSuppress;
      const expected = AUTO_SUPPRESS_CATEGORIES.includes(cat as any);
      expect(wants).toBe(expected);
    });

    it(`edit through applyCommand never mutates ignored_categories (${cat})`, () => {
      const seg = makeSegment(0, 0, 1000, { segment_uid: `uid-${cat}` });
      const cmd: EditReferenceCommand = {
        type: 'editReference',
        segmentUid: `uid-${cat}`,
        matched_ref: '1:1:1-1:1:1',
        matched_text: 'x',
        sourceCategory: cat,
      };
      const result = applyCommand(
        { byId: { [`uid-${cat}`]: seg }, idsByChapter: { 1: [`uid-${cat}`] }, selectedChapter: 1 } as any,
        cmd,
      );
      expect(result.operation).toBeTruthy();
      // muqattaat is neutral (info-only); every other category persists its
      // sourceCategory as op_context_category.
      const isNeutral = cat === 'muqattaat';
      expect(result.operation.op_context_category).toBe(isNeutral ? null : cat);
      const updated = result.nextState.byId[`uid-${cat}`];
      expect(updated?.ignored_categories ?? []).not.toContain(cat);
    });

    it(`card type dispatched from registry (${cat})`, () => {
      const row = IssueRegistry[cat]!;
      expect(['generic', 'missingVerses', 'missingWords', 'error']).toContain(row.cardType);
    });
  }

  it('accordion order matches registry', () => {
    const orders = ALL_CATEGORIES.map((c) => IssueRegistry[c]!.accordionOrder).sort((a, b) => a - b);
    const expected = Array.from({ length: ALL_CATEGORIES.length }, (_, i) => i + 1);
    expect(orders).toEqual(expected);
  });
});
