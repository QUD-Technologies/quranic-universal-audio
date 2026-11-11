import { afterEach, describe, expect, it } from 'vitest';
import { get } from 'svelte/store';

import {
    resetBoundaryFilter,
    toggleBoundaryState,
    valBoundaryFilter,
} from '../../stores/boundary-filter';

afterEach(() => resetBoundaryFilter());

describe('valBoundaryFilter', () => {
    it('starts on Unset only', () => {
        expect([...get(valBoundaryFilter)]).toEqual(['unset']);
    });

    it('reset drops chips the reviewer turned on back to Unset only', () => {
        toggleBoundaryState('wasl');
        toggleBoundaryState('waqf');
        toggleBoundaryState('unset');
        expect([...get(valBoundaryFilter)].sort()).toEqual(['waqf', 'wasl']);

        resetBoundaryFilter();

        expect([...get(valBoundaryFilter)]).toEqual(['unset']);
    });

    it('keeps the last remaining chip on', () => {
        toggleBoundaryState('unset');
        expect([...get(valBoundaryFilter)]).toEqual(['unset']);
    });
});
