/**
 * 1 / 2 label the WASL/WAQF boundary that belongs to the focused piece of a
 * cross-verse card. The card publishes the boundary's commit as `setWasl` in
 * the active-row bundle (see `GenericIssueCard`); these tests pin the dispatch
 * half: the digits must reach that callback with the right value, must honour
 * the edit gate, and must stay out of the way (unhandled) when the focused row
 * has no boundary — so the arrows keep seeking.
 */

import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { editingMode } from '../../../../lib/stores/editing-mode';
import { setActiveTab } from '../../../../lib/utils/active-tab';
import { TAB_NAMES } from '../../../../lib/utils/constants';
import { activeRowActions } from '../../stores/active-actions';
import { accordionNavCursor, playingSegmentIndex } from '../../stores/playback';
import { valUiOpenCategory } from '../../stores/validation';
import { handleSegmentsKey } from '../keyboard';

const setWasl = vi.fn();

function press(code: string): boolean {
    return handleSegmentsKey({ code, target: document.body } as unknown as KeyboardEvent);
}

function publishBundle(withWasl: boolean): void {
    activeRowActions.set({
        owner: Symbol('test-row'),
        chapter: 1,
        index: 4,
        uid: 'piece-a',
        ...(withWasl ? { setWasl } : {}),
    });
}

beforeEach(() => {
    setActiveTab(TAB_NAMES.SEGMENTS);
    editingMode.set({ kind: 'editor' });
    valUiOpenCategory.set('cross_verse'); // an accordion is open
    setWasl.mockClear();
});

afterEach(() => {
    setActiveTab('dashboard');
    valUiOpenCategory.set(null);
    activeRowActions.set(null);
    accordionNavCursor.set(null);
    playingSegmentIndex.set(null);
    editingMode.set({ kind: 'view', viewReason: 'unauthenticated' });
});

describe('WASL/WAQF digit shortcuts', () => {
    it('1 labels the focused piece’s boundary wasl', () => {
        publishBundle(true);
        expect(press('Digit1')).toBe(true);
        expect(setWasl).toHaveBeenCalledWith(true);
    });

    it('2 labels it waqf', () => {
        publishBundle(true);
        expect(press('Digit2')).toBe(true);
        expect(setWasl).toHaveBeenCalledWith(false);
    });

    it('leaves the keys unhandled when the focused row has no boundary', () => {
        publishBundle(false);
        expect(press('Digit1')).toBe(false);
        expect(press('Digit2')).toBe(false);
        expect(setWasl).not.toHaveBeenCalled();
    });

    it('is inert with no focused row at all', () => {
        activeRowActions.set(null);
        expect(press('Digit1')).toBe(false);
        expect(setWasl).not.toHaveBeenCalled();
    });

    it('honours the edit gate instead of labelling', () => {
        publishBundle(true);
        editingMode.set({ kind: 'view', viewReason: 'wrong-assignee' });
        expect(press('Digit1')).toBe(true); // consumed, but no mutation
        expect(setWasl).not.toHaveBeenCalled();
    });

    it('does not fire outside an open accordion', () => {
        publishBundle(true);
        valUiOpenCategory.set(null);
        expect(press('Digit1')).toBe(false);
        expect(setWasl).not.toHaveBeenCalled();
    });

    it('leaves the nav cursor alone, so ↑/↓ still steps from the same segment', () => {
        publishBundle(true);
        const cursor = { uid: 'piece-1', chapter: 1, index: 4, startMs: 2000, endMs: 3000 };
        accordionNavCursor.set({ ...cursor });
        press('Digit1');
        expect(setWasl).toHaveBeenCalledWith(true);
        expect(get(accordionNavCursor)).toEqual(cursor);
    });

    it('does not move what is playing', () => {
        publishBundle(true);
        const playing = { chapter: 1, index: 4, origin: 'accordion' as const };
        playingSegmentIndex.set({ ...playing });
        press('Digit2');
        expect(setWasl).toHaveBeenCalledWith(false);
        expect(get(playingSegmentIndex)).toEqual(playing);
    });

    it('keeps the arrow keys on seek (they resolve to seek_back/seek_fwd)', () => {
        publishBundle(true);
        // Arrows must NOT reach setWasl — whatever the seek path does with
        // them, the boundary is untouched.
        press('ArrowLeft');
        press('ArrowRight');
        expect(setWasl).not.toHaveBeenCalled();
    });
});
