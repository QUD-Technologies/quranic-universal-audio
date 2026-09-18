/**
 * 1 / 2 label the WASL/WAQF boundary that belongs to the focused piece of a
 * cross-verse card. The card publishes the boundary's commit as `setWasl` in
 * the active-row bundle (see `GenericIssueCard`); these tests pin the dispatch
 * half: the digits must reach that callback with the right value, must honour
 * the edit gate, and must stay out of the way (unhandled) when the focused row
 * has no boundary — so the arrows keep seeking.
 */

import { editingMode } from '../../../../lib/stores/editing-mode';
import { setActiveTab } from '../../../../lib/utils/active-tab';
import { TAB_NAMES } from '../../../../lib/utils/constants';
import { activeRowActions } from '../../stores/active-actions';
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

    it('keeps the arrow keys on seek (they resolve to seek_back/seek_fwd)', () => {
        publishBundle(true);
        // Arrows must NOT reach setWasl — whatever the seek path does with
        // them, the boundary is untouched.
        press('ArrowLeft');
        press('ArrowRight');
        expect(setWasl).not.toHaveBeenCalled();
    });
});
