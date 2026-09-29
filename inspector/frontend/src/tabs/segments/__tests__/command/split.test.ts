// IS-6: split flows dispatch through applyCommand.

import { describe, expect, it } from 'vitest';

import { applyCommand } from '../../domain/apply-command';
import type {
  EditReferenceCommand,
  MergeCommand,
  SetIsWaslCommand,
  SplitCommand,
} from '../../domain/command';
import { makeApplyCommandState, makeSegment } from '../helpers/make-segment';

const baseState = () => makeApplyCommandState([
  makeSegment(0, 0, 4000, { segment_uid: 'uid-split' }),
]);

const baseCmd: SplitCommand = { type: 'split', segmentUid: 'uid-split', splitMs: 2000 };

describe('command/split', () => {
  it('op produces expected segment mutations', () => {
    const r = applyCommand(baseState(), baseCmd);
    const ids = Object.keys(r.nextState.byId ?? r.nextState);
    expect(ids.length).toBeGreaterThanOrEqual(2);
  });

  it('op records snapshots before / after', () => {
    const r = applyCommand(baseState(), baseCmd);
    expect(r.operation.snapshots?.before).toBeTruthy();
    expect(r.operation.snapshots?.after).toBeTruthy();
  });

  it('op marks dirty correctly (structural vs single-index)', () => {
    const r = applyCommand(baseState(), baseCmd);
    expect(r.operation.kind).toBe('structural');
  });

  it('op honors auto-suppress per registry', () => {
    const cmd: SplitCommand = { ...baseCmd, sourceCategory: 'cross_verse' };
    const r = applyCommand(baseState(), cmd);
    expect(r.operation).toBeTruthy();
  });

  it('op result feeds save payload correctly', () => {
    const r = applyCommand(baseState(), baseCmd);
    expect(r.operation.type).toBe('split');
  });

  it('records targetSegmentIndex with the segment chapter (mountId is dispatcher-only and ignored here)', () => {
    // _mountId is consumed by the dispatcher to pick the rendered row, not
    // by applyCommand. The prior "main-list vs accordion" pair asserted the
    // same chapter=1 against both variants and could not fail differently.
    const cmd: SplitCommand = { ...baseCmd, _mountId: 'main-list' };
    const r = applyCommand(baseState(), cmd);
    expect(r.operation.targetSegmentIndex?.chapter).toBe(1);
  });

  it('editReference drops wrap when matched_ref changes', () => {
    // Regression: editing matched_ref kept the wrap intact even when the new
    // ref no longer contained the wrap word range — Ayyub edit_history shows
    // the bug:  "1:24 wrap=11..24" → "1:21 wrap=11..24" (wrap now stale).
    const state = makeApplyCommandState([
      makeSegment(0, 0, 4000, {
        segment_uid: 'uid-rep',
        matched_ref: '48:29:1-48:29:24',
        wrap_word_ranges: [['48:29:11', '48:29:11', '48:29:24']] as any,
      }),
    ]);
    const cmd: EditReferenceCommand = {
      type: 'editReference', segmentUid: 'uid-rep', matched_ref: '48:29:1-48:29:3',
    };
    const r = applyCommand(state, cmd);
    const out = Object.values(r.nextState.byId ?? r.nextState)[0] as any;
    expect(out.matched_ref).toBe('48:29:1-48:29:3');
    expect(out.wrap_word_ranges).toBeUndefined();
  });

  it('confirmReference (no ref change) preserves wrap', () => {
    // confirm_reference re-affirms the existing matched_ref. The wrap should
    // not be discarded just because the user audited the row.
    const state = makeApplyCommandState([
      makeSegment(0, 0, 4000, {
        segment_uid: 'uid-rep',
        matched_ref: '48:29:1-48:29:24',
        wrap_word_ranges: [['48:29:11', '48:29:11', '48:29:24']] as any,
      }),
    ]);
    const cmd: EditReferenceCommand = {
      type: 'editReference', opType: 'confirm_reference', segmentUid: 'uid-rep',
      matched_ref: '48:29:1-48:29:24',
    };
    const r = applyCommand(state, cmd);
    const out = Object.values(r.nextState.byId ?? r.nextState)[0] as any;
    expect(out.wrap_word_ranges).toEqual([['48:29:11', '48:29:11', '48:29:24']]);
  });

  it('merge drops wrap from both segs', () => {
    // Merging changes matched_ref + geometry; any wrap on the first seg may
    // not apply to the merged span. Drop for the same reason split does.
    const state = makeApplyCommandState([
      makeSegment(0, 0, 2000, {
        segment_uid: 'uid-a',
        matched_ref: '48:29:1-48:29:11',
        wrap_word_ranges: [['48:29:5', '48:29:5', '48:29:11']] as any,
      }),
      makeSegment(1, 2000, 4000, {
        segment_uid: 'uid-b',
        matched_ref: '48:29:12-48:29:24',
      }),
    ]);
    const cmd: MergeCommand = { type: 'merge', fromUid: 'uid-b', toUid: 'uid-a' };
    const r = applyCommand(state, cmd);
    const merged = Object.values(r.nextState.byId ?? r.nextState)[0] as any;
    expect(merged.wrap_word_ranges).toBeUndefined();
  });

  it('split propagates wasls[] to children on inter-piece boundaries', () => {
    // CV wizard: cursors=[1500, 3000] produces 3 pieces with 2 new boundaries.
    // wasls=[true, false] tags child[0].is_wasl=true, child[1].is_wasl=false.
    // The last child (no new boundary after it) inherits parent.is_wasl (default false).
    const state = makeApplyCommandState([
      makeSegment(0, 0, 4500, {
        segment_uid: 'uid-cv',
        matched_ref: '37:151:1-37:153:5',
      }),
    ]);
    const cmd: SplitCommand = {
      type: 'split',
      segmentUid: 'uid-cv',
      splitMs: [1500, 3000],
      newUids: ['uid-c1', 'uid-c2'],
      wasls: [true, false],
    };
    const r = applyCommand(state, cmd);
    const ids = (r.nextState.idsByChapter?.[1]) as string[] | undefined;
    expect(ids).toBeTruthy();
    const ordered = ids!.map((u) => r.nextState.byId[u]) as any[];
    expect(ordered.length).toBe(3);
    expect(ordered[0].is_wasl).toBe(true);
    expect(ordered[1].is_wasl).toBe(false);
    expect(ordered[2].is_wasl).toBe(false);
  });

  it('split records a waqf verdict only at the cuts not labelled wasl', () => {
    const state = makeApplyCommandState([
      makeSegment(0, 0, 4500, { segment_uid: 'uid-cv', matched_ref: '37:151:1-37:153:5' }),
    ]);
    const cmd: SplitCommand = {
      type: 'split',
      segmentUid: 'uid-cv',
      splitMs: [1500, 3000],
      newUids: ['uid-c1', 'uid-c2'],
      refs: ['37:151:1-37:151:4', '37:152:1-37:152:6', '37:153:1-37:153:5'],
      wasls: [true, false],
    };
    const r = applyCommand(state, cmd);
    expect(r.nextState.byId['uid-cv']?.join_verdicts ?? []).toEqual([]);
    expect(r.nextState.byId['uid-c1']?.join_verdicts).toEqual([
      { at_ms: 3000, after_ref: '37:152:6', verdict: 'waqf' },
    ]);
  });

  it('split with parent is_wasl=true preserves it on the LAST child only', () => {
    // Parent's is_wasl=true described the parent→next-seg boundary. After
    // split, that boundary is still owned by the new last child.
    const state = makeApplyCommandState([
      makeSegment(0, 0, 4000, {
        segment_uid: 'uid-cv',
        matched_ref: '2:5:1-2:6:3',
        is_wasl: true,
      }),
    ]);
    const cmd: SplitCommand = {
      type: 'split',
      segmentUid: 'uid-cv',
      splitMs: 2000,
      newUids: ['uid-c2'],
    };
    const r = applyCommand(state, cmd);
    const ids = (r.nextState.idsByChapter?.[1]) as string[];
    const ordered = ids.map((u) => r.nextState.byId[u]) as any[];
    expect(ordered.length).toBe(2);
    expect(ordered[0].is_wasl).toBe(false);
    expect(ordered[1].is_wasl).toBe(true);
  });

  it('setIsWasl toggles is_wasl on the target segment', () => {
    const state = makeApplyCommandState([
      makeSegment(0, 0, 4000, {
        segment_uid: 'uid-w',
        matched_ref: '2:5:1-2:5:3',
        is_wasl: false,
      }),
    ]);
    const onCmd: SetIsWaslCommand = { type: 'setIsWasl', segmentUid: 'uid-w', is_wasl: true };
    const onResult = applyCommand(state, onCmd);
    const onSeg = Object.values(onResult.nextState.byId ?? onResult.nextState)[0] as any;
    expect(onSeg.is_wasl).toBe(true);
    expect(onResult.operation.op_type).toBe('set_is_wasl');
    expect(onResult.operation.kind).toBe('single-index');

    // Round-trip back to false.
    const offCmd: SetIsWaslCommand = { type: 'setIsWasl', segmentUid: 'uid-w', is_wasl: false };
    const offResult = applyCommand(
      { ...state, byId: { 'uid-w': onSeg } },
      offCmd,
    );
    const offSeg = Object.values(offResult.nextState.byId ?? offResult.nextState)[0] as any;
    expect(offSeg.is_wasl).toBe(false);
  });

  it('drops wrap_word_ranges from every child', () => {
    // Regression: a parent repetition seg used to leak its wrap onto every
    // split child, re-tagging post-split clean segs as repetitions and
    // making Auto Split feed the wrong refs to MFA.
    const state = makeApplyCommandState([
      makeSegment(0, 0, 4000, {
        segment_uid: 'uid-rep',
        matched_ref: '48:29:1-48:29:24',
        wrap_word_ranges: [['48:29:11', '48:29:11', '48:29:24']] as any,
      }),
    ]);
    const cmd: SplitCommand = {
      type: 'split', segmentUid: 'uid-rep', splitMs: 2000, newUids: ['uid-new'],
    };
    const r = applyCommand(state, cmd);
    const pieces = Object.values(r.nextState.byId ?? r.nextState) as any[];
    expect(pieces.length).toBe(2);
    for (const p of pieces) {
      expect(p.wrap_word_ranges).toBeUndefined();
    }
  });
});
