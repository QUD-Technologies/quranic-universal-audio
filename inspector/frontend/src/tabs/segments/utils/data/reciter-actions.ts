/**
 * Reciter-level reload action — shared by SegmentsTab's reciter-change
 * handler and the stale-data reload paths in history/save.
 *
 * Fetches the full segment corpus on the critical path, then lets validation,
 * stats, and history populate independently. Chapter-select options come from
 * `segAllData` reactively. Each load registers the manifest size per chapter
 * URL with `play-url` so direct CDN play is gated on an unchanged file.
 */

import { get } from 'svelte/store';

import { fetchJson } from '../../../../lib/api';
import { registerExpectedSizes } from '../../../../lib/playback/play-url';
import { loadQuranRefs, quranRefs } from '../../../../lib/refs/quran-refs';
import { deliveryRiwayah } from '../../../dashboard/stores/catalog-data';
import type { SegAllResponse, SegValidateResponse } from '../../../../lib/types/generated/schemas';
import { preconnectOrigins } from '../../../../lib/utils/preconnect';
import type { SegAllState } from '../../stores/chapter';
import {
    reciterVbrChapters,
    segAllData,
    selectedChapter,
    selectedReciter,
    selectedVerse,
} from '../../stores/chapter';
import { activeFilters } from '../../stores/filters';
import { savedFilterView } from '../../stores/navigation';
import { setValidation } from '../../stores/validation';
import { clearPerReciterState } from './clear-per-reciter-state';
import { dkTextForRef } from './references';

/**
 * Hydrate per-segment fields that the server stripped from /seg/all to save
 * wire bytes:
 *   - `audio_url`: re-derived from the top-level `audio_by_chapter` map.
 *     Every consumer expects each Segment to carry its own URL, and shared
 *     string references across segments in the same chapter cost less heap
 *     than separately allocated strings on the wire.
 *   - `matched_text`: re-derived via `dkTextForRef` from the global
 *     `quran-refs.json` bundle. Display already uses `dkTextForRef`; the
 *     in-memory copy is for save round-trip + undo snapshots + merge text
 *     composition. If `quranRefs` is still null (offline / fetch failed),
 *     consumers tolerate empty `matched_text` via `|| ''` fallbacks.
 */
function _hydrateSegAll(all: SegAllResponse): SegAllState {
    const audioByChapter = all.audio_by_chapter ?? {};
    const refs = get(quranRefs);
    const dkWords = refs?.dk_words;
    const vwc = refs?.verse_word_counts;
    const hydrated = all as unknown as SegAllState;
    for (const seg of hydrated.segments) {
        if (!seg.audio_url) {
            seg.audio_url = audioByChapter[String(seg.chapter)] ?? '';
        }
        if (!seg.matched_text && dkWords && vwc) {
            seg.matched_text = dkTextForRef(seg.matched_ref, dkWords, vwc);
        }
    }
    return hydrated;
}

/**
 * Re-fetch just `/seg/all` for the currently selected reciter. Lighter
 * counterpart to `reloadCurrentReciter` — used post-save / post-undo when
 * filters, chapter selection, and per-reciter waveform/peaks state must be
 * preserved.
 */
export async function reloadSegAll(): Promise<void> {
    const reciter = get(selectedReciter);
    if (!reciter) return;
    try {
        const all = await fetchJson<SegAllResponse>(`/api/seg/all/${reciter}`);
        if (get(selectedReciter) !== reciter) return;
        if ('error' in all) {
            console.error('Error loading all segments:', (all as any).error);
            return;
        }
        registerExpectedSizes(all.size_bytes_by_url ?? {});
        segAllData.set(_hydrateSegAll(all));
        reciterVbrChapters.set(new Set(all.reciter_vbr_chapters ?? []));
    } catch (e) {
        console.error('Error reloading seg all:', e);
    }
}

/**
 * Re-fetch data for the currently selected reciter. Used for the stale-data
 * reload paths triggered after undo (from hideHistoryView / hideSavePreview)
 * and from SegmentsTab's reciter-change handler.
 */
export async function reloadCurrentReciter(): Promise<void> {
    const reciter = get(selectedReciter);
    if (!reciter) return;

    selectedChapter.set('');
    selectedVerse.set('');
    activeFilters.set([]);
    savedFilterView.set(null);
    clearPerReciterState();

    // Kick off the refs load in parallel; hydration tolerates a null
    // ``quranRefs`` (matched_text stays empty for those segs and consumer
    // fallbacks via dkTextForRef at render time fill the gap).
    //
    // The bundle is edition-specific, so it is keyed on THIS delivery's
    // riwayah. A delivery in an unsupported riwayah loads no bundle at all
    // rather than the Hafs one: every ref the tab then renders would be
    // against the wrong verse geometry.
    const riwayah = deliveryRiwayah(reciter);
    const allPromise = Promise.all([
        fetchJson<SegAllResponse>(`/api/seg/all/${reciter}`),
        riwayah ? loadQuranRefs(riwayah) : Promise.resolve(),
    ])
        .then(([all]) => {
            if (get(selectedReciter) !== reciter) return;
            if ('error' in all) {
                console.error('Error loading all segments:', (all as any).error);
                return;
            }
            registerExpectedSizes(all.size_bytes_by_url ?? {});
            segAllData.set(_hydrateSegAll(all));
            reciterVbrChapters.set(new Set(all.reciter_vbr_chapters ?? []));
            preconnectOrigins(Object.values(all.audio_by_chapter ?? {}));
        })
        .catch((e) => console.error('Error loading all segments:', e));

    void fetchJson<SegValidateResponse>(`/api/seg/validate/${reciter}`)
        .then((data) => {
            if (get(selectedReciter) === reciter) setValidation(data);
        })
        .catch((e) => console.error('Error loading validation:', e));

    // Stats are lazy-fetched by StatsPanel.svelte on accordion open. Compute
    // is ~0.7-1.2 s server-side on prod-sized reciters; paying it eagerly on
    // every reciter switch is wasteful — the panel rarely gets opened.

    // Edit-history is lazy-fetched when the user opens the History panel —
    // see SegmentsFooter.svelte::showHistoryView. The validate response
    // already carries the precomputed split_group_index that accordion cards
    // need, so cold reciter-select no longer pulls the full JSONL.

    await allPromise;
}
