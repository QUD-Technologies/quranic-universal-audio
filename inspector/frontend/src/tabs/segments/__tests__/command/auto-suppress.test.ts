// Auto-suppress decoupling — edits MUST NOT mutate ignored_categories.
//
// `ignored_categories` reflects only explicit Ignore actions. Editing from
// a validation accordion card records the source category on the op
// (op_context_category, for history pills) but never writes to the
// segment's persisted ignore list. Card dismissal for soft-rule
// categories is handled out-of-band via the session-resolved store.

import { describe, expect, it } from 'vitest';

import { applyCommand } from '../../domain/apply-command';
import type { EditReferenceCommand, TrimCommand } from '../../domain/command';
import { CAN_IGNORE_CATEGORIES } from '../helpers/categories';
import { makeApplyCommandState, makeSegment } from '../helpers/make-segment';

const baseState = () => makeApplyCommandState([
  makeSegment(0, 0, 1000, { segment_uid: 'uid-as' }),
]);

describe('command/auto-suppress (decoupled)', () => {
  for (const cat of CAN_IGNORE_CATEGORIES) {
    it(`edit dispatched with sourceCategory='${cat}' does NOT add ${cat} to seg.ignored_categories`, () => {
      const cmd: EditReferenceCommand = {
        type: 'editReference',
        segmentUid: 'uid-as',
        matched_ref: '1:1:1-1:1:1',
        matched_text: 'x',
        sourceCategory: cat,
      };
      const r = applyCommand(baseState(), cmd);
      const updated = r.nextState.byId['uid-as']!;
      expect(updated.ignored_categories ?? []).not.toContain(cat);
    });
  }

  it('op records sourceCategory in op_context_category for history audit', () => {
    const cmd: EditReferenceCommand = {
      type: 'editReference',
      segmentUid: 'uid-as',
      matched_ref: '1:1:1-1:1:1',
      matched_text: 'x',
      sourceCategory: 'repetitions',
    };
    const r = applyCommand(baseState(), cmd);
    expect(r.operation.op_context_category).toBe('repetitions');
  });

  it('trim with sourceCategory does not mutate ignored_categories', () => {
    const cmd: TrimCommand = {
      type: 'trim',
      segmentUid: 'uid-as',
      delta: { time_start: 100, time_end: 900 },
      sourceCategory: 'audio_bleeding',
    };
    const r = applyCommand(baseState(), cmd);
    const updated = r.nextState.byId['uid-as']!;
    expect(updated.ignored_categories ?? []).not.toContain('audio_bleeding');
  });

  it('main-list edit (no sourceCategory) does not mutate ignored_categories', () => {
    const cmd: TrimCommand = {
      type: 'trim',
      segmentUid: 'uid-as',
      delta: { time_start: 100, time_end: 900 },
    };
    const r = applyCommand(baseState(), cmd);
    const updated = r.nextState.byId['uid-as']!;
    expect(updated.ignored_categories ?? []).toEqual([]);
  });

  it('preserves pre-existing ignored_categories on the segment', () => {
    const state = makeApplyCommandState([
      makeSegment(0, 0, 1000, {
        segment_uid: 'uid-as',
        ignored_categories: ['low_confidence'],
      }),
    ]);
    const cmd: TrimCommand = {
      type: 'trim',
      segmentUid: 'uid-as',
      delta: { time_start: 100, time_end: 900 },
      sourceCategory: 'repetitions',
    };
    const r = applyCommand(state, cmd);
    const updated = r.nextState.byId['uid-as']!;
    expect(updated.ignored_categories).toEqual(['low_confidence']);
  });
});
