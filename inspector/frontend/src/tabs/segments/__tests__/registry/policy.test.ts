// Snapshot tests pinning the TS-side IssueRegistry to plan Appendix A.

import { describe, expect, it } from 'vitest';

import { IssueRegistry } from '../../domain/registry';

const EXPECTED = {
  failed:            { canIgnore: false, autoSuppress: true,  persistsIgnore: false, scope: 'per_segment', cardType: 'generic',        severity: 'error' },
  missing_verses:    { canIgnore: false, autoSuppress: true,  persistsIgnore: false, scope: 'per_verse',   cardType: 'missingVerses',  severity: 'error' },
  missing_words:     { canIgnore: false, autoSuppress: false, persistsIgnore: false, scope: 'per_verse',   cardType: 'missingWords',   severity: 'error' },
  structural_errors: { canIgnore: false, autoSuppress: true,  persistsIgnore: false, scope: 'per_chapter', cardType: 'error',          severity: 'error' },
  low_confidence:    { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning' },
  low_confidence_v2: { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning' },
  repetitions:       { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning' },
  audio_bleeding:    { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning' },
  boundary_adj:      { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'warning' },
  cross_verse:       { canIgnore: false, autoSuppress: false, persistsIgnore: false, scope: 'per_segment', cardType: 'generic',        severity: 'warning' },
  qalqala:           { canIgnore: false, autoSuppress: false, persistsIgnore: false, scope: 'per_segment', cardType: 'generic',        severity: 'info' },
  muqattaat:         { canIgnore: false, autoSuppress: false, persistsIgnore: false, scope: 'per_segment', cardType: 'generic',        severity: 'info' },
  basmala_amin:      { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info' },
  hidden_pause:      { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info' },
  missed_waqf:       { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info' },
  false_split:       { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info' },
  unmarked_wasl:     { canIgnore: true,  autoSuppress: true,  persistsIgnore: true,  scope: 'per_segment', cardType: 'generic',        severity: 'info' },
};

describe('registry policy snapshot', () => {
  it('pins matrix verbatim (TS)', () => {
    for (const [cat, want] of Object.entries(EXPECTED)) {
      const row = IssueRegistry[cat];
      expect(row).toBeTruthy();
      for (const [key, value] of Object.entries(want)) {
        expect((row as any)[key]).toBe(value);
      }
    }
  });

  it('mirror parity with Python registry', () => {
    const keys = Object.keys(IssueRegistry).sort();
    expect(keys).toEqual(Object.keys(EXPECTED).sort());
  });
});
