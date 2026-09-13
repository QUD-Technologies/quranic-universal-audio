// Mark-ready copy module parity test.
//
// Guards against drift between:
//   1. The CHECKLIST_ORDER keys exported by ../../copy/mark-ready
//   2. The MarkReadyChecklist interface generated from the backend
//      Pydantic model (lib/types/generated/schemas.ts)
//   3. The BLOCKING_COUNT_KEYS set the backend uses for the submission
//      count gate
//   4. The actual markdown files (every key must have a ### section)
//
// If you add a checklist key on either side, both this test and the
// codegen guard (`schema-codegen-check` in CI) will fail the build.

import { describe, expect, it } from 'vitest';

import type { MarkReadyChecklist } from '../../../../lib/types/generated/schemas';
import {
    BLOCKING_COUNT_KEYS,
    BLOCKING_LABELS,
    CHECKLIST_ORDER,
    emptyChecklist,
    isAllChecked,
    markReadyCopy,
} from '../../copy/mark-ready';
import { OWNER_ONLY_CATEGORIES } from '../../domain/registry';

describe('mark-ready copy module', () => {
    it('exports exactly six checklist keys', () => {
        expect(CHECKLIST_ORDER).toHaveLength(6);
    });

    it('checklist keys match the codegen MarkReadyChecklist interface', () => {
        // Build a fully-true checklist and assert TypeScript accepts every
        // CHECKLIST_ORDER key against the generated interface.
        const sample: MarkReadyChecklist = {
            failed_alignments: true,
            missing_words: true,
            low_confidence: true,
            repetitions: true,
            splits_wasl_waqf: true,
            basmala_amin_intros: true,
        };
        const expected = Object.keys(sample).sort();
        const actual = [...CHECKLIST_ORDER].sort();
        expect(actual).toEqual(expected);
    });

    it('no owner-only validation category gates submission', () => {
        // An owner-only category is invisible to the reviewer, so it must never
        // appear in the blocking-count panel they cannot act on.
        for (const cat of OWNER_ONLY_CATEGORIES) {
            expect(BLOCKING_COUNT_KEYS as readonly string[]).not.toContain(cat);
        }
    });

    it('every checklist key has non-empty label text loaded from markdown', () => {
        for (const key of CHECKLIST_ORDER) {
            const item = markReadyCopy.checklist.find((c) => c.key === key);
            expect(item, `missing copy for ${key}`).toBeTruthy();
            expect(item!.label.length).toBeGreaterThan(10);
        }
    });

    it('form.md sections are all populated', () => {
        const f = markReadyCopy.form;
        expect(f.title.length).toBeGreaterThan(0);
        expect(f.warning.length).toBeGreaterThan(0);
        expect(f.blockingLede.length).toBeGreaterThan(0);
        expect(f.submit.length).toBeGreaterThan(0);
        expect(f.cancel.length).toBeGreaterThan(0);
        expect(f.submitting.length).toBeGreaterThan(0);
    });

    it('comments.md provides label + placeholder for both textareas', () => {
        expect(markReadyCopy.comments.checks.label.length).toBeGreaterThan(0);
        expect(markReadyCopy.comments.checks.placeholder.length).toBeGreaterThan(0);
        expect(markReadyCopy.comments.issues.label.length).toBeGreaterThan(0);
        expect(markReadyCopy.comments.issues.placeholder.length).toBeGreaterThan(0);
    });

    it('blocking count keys exactly match the backend list', () => {
        // The backend enforces the same keys via BLOCKING_COUNT_KEYS in
        // qua_shared/schemas/wire/mark_ready.py — these must stay aligned.
        // `boundary_adj` is deliberately absent: it is an owner-only category
        // (see the owner-only test below), so a reviewer can neither see nor
        // resolve it.
        expect([...BLOCKING_COUNT_KEYS].sort()).toEqual([
            'basmala_amin',
            'cross_verse',
            'low_confidence',
            'low_confidence_v2',
            'repetitions',
        ]);
    });

    it('every blocking key has a human label', () => {
        for (const k of BLOCKING_COUNT_KEYS) {
            expect(BLOCKING_LABELS[k]).toBeTruthy();
        }
    });

    it('emptyChecklist + isAllChecked agree', () => {
        const empty = emptyChecklist();
        expect(isAllChecked(empty)).toBe(false);
        const full: MarkReadyChecklist = {
            failed_alignments: true,
            missing_words: true,
            low_confidence: true,
            repetitions: true,
            splits_wasl_waqf: true,
            basmala_amin_intros: true,
        };
        expect(isAllChecked(full)).toBe(true);
    });
});
