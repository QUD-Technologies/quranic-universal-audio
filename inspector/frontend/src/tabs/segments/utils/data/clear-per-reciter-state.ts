/**
 * Reset per-reciter imperative state so validation / stats / history /
 * save-preview panels reset when the user switches reciter OR a stale-data
 * reload triggers.
 *
 * Shared between SegmentsTab's reciter handler (direct call) and the
 * stale-data reload path in reciter-actions.ts::reloadCurrentReciter().
 */

import { clearWaveformCache } from '../../../../lib/utils/waveform-cache';
import { clearAccordionPin } from '../../stores/accordion-pin';
import { clearAutoSplitMap } from '../../stores/auto-split';
import { clearAllStagedPicks } from '../../stores/staged-split';
import {
    pickerDisplayChapter,
    reciterVbrChapters,
    segAllData,
    segCurrentIdx,
    segData,
} from '../../stores/chapter';
import { chapterCbrKbps } from '../../stores/chapter-meta';
import { setPendingOp } from '../../stores/dirty';
import {
    clearDirtyMap,
    clearOpLog,
} from '../../stores/dirty';
import { clearEdit } from '../../stores/edit';
import { setHistoryData, setHistoryVisible } from '../../stores/history';
import { clearMergeRedirects } from '../../stores/merge-redirect';
import { playingSegmentIndex } from '../../stores/playback';
import {
    clearSavePreviewData,
    hidePreview,
    savedChains,
} from '../../stores/save';
import { clearStats } from '../../stores/stats';
import { clearValidation } from '../../stores/validation';
import { resetHistoryLoader } from '../history/loader';
import { disposeSegRange, stopSegAnimation } from '../playback/playback';
import { clearRowRegistry } from '../playback/row-registry';
import { resetWaveformState } from '../waveform/utils';

export function clearPerReciterState(): void {
    resetWaveformState();
    segAllData.set(null);
    segData.set(null);
    reciterVbrChapters.set(new Set());
    chapterCbrKbps.set(new Map());
    segCurrentIdx.set(-1);
    clearDirtyMap();
    clearOpLog();
    setPendingOp(null);
    clearEdit();
    clearMergeRedirects();

    clearValidation();
    clearAccordionPin();
    clearAutoSplitMap();
    clearAllStagedPicks();
    clearStats();

    savedChains.set(null);
    setHistoryVisible(false);
    setHistoryData(null);
    resetHistoryLoader();
    hidePreview();
    clearSavePreviewData();

    clearRowRegistry();
    playingSegmentIndex.set(null);
    pickerDisplayChapter.set(null);
    clearWaveformCache();

    disposeSegRange();
    stopSegAnimation();
}
