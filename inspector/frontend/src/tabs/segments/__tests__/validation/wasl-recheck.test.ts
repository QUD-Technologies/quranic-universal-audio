/**
 * `waslRecheck` store — reseeded from each validate payload, minus uids an
 * unsaved `set_is_wasl` op already answers; `resolveWaslRecheck` drops one.
 */
import { get } from 'svelte/store';
import { afterEach, describe, expect, it } from 'vitest';

import type { SegValidateResponse } from '../../../../lib/types/generated/schemas';
import type { EditOp } from '../../../../lib/types/view-models';
import { clearOpLog, finalizeOp } from '../../stores/dirty';
import { clearValidation, resolveWaslRecheck, segValidation, waslRecheck } from '../../stores/validation';

const payload = (uids: string[]): SegValidateResponse => ({ wasl_recheck: uids }) as SegValidateResponse;

afterEach(() => {
    clearOpLog();
    clearValidation();
});

describe('waslRecheck', () => {
    it('mirrors the payload and empties when validation is cleared', () => {
        segValidation.set(payload(['a', 'b']));
        expect([...get(waslRecheck)]).toEqual(['a', 'b']);
        clearValidation();
        expect(get(waslRecheck).size).toBe(0);
    });

    it('skips uids answered by an unsaved set_is_wasl op', () => {
        finalizeOp(2, { op_type: 'set_is_wasl', targets_before: [{ segment_uid: 'a' }] } as unknown as EditOp);
        segValidation.set(payload(['a', 'b']));
        expect([...get(waslRecheck)]).toEqual(['b']);
    });

    it('resolveWaslRecheck drops only the answered uid', () => {
        segValidation.set(payload(['a', 'b']));
        resolveWaslRecheck('a');
        expect([...get(waslRecheck)]).toEqual(['b']);
    });
});
