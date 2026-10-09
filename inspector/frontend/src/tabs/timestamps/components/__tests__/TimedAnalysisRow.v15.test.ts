import { cleanup, fireEvent, render, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashPort } from '../../../../lib/playback/dash-port';
import { chapterOccasions } from '../../../../lib/recitation-data/occasions';
import { nativeReading } from '../../../../lib/recitation-data/test-native-fixture';
import {
    SAKT_COLUMN_ID,
    v15SaktReading,
    v15VariantReading,
} from '../../../../lib/recitation-data/test-v15-fixture';
import { assembleWaslGroup } from '../../../../lib/recitation-data/ts-source';
import type { TsShardReading } from '../../../../lib/types/ts-client';
import { TS_CLICK_DELAY_MS } from '../../utils/constants';
import { focusWaslGroup, loadedVerse } from '../../stores/verse';
import TimedAnalysisRow from '../TimedAnalysisRow.svelte';

type Row = { updateHighlights: () => void };

function show(reading: TsShardReading, ref: string): void {
    const data = assembleWaslGroup(
        'r', chapterOccasions([reading]), ref, {}, {}, { audio_category: 'by_surah' }, '',
    );
    loadedVerse.set({ data, tsSegOffset: reading.parts[0]!.t[0] / 1000, tsSegEnd: 1e9 });
}

const option = (container: HTMLElement, name: string) =>
    container.querySelector<HTMLElement>(`[data-qc-variant-option="${name}"]`)!;

const spotted = (container: HTMLElement): string[] =>
    [...container.querySelectorAll<HTMLElement>('.qc-variant-spot')].map(({ dataset }) =>
        dataset.qcColumnId !== undefined ? `c${dataset.qcColumnId}`
            : dataset.qcSoundId !== undefined ? `s${dataset.qcSoundId}` : `b${dataset.qcBoundaryId}`,
    ).sort();

describe('TimedAnalysisRow v15 reading variants', () => {
    beforeEach(() => {
        loadedVerse.set(null);
        focusWaslGroup.set(null);
    });

    afterEach(() => {
        vi.restoreAllMocks();
        vi.useRealTimers();
        cleanup();
        loadedVerse.set(null);
    });

    it('renders read-only numbers with the chosen face filled', async () => {
        show(v15VariantReading(), '6:143');
        const { container } = render(TimedAnalysisRow);
        await waitFor(() => expect(container.querySelector('[data-qc-variant]')).not.toBeNull());

        expect(option(container, 'tashil').dataset.qcVariantState).toBe('chosen');
        expect(option(container, 'ibdal').dataset.qcVariantState).toBe('default');
        expect(option(container, 'tashil').getAttribute('aria-disabled')).toBe('true');
        expect(option(container, 'tashil').closest('[data-qc-word-id]')?.getAttribute('data-qc-word-id'))
            .toBe('1');
    });

    it('spotlights exactly the other face\'s cells and names it in the tip', async () => {
        show(v15VariantReading(), '6:143');
        const { container } = render(TimedAnalysisRow);
        await waitFor(() => expect(container.querySelector('[data-qc-variant]')).not.toBeNull());

        await fireEvent.pointerOver(option(container, 'ibdal'));
        await fireEvent.pointerEnter(option(container, 'ibdal'));

        await waitFor(() => expect(spotted(container)).toEqual(['c101', 's1']));
        await waitFor(
            () => expect(document.querySelector('.cell-tip')?.textContent)
                .toContain('Istifham article: IbdalOther reading'),
            { timeout: 1_000 },
        );

        await fireEvent.pointerLeave(option(container, 'ibdal'));
        await waitFor(() => expect(spotted(container)).toEqual([]));
    });

    it('does not seek or loop when a number is clicked or pressed', async () => {
        show(v15VariantReading(), '6:143');
        const seek = vi.spyOn(dashPort, 'seek').mockImplementation(() => undefined);
        const { container } = render(TimedAnalysisRow);
        await waitFor(() => expect(container.querySelector('[data-qc-variant]')).not.toBeNull());

        await fireEvent.click(option(container, 'ibdal'));
        await fireEvent.dblClick(option(container, 'tashil'));
        await fireEvent.keyDown(option(container, 'tashil'), { key: 'Enter' });
        await new Promise((resolve) => setTimeout(resolve, TS_CLICK_DELAY_MS + 50));

        expect(seek).not.toHaveBeenCalled();
        expect(option(container, 'tashil').dataset.qcVariantState).toBe('chosen');
    });

    it('renders a reading without variants with no variant markup', async () => {
        show(nativeReading('r1', [
            { ref: '6:143', start: 100, end: 300, text: 'a' },
            { ref: '6:143', start: 300, end: 500, text: 'b' },
        ]), '6:143');
        const { container } = render(TimedAnalysisRow);
        await waitFor(() => expect(container.querySelectorAll('[data-qc-word-id]')).toHaveLength(2));

        expect(container.querySelector('[data-qc-variant]')).toBeNull();
        expect(container.querySelector('.analysis-row.has-boundary-variants')).toBeNull();
    });
});

describe('TimedAnalysisRow timed sakt sign', () => {
    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
        loadedVerse.set(null);
    });

    async function signAt(reading: TsShardReading, nowMs: number): Promise<HTMLElement> {
        show(reading, '75:27');
        vi.spyOn(dashPort, 'currentTimeMs').mockReturnValue(nowMs);
        const { component, container } = render(TimedAnalysisRow);
        const selector = `.pause-bridge[data-qc-column-id="${SAKT_COLUMN_ID}"]`;
        await waitFor(() => expect(container.querySelector(selector)).not.toBeNull());
        (component as unknown as Row).updateHighlights();
        return container.querySelector<HTMLElement>(selector)!;
    }

    it('lights the sign column while the playhead is inside its timing', async () => {
        const sign = await signAt(v15SaktReading(), 86_500);
        expect(sign.textContent).toContain('ۜ');
        expect(sign.classList).toContain('active');
    });

    it.each([86_000, 86_800])('leaves the sign dark at %i ms', async (now) => {
        const sign = await signAt(v15SaktReading(), now);
        expect(sign.classList).not.toContain('active');
    });

    it('never lights a sign column without a timing row', async () => {
        const sign = await signAt(v15SaktReading(false), 86_500);
        expect(sign.classList).not.toContain('active');
    });
});
