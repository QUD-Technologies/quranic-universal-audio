import { cleanup, fireEvent, render } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { setEditingMode } from '../../../../../lib/stores/editing-mode';
import type { EditOp, Segment } from '../../../../../lib/types/view-models';
import { makeSegment } from '../../../__tests__/helpers/make-segment';
import { segAllData } from '../../../stores/chapter';
import { clearDirtyMap, clearOpLog, getChapterOps } from '../../../stores/dirty';
import { waslRecheck } from '../../../stores/validation';
import WaslBoundary from '../WaslBoundary.svelte';

const CHAPTER = 2;

function pieces(leftWasl: boolean): [Segment, Segment] {
    const left = { ...makeSegment(0, 0, 1000, { matched_ref: '2:5:1-2:5:9', is_wasl: leftWasl }), chapter: CHAPTER } as Segment;
    const right = { ...makeSegment(1, 1000, 2000, { matched_ref: '2:6:1-2:6:4' }), chapter: CHAPTER } as Segment;
    segAllData.set({ segments: [left, right] } as never);
    return [left, right];
}

function waslOps() {
    return getChapterOps(CHAPTER).filter((op) => op.op_type === 'set_is_wasl');
}

beforeEach(() => {
    setEditingMode({ kind: 'editor' });
});

afterEach(() => {
    cleanup();
    clearOpLog();
    clearDirtyMap();
    waslRecheck.set(new Set());
    segAllData.set(null);
    setEditingMode({ kind: 'view', viewReason: 'unauthenticated' });
});

describe('WaslBoundary re-asked boundary', () => {
    it('renders pending with neither WASL nor WAQF pressed', () => {
        const [left, right] = pieces(false);
        waslRecheck.set(new Set([left.segment_uid!]));
        const { container, getByText } = render(WaslBoundary, { leftSeg: left, rightSeg: right });

        expect(container.querySelector('.wasl-boundary')?.classList.contains('is-pending')).toBe(true);
        expect(getByText('WASL').getAttribute('aria-pressed')).toBe('false');
        expect(getByText('WAQF').getAttribute('aria-pressed')).toBe('false');
    });

    it('records a WAQF answer on a piece already waqf and closes the re-check', async () => {
        const [left, right] = pieces(false);
        const uid = left.segment_uid!;
        waslRecheck.set(new Set([uid]));
        const { getByText } = render(WaslBoundary, { leftSeg: left, rightSeg: right });

        await fireEvent.click(getByText('WAQF'));

        const ops = waslOps();
        expect(ops).toHaveLength(1);
        expect(ops[0]!.targets_before[0]!.segment_uid).toBe(uid);
        expect((ops[0] as EditOp & { command?: { is_wasl?: boolean } }).command?.is_wasl).toBe(false);
        expect(get(waslRecheck).has(uid)).toBe(false);
    });

    it('records the keyboard answer handed up through onCommitReady', () => {
        const [left, right] = pieces(true);
        const uid = left.segment_uid!;
        waslRecheck.set(new Set([uid]));
        let commit: ((value: boolean) => void) | null = null;
        render(WaslBoundary, {
            leftSeg: left,
            rightSeg: right,
            onCommitReady: (_uid: string, fn: ((value: boolean) => void) | null) => { commit = fn; },
        });

        commit!(true);

        expect(waslOps()).toHaveLength(1);
        expect(get(waslRecheck).has(uid)).toBe(false);
    });

    it('emits no op for a same-value click on a boundary that is not re-asked', async () => {
        const [left, right] = pieces(false);
        const { getByText } = render(WaslBoundary, { leftSeg: left, rightSeg: right });

        await fireEvent.click(getByText('WAQF'));

        expect(waslOps()).toHaveLength(0);
    });
});
