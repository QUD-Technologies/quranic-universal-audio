import { fireEvent, render } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import WordTimingEditor from '../../components/edit/WordTimingEditor.svelte';

describe('sample word timing editor', () => {
    it('renders one handle per shared boundary and sends both changed word edges', async () => {
        const onChange = vi.fn();
        const { container, getByRole } = render(WordTimingEditor, {
            props: {
                words: [
                    { start_ms: 0, end_ms: 100 },
                    { start_ms: 100, end_ms: 200 },
                    { start_ms: 200, end_ms: 300 },
                ],
                labels: ['one', 'two', 'three'],
                startMs: 0,
                endMs: 300,
                width: 300,
                lockedIndex: 1,
                onChange,
                onSeek: vi.fn(),
                onLock: vi.fn(),
            },
        });
        expect(container.querySelectorAll('.word-boundary')).toHaveLength(4);
        const shared = getByRole('button', { name: 'Drag boundary between one and two' });
        await fireEvent.pointerDown(shared, { clientX: 100 });
        await fireEvent.pointerMove(window, { clientX: 120 });
        await fireEvent.pointerUp(window, { clientX: 120 });
        expect(onChange).toHaveBeenLastCalledWith([
            { start_ms: 0, end_ms: 120 },
            { start_ms: 120, end_ms: 200 },
            { start_ms: 200, end_ms: 300 },
        ]);
    });

    it('highlights only the sounding word and follows playback as it advances', async () => {
        const props = {
            words: [
                { start_ms: 0, end_ms: 100 },
                { start_ms: 100, end_ms: 200 },
                { start_ms: 200, end_ms: 300 },
            ],
            labels: ['one', 'two', 'three'],
            startMs: 0,
            endMs: 300,
            width: 300,
            lockedIndex: null,
            activeIndex: 0,
            onChange: vi.fn(),
            onSeek: vi.fn(),
            onLock: vi.fn(),
        };
        const { container, rerender } = render(WordTimingEditor, { props });
        const activeCards = () => [...container.querySelectorAll('.word-card.is-active')];
        expect(activeCards()).toHaveLength(1);
        expect(activeCards()[0]?.textContent).toContain('one');

        await rerender({ ...props, activeIndex: 1 });
        expect(activeCards()).toHaveLength(1);
        expect(activeCards()[0]?.textContent).toContain('two');

        await rerender({ ...props, activeIndex: null });
        expect(activeCards()).toHaveLength(0);
    });
});
