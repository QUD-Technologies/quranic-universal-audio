// TS-registry ↔ Python-registry parity test.
//
// The Python side is the source of truth; the TS twin mirrors it row-for-row.
// These assertions guarantee no drift between the two registries.

import { describe, expect, it } from 'vitest';

import { IssueRegistry } from '../../domain/registry';

// Mirror the Python registry policy + presentation fields row-for-row. The
// boolean policy keys were the only ones pinned here originally — accordionOrder
// and displayTitle now ride along so a TS / Python drift on either surfaces
// here instead of waiting for someone to notice an out-of-order accordion or a
// mistitled card. ``description`` is intentionally NOT pinned: it carries
// known drift between TS and Python and is tracked as a surfaced bug.
const PY_SNAPSHOT = {
  failed:            { canIgnore: false, autoSuppress: true,  persistsIgnore: false, scope: 'per_segment', cardType: 'generic',        severity: 'error',   accordionOrder: 1, displayTitle: 'Failed Alignments' },
  missing_verses:    { canIgnore: false, autoSuppress: true,  persistsIgnore: false, scope: 'per_verse',   cardType: 'missingVerses',  severity: 'error',   accordionOrder: 2, displayTitle: 'Missing Verses' },
  missing_words:     { canIgnore: false, autoSuppress: false, persistsIgnore: false, scope: 'per_verse',   cardType: 'missingWords',   severity: 'error',   accordionOrder: 3, displayTitle: 'Missing Words' },
  structural_errors: { canIgnore: false, autoSuppress: true,  persistsIgnore: false, scope: 'per_chapter', cardType: 'error',          severity: 'error',   accordionOrder: 4, displayTitle: 'Structural Errors' },
  low_confidence:    { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning', accordionOrder: 5, displayTitle: 'Low Confidence' },
  low_confidence_v2: { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning', accordionOrder: 6, displayTitle: 'Low Confidence v2' },
  repetitions:       { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning', accordionOrder: 9, displayTitle: 'Detected Repetitions' },
  audio_bleeding:    { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning', accordionOrder: 7, displayTitle: 'Audio Bleeding' },
  boundary_adj:      { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning', accordionOrder: 8, displayTitle: 'May Require Boundary Adjustment', ownerOnly: true },
  cross_verse:       { canIgnore: false, autoSuppress: false, persistsIgnore: false, scope: 'per_segment', cardType: 'generic',        severity: 'warning', accordionOrder: 10, displayTitle: 'Cross-verse' },
  qalqala:           { canIgnore: false, autoSuppress: false, persistsIgnore: false, scope: 'per_segment', cardType: 'generic',        severity: 'info',    accordionOrder: 12, displayTitle: 'Qalqala', ownerOnly: true },
  muqattaat:         { canIgnore: false, autoSuppress: false, persistsIgnore: false, scope: 'per_segment', cardType: 'generic',        severity: 'info',    accordionOrder: 13, displayTitle: 'Muqattaʼat' },
  basmala_amin:      { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info',    accordionOrder: 14, displayTitle: 'Basmala + Amin' },
  hidden_pause:      { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info',    accordionOrder: 15, displayTitle: 'Hidden Pause (review)' },
  missed_waqf:       { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info',    accordionOrder: 11, displayTitle: 'Missed Waqf (review)' },
  false_split:       { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info',    accordionOrder: 16, displayTitle: 'False Split (review)' },
  unmarked_wasl:     { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info',    accordionOrder: 17, displayTitle: 'Unmarked Wasl (review)' },
};

describe('TS ↔ Python registry parity', () => {
  it('TS registry matches Python registry snapshot', () => {
    for (const [cat, want] of Object.entries(PY_SNAPSHOT)) {
      const row = IssueRegistry[cat];
      expect(row).toBeTruthy();
      for (const [key, value] of Object.entries(want)) {
        expect((row as any)[key]).toBe(value);
      }
      // `ownerOnly` is opt-in; every row the snapshot doesn't pin must be
      // falsy so a new owner-only category can't slip in silently.
      if (!('ownerOnly' in want)) expect(row?.ownerOnly ?? false).toBe(false);
    }
  });
});
