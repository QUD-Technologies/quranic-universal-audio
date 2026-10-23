// Frontend reads classified_issues from backend DTOs; no live classification.

import { describe, expect, it } from 'vitest';

import { classifiedIssuesOf, deriveOpIssueDelta } from '../../utils/validation/classified-issues';
import { usesStoredClassifiedIssues } from '../../utils/history/items';

async function tryImport(path: string): Promise<unknown | null> {
  try {
    return await import(/* @vite-ignore */ path);
  } catch {
    return null;
  }
}

describe('classifier output parity', () => {
  it('frontend reads classified_issues field from backend DTO; no local classification', async () => {
    const classifyMod = await tryImport('../../utils/validation/classify');
    expect(classifyMod).toBeNull();
    expect(typeof classifiedIssuesOf).toBe('function');
  });

  it('history items do not import a live segment classifier', async () => {
    // Asserting against the hardcoded ``usesStoredClassifiedIssues`` constant
    // is tautological — only an intentional source edit could flip it. The
    // real invariant is that no live-classifier module is reachable from
    // history items. Probe the conventional location and assert absence.
    expect(usesStoredClassifiedIssues).toBe(true);
    const liveClassifier = await tryImport('../../utils/validation/live-classifier');
    expect(liveClassifier).toBeNull();
  });

  it('utils/validation/classify is not present (classification is server-side)', async () => {
    const classifyMod = await tryImport('../../utils/validation/classify');
    expect(classifyMod).toBeNull();
  });

  it('history issue deltas hide the legacy Basmala + Amin category', () => {
    const delta = deriveOpIssueDelta([
      {
        targets_before: [{ segment_uid: 'u1', classified_issues: ['basmala_amin'] }],
        targets_after: [{ segment_uid: 'u1', classified_issues: [] }],
      },
    ] as any);
    expect(delta).toEqual({ resolved: [], introduced: [], involved: [] });
  });
});
