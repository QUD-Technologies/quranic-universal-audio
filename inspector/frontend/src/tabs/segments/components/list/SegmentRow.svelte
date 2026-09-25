<script lang="ts">
    /**
     * SegmentRow — one .seg-row card in the segments list.
     *
     * Used from every SegmentRow site: the primary list (#seg-list) via
     * SegmentsList.svelte, the validation accordion subcomponents
     * (GenericIssueCard / MissingWordsCard / MissingVersesCard), history
     * view (SplitChainRow / HistoryOp), and the save preview. Per-button
     * on:click handlers are wired directly here so no delegated container
     * listeners are needed.
     *
     * History-mode props. Highlight props (splitHL / trimHL / mergeHL /
     * changedFields) drive visual overlays in history mode.
     *
     * Layout: normal mode = horizontal (play-col | left-col | text-box).
     * History mode = vertical (waveform above text) — scoped via `class:mode-history`.
     *
     * Waveform observer: onMount registers the canvas with the segments
     * IntersectionObserver via _ensureWaveformObserver().observe(canvas).
     * On destroy the canvas is implicitly unobserved (the observer's weak
     * tracking releases destroyed nodes; see segments/waveform/index.ts).
     */

    import { onDestroy,onMount, tick } from 'svelte';
    import { get } from 'svelte/store';

    import { editGate } from '../../../../lib/actions/editGate';
    import { fetchJsonOrNull } from '../../../../lib/api';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import { shadowPrewarm } from '../../../../lib/playback/shadow-audio';
    import { quranRefs } from '../../../../lib/refs/quran-refs';
    import { currentUser } from '../../../../lib/stores/current-user';
    import { pushToast } from '../../../../lib/stores/toast';
    import type { FlagAuthor } from '../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../lib/types/view-models';
    import { clearAccordionPin } from '../../stores/accordion-pin';
    import { clearRowActions, publishRowActions } from '../../stores/active-actions';
    import { ensureAutoSplitMap } from '../../stores/auto-split';
    import {
        getAdjacentSegments,
        pickerDisplayChapter,
        refreshSegInStore,
        segAllData,
        segCurrentIdx,
        selectedChapter,
        selectedReciter,
        selectedVerse,
    } from '../../stores/chapter';
    import { dirtyTick, isDirty, isIndexDirty } from '../../stores/dirty';
    import {
        editingMountId,
        editingSegUid,
        editMode,
        setEditCanvas,
    } from '../../stores/edit';
    import { activeFilters } from '../../stores/filters';
    import { savedFilterView } from '../../stores/navigation';
    import { isSampleMode } from '../../stores/samples';
    import { missingWordsSegKeys } from '../../stores/validation';
    import { deriveRowChips, type RowChip } from '../../utils/samples/chips';
    import { displayWordsForTimings, timingsMatchRef } from '../../utils/samples/word-timing';
    import { contiguousWordDraft } from '../../utils/samples/word-timing-draft';
    import {
        chapterIndexKey,
        flashSegmentIndices,
        targetSegmentIndex,
    } from '../../stores/navigation';
    import {
        activeWordCursor,
        isMainAudioPlaying,
        playingSegmentIndex,
        segListElement,
        segPort,
        stagedPlayheadWindow,
    } from '../../stores/playback';
    import { valUiOpenCategory } from '../../stores/validation';
    import type {
        MergeHighlight,
        SegCanvas,
        SplitHighlight,
        TrimHighlight,
    } from '../../types/segments-waveform';
    import { SEG_ROW_CANVAS_HEIGHT, SEG_ROW_CANVAS_WIDTH } from '../../utils/constants';
    import { jumpToSegment } from '../../utils/data/navigation-actions';
    import {
        _addVerseMarkers,
        dkTextForRef,
        formatRef,
        formatTimeMs,
        isCrossVerse,
    } from '../../utils/data/references';
    import { deleteSegment } from '../../utils/edit/delete';
    import { enterEditWithBuffer } from '../../utils/edit/enter';
    import { flagSegment } from '../../utils/edit/flag';
    import { mergeAdjacent } from '../../utils/edit/merge';
    import { beginRefEdit } from '../../utils/edit/reference';
    import { playFromSegment, startWordTimingPreview, updateWordTimingPreview, stopWordTimingPreview, onSegPlayClick } from '../../utils/playback/playback';
    import type { PreviewPlaybackContext } from '../../utils/playback/preview';
    import { deregisterRow, registerRow } from '../../utils/playback/row-registry';
    import { wrapCbrSrcIfBySurah } from '../../utils/playback/source';
    import { warmSeg } from '../../utils/playback/warmup';
    import { getConfClass } from '../../utils/validation/conf-class';
    import { _ensureWaveformObserver } from '../../utils/waveform/utils';
    import { resetHistoryLoader } from '../../utils/history/loader';
    import WordTimingEditor from '../edit/WordTimingEditor.svelte';
    import ReferenceEditor from '../edit/ReferenceEditor.svelte';
    import SplitPanel from '../edit/SplitPanel.svelte';
    import TrimPanel from '../edit/TrimPanel.svelte';
    import TimeRange from './TimeRange.svelte';

    // ---- Required ----
    export let seg: Segment;
    // ---- Optional rendering flags ----
    export let readOnly: boolean = false;
    export let showChapter: boolean = false;
    export let showPlayBtn: boolean = true;
    export let showGotoBtn: boolean = false;
    export let isContext: boolean = false;
    export let contextLabel: string = '';
    export let isNeighbour: boolean = false;
    /** Provisioning slot — overlay applied in history mode. */
    export let splitHL: SplitHighlight | null = null;
    /** Provisioning slot — overlay applied in history mode. */
    export let trimHL: TrimHighlight | null = null;
    /** Provisioning slot — overlay applied in history mode. */
    export let mergeHL: MergeHighlight | null = null;
    /** Provisioning slot — marks changed fields in the card. The `body` flag
     *  was retired alongside `display_text`: row body is now derived from
     *  `matched_ref`, so a body change always implies a `ref` change. */
    export let changedFields: Set<'ref' | 'duration' | 'conf'> | null = null;
    /** `history` mode = vertical layout (waveform above text). */
    export let mode: 'normal' | 'history' = 'normal';
    /** Fallback chapter when `seg.chapter` is null — only used for dirty lookup. */
    export let fallbackChapter: number = 0;
    /**
     * Which DOM context is rendering this row. Default `accordion` — the
     * most common non-readOnly placement (validation cards, ErrorCard
     * contexts). Only `main` reacts to `$targetSegmentIndex` scroll, so
     * a "Go to" or verse-pill jump always targets the main list even when
     * an identical row is mounted in an accordion twin. Only `main` also
     * claims a programmatic edit session (split-chain handoff, auto-fix,
     * keyboard `E`) when `editingMountId` is null. `history` / `preview`
     * rows are always readOnly and never participate in edit or scroll.
     */
    export let instanceRole: 'main' | 'accordion' | 'history' | 'preview' = 'accordion';
    /**
     * Validation category that initiated this row's rendering (e.g.
     * 'low_confidence', 'cross_verse'). Set by accordion cards on the
     * resolved (non-context) SegmentRow so every edit op started from
     * this row is tagged with its originating category — the save flow
     * then auto-adds the category to `ignored_categories` on commit so
     * the issue disappears from the accordion post-save. Context rows
     * (isContext=true) leave this null: editing a neighbour must not
     * auto-ignore the issue for the original seg.
     */
    export let validationCategory: string | null = null;
    /**
     * Optional preview-playback context. When supplied AND `readOnly`,
     * the play button becomes a wired toggle that drives the panel-owned
     * AudioRange (see `utils/playback/preview.ts`). Without this, readOnly
     * rows render an inert play button — that's the historical default for
     * accordion validation rows that share uids with main-list twins. Only
     * SavePreview and HistoryPanel pass a context.
     */
    export let previewCtx: PreviewPlaybackContext | undefined = undefined;
    /**
     * History op_id this row belongs to. Set only by History views
     * (HistoryOp / SplitChainRow). When present and `previewCtx` is wired,
     * peaks computed for this row on play are persisted server-side under
     * this op_id — so future sessions render the same row without
     * re-computing. Live-edit and SavePreview rows leave this null.
     */
    export let opId: string | null = null;
    /**
     * Rendered sibling list of the accordion card mounting this row. Set by
     * `MissingVersesCard` / `MissingWordsCard` / `GenericIssueCard` to the
     * full ordered list of segments they render — the playback layer uses
     * it to prefetch the *next* sibling's clip URL by list position when
     * this row plays. Cross-chapter siblings are supported (validation
     * panels with `chapter=null`); the per-reciter VBR map decides whether
     * each sibling routes through the clip endpoint or the chapter URL.
     * Main-list and history/preview rows pass null.
     */
    export let accordionSiblings: Segment[] | null = null;
    /**
     * Card-level action callbacks, set ONLY by accordion cards on their main
     * (non-context) member rows. Forwarded into the active-row action bundle so
     * the keyboard shortcuts L (ignore) / F (auto-fill) / C (toggle context) /
     * 1–2 (label the WASL/WAQF boundary under this piece) can act on the
     * focused card. `null` on every other placement — `onCardSetWasl` is also
     * null on a row with no boundary below or above it.
     */
    export let onCardIgnore: (() => void) | null = null;
    export let onCardAutofill: (() => void) | null = null;
    export let onCardToggleContext: (() => void) | null = null;
    export let onCardSetWasl: ((_value: boolean) => void) | null = null;
    /**
     * End of the contiguous piece group this row belongs to (a card's
     * multi-piece split group). Extends this row's bounded play so the audio
     * runs through the whole group and the cursor walks from piece to piece,
     * instead of cutting at every piece edge. Null everywhere else.
     */
    export let groupEndMs: number | null = null;
    /**
     * Staged piece of a pre-applied cross-verse split that is not in the
     * store yet (see `utils/validation/staged-split.ts`). The row looks and
     * acts like any other, drawing its own waveform from the piece's time
     * range (`data-hist-*`); the differences are that it does not register
     * in the playback row registry (its index is still the parent's) and
     * every edit action first calls `onStagedActivate` — the card commits
     * the split — then runs against the now-real piece (same uid, same
     * mounted row).
     */
    export let staged: boolean = false;
    export let onStagedActivate: (() => void) | null = null;

    /** Materialise the staged split before an edit action, and let the
     *  props settle so `seg` is the store-backed piece. */
    async function activate(): Promise<void> {
        if (!staged || !onStagedActivate) return;
        onStagedActivate();
        await tick();
    }

    // Apply history-mode highlight descriptors to the underlying canvas element
    // so the IntersectionObserver draw pipeline (segments/waveform/index.ts +
    // draw.ts) can read them via the SegCanvas ad-hoc fields. `canvasEl` is
    // bound below; these statements run after it is assigned and re-run when
    // any prop changes.
    $: if (canvasEl) {
        const c = canvasEl as SegCanvas;
        c._splitHL = splitHL ?? undefined;
        c._trimHL = trimHL ?? undefined;
        c._mergeHL = mergeHL ?? undefined;
    }

    // True only when this specific mounted row is the editing target. The
    // editing row is identified by (segment_uid) AND (initiating mountId).
    // When `editingMountId` is null the edit was started programmatically
    // (split-chain handoff, auto-fix, keyboard E) — the main-list instance
    // claims it so edit panels always appear in the main list, not on an
    // accordion twin. readOnly sites (history, save preview) share uids
    // with main-list rows and must never participate.
    $: isInitiatingEditRow = !readOnly
        && !!seg.segment_uid
        && $editingSegUid === seg.segment_uid
        && ($editingMountId === _mountId
            || ($editingMountId === null && instanceRole === 'main'));

    // Publish canvas to `editCanvas` store whenever THIS mounted row is the
    // active edit target. Replaces the legacy `_getEditCanvas()` document-
    // wide DOM query. UID alone is ambiguous across twin mounts; gating on
    // the initiating mountId keeps the accordion twin from clobbering the
    // main-list row's canvas (or vice-versa) after an edit starts.
    $: {
        if (isInitiatingEditRow && canvasEl) {
            setEditCanvas(canvasEl as SegCanvas);
        }
    }

    // True only for the one live row currently being edited (any mode).
    // Drives the conditional mount of TrimPanel / SplitPanel / ReferenceEditor
    // inside the row, the `.seg-edit-target` class binding, and the hiding of
    // the row control footer during persistent drag modes.
    $: isEditingThisRow = isInitiatingEditRow && $editMode !== null;
    $: editSegCanvas = canvasEl as SegCanvas | undefined;

    // Derived values. Seg-derived reactives also subscribe to $segAllData via
    // `segStoreTick` so they re-fire when refreshSegInStore bumps the store —
    // validation-card sites derive resolvedSeg from the store, so their seg
    // prop points to the refreshed object only after the store tick.
    $: segStoreTick = $segAllData;
    $: chapterForDirty = seg.chapter ?? fallbackChapter;
    $: dirty = (void $dirtyTick, !readOnly && isIndexDirty(chapterForDirty, seg.index));
    $: confClass = (void segStoreTick, getConfClass(seg));
    $: durTitle = (void segStoreTick, `${formatTimeMs(seg.time_start)} \u2013 ${formatTimeMs(seg.time_end)}`);
    $: adj = !readOnly && !isContext
        ? getAdjacentSegments(seg.chapter ?? 0, seg.index)
        : { prev: null, next: null };
    $: mergePrevDisabled = !adj.prev
        || (!!adj.prev?.audio_url && !!seg.audio_url && adj.prev.audio_url !== seg.audio_url);
    $: mergePrevTitle = tr($localeStore, !adj.prev
        ? m.segments_row_no_prev_merge_title()
        : (adj.prev.audio_url && seg.audio_url && adj.prev.audio_url !== seg.audio_url)
        ? m.segments_row_merge_diff_audio_title()
        : '');
    $: mergeNextDisabled = !adj.next
        || (!!adj.next?.audio_url && !!seg.audio_url && adj.next.audio_url !== seg.audio_url);
    $: mergeNextTitle = tr($localeStore, !adj.next
        ? m.segments_row_no_next_merge_title()
        : (adj.next.audio_url && seg.audio_url && adj.next.audio_url !== seg.audio_url)
        ? m.segments_row_merge_diff_audio_title()
        : '');
    // Live ref-edit preview ref. ReferenceEditor dispatches the normalized ref
    // on every keystroke; the body re-renders synchronously through the same
    // `dkTextForRef` lookup the persisted row uses. `null` = no preview /
    // invalid input — body falls back to `seg.matched_ref`.
    let previewState: { ref: string } | null = null;
    $: if (!isEditingThisRow || $editMode !== 'reference') {
        previewState = null;
    }

    // History-mode changed-field markers.
    $: changedRef = !!changedFields?.has('ref');
    $: changedDur = !!changedFields?.has('duration');
    $: changedConf = !!changedFields?.has('conf');
    $: bodyRef = previewState?.ref ?? seg.matched_ref;
    $: bodyText = (() => {
        const text = dkTextForRef(bodyRef, $quranRefs?.dk_words, $quranRefs?.verse_word_counts);
        if (!text) {
            // Special segs (Basmala/Isti'adha/Amin/Takbir/...) carry their
            // Arabic text on the snapshot but have non-Quran-ref matched_refs,
            // so the dk_words lookup is empty. Fall back to matched_text.
            if (seg.matched_text) return seg.matched_text;
            return seg.matched_ref ? tr($localeStore, m.segments_row_no_text_fallback()) : tr($localeStore, m.segments_row_no_match_fallback());
        }
        return _addVerseMarkers(text, bodyRef, $quranRefs?.verse_word_counts) || text;
    })();
    $: reviewWordTimings = $isSampleMode && timingsMatchRef(bodyRef, seg.word_timings)
        ? (seg.word_timings ?? [])
        : [];
    $: reviewDisplayWords = displayWordsForTimings(
        reviewWordTimings,
        bodyText,
        $quranRefs?.dk_words,
        $quranRefs?.verse_word_counts,
        $quranRefs?.verse_marker_prefix ?? '۝',
    );
    let wordEditing = false;
    let wordDraft: { start_ms: number; end_ms: number }[] = [];
    let wordLockedIndex: number | null = null;
    let wordSaving = false;
    let wordError = '';
    let wordHasChanges = false;
    $: wordHasChanges = wordDraft.some((word, index) =>
        word.start_ms !== reviewWordTimings[index]?.start_ms || word.end_ms !== reviewWordTimings[index]?.end_ms);
    $: wordEditWidth = Math.min(8000, Math.max(900, reviewWordTimings.length * 150, (seg.time_end - seg.time_start) * 0.1));
    async function toggleWordEditor(): Promise<void> {
        if (!wordEditing && get(editMode)) return;
        if (!wordEditing && isDirty()) {
            pushToast({ kind: 'warn', text: tr($localeStore, m.segments_word_edit_pending()) });
            return;
        }
        wordEditing = !wordEditing;
        wordError = '';
        wordLockedIndex = null;
        if (wordEditing) {
            wordDraft = contiguousWordDraft(reviewWordTimings);
            startWordTimingPreview(seg, rowChapter, seg.time_start, seg.time_end);
        } else {
            stopWordTimingPreview(seg.segment_uid ?? '');
        }
        await tick();
        if (canvasEl) {
            canvasEl.setAttribute('data-needs-waveform', '');
            _ensureWaveformObserver().observe(canvasEl);
        }
    }
    function onWordDraftChange(boundaries: { start_ms: number; end_ms: number }[]): void {
        const previousLocked = wordLockedIndex === null ? null : wordDraft[wordLockedIndex];
        wordDraft = boundaries;
        if (wordLockedIndex !== null) {
            const locked = boundaries[wordLockedIndex]!;
            if (!previousLocked || previousLocked.start_ms !== locked.start_ms || previousLocked.end_ms !== locked.end_ms) {
                updateWordTimingPreview(seg.segment_uid ?? '', locked.start_ms, locked.end_ms);
            }
        }
    }
    function seekWordPreview(timeMs: number): void {
        wordLockedIndex = null;
        startWordTimingPreview(seg, rowChapter, seg.time_start, seg.time_end, timeMs);
    }
    function toggleWordLock(index: number): void {
        if (wordLockedIndex === index) {
            wordLockedIndex = null;
            startWordTimingPreview(seg, rowChapter, seg.time_start, seg.time_end);
        } else {
            wordLockedIndex = index;
            startWordTimingPreview(seg, rowChapter, wordDraft[index]!.start_ms, wordDraft[index]!.end_ms);
        }
    }
    async function applyWordTimings(): Promise<void> {
        if (wordSaving) return;
        wordSaving = true;
        wordError = '';
        try { await saveWordTimings(wordDraft); }
        catch (error) { wordError = error instanceof Error ? error.message : tr($localeStore, m.segments_word_edit_error()); }
        finally { wordSaving = false; }
    }
    async function saveWordTimings(boundaries: { start_ms: number; end_ms: number }[]): Promise<boolean> {
        if (!seg.segment_uid || isDirty()) throw new Error(tr($localeStore, m.segments_word_edit_pending()));
        const reciter = get(selectedReciter);
        const expected = reviewWordTimings.map(w => ({ start_ms: w.start_ms, end_ms: w.end_ms }));
        const response = await fetch(`/api/seg/save/${encodeURIComponent(reciter)}/${rowChapter}`, {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ segments: [], operations: [{
                op_id: crypto.randomUUID(), op_type: 'edit_word_timings', type: 'editWordTimings',
                command: { type: 'editWordTimings', segmentUid: seg.segment_uid, expected, boundaries },
            }] }),
        });
        if (!response.ok) {
            const body = await response.json().catch(() => null) as { error?: string } | null;
            throw new Error(body?.error || `Save failed (${response.status}).`);
        }
        seg.word_timings = reviewWordTimings.map((w, i) => ({ ...w, ...boundaries[i]! }));
        refreshSegInStore(seg);
        resetHistoryLoader();
        wordEditing = false;
        stopWordTimingPreview(seg.segment_uid ?? '');
        await tick();
        if (canvasEl) {
            canvasEl.setAttribute('data-needs-waveform', '');
            _ensureWaveformObserver().observe(canvasEl);
        }
        pushToast({ kind: 'success', text: tr($localeStore, m.segments_word_edit_saved()) });
        return true;
    }
    $: confText = (void segStoreTick, seg.matched_ref ? ((seg.confidence ?? 0) * 100).toFixed(1) + '%' : tr($localeStore, m.segments_row_conf_fail_label()));
    $: indexLabel = (showChapter && seg.chapter != null)
        ? `${seg.chapter}:#${seg.index}`
        : `#${seg.index}`;

    $: playButtonTitle = tr($localeStore, m.segments_row_play_button_title());
    // Sample mode: the three review signals live on the row, not in the accordion.
    $: rowChips = (void segStoreTick, $isSampleMode && mode === 'normal' && instanceRole === 'main'
        ? deriveRowChips(seg, $missingWordsSegKeys, rowChapter)
        : []) as RowChip[];
    $: chipLabel = (chip: RowChip) => tr(
        $localeStore,
        chip === 'low_conf'
            ? m.segments_chip_low_conf()
            : chip === 'repetition' ? m.segments_chip_repetition() : m.segments_chip_missing_words(),
    );
    $: gotoButtonLabel = tr($localeStore, m.segments_row_goto_button());
    $: flagAriaLabel = tr($localeStore, !canEditFlag
        ? m.segments_row_flag_view_other_aria_label()
        : isFlagged ? m.segments_row_flag_view_mine_aria_label() : m.segments_row_flag_new_aria_label());
    $: flagButtonTitle = tr($localeStore, m.segments_row_flag_button_title());
    $: flagTipLabel = tr($localeStore, m.segments_row_flag_tip_label());
    $: adjustButtonLabel = tr($localeStore, m.segments_row_adjust_button());
    $: mergeUpButtonLabel = tr($localeStore, m.segments_row_merge_up_button());
    $: deleteButtonLabel = tr($localeStore, m.segments_row_delete_button());
    $: splitButtonLabel = tr($localeStore, isAutoSplit ? m.segments_row_auto_split_button() : m.segments_row_split_button());
    $: mergeDownButtonLabel = tr($localeStore, m.segments_row_merge_down_button());
    $: editRefButtonLabel = tr($localeStore, m.segments_row_edit_ref_button());
    $: flagPlaceholder = tr($localeStore, m.segments_row_flag_placeholder());
    $: flagHint = tr($localeStore, isFlagged ? m.segments_row_flag_hint_clear() : m.segments_row_flag_hint_required());
    $: flagApplyLabel = tr($localeStore, isFlagged && flagDraft.trim().length === 0
        ? m.segments_row_flag_remove_button()
        : isFlagged ? m.segments_row_flag_update_button() : m.segments_row_flag_apply_button());

    // ---------------------------------------------------------------------
    // Playback highlight + jump target (store-driven)
    // ---------------------------------------------------------------------
    // readOnly rows (history view, save preview) share seg.index with rows in
    // the main list. Guarding on !readOnly keeps them from lighting up when
    // the main-list row for the same index is playing or flashing. Validation
    // accordion rows (isContext=true) are NOT readOnly — they MUST light up
    // in sync with the main-list twin for the same segment.
    //
    // Active-pair match: both chapter AND index must match. The validation
    // panel can be mounted with chapter=null (all chapters), so same-index
    // rows in other chapters must not collide.
    $: rowChapter = seg.chapter ?? fallbackChapter;
    /** Stable identity for this preview snapshot row. Snapshots can share
     *  segment_uid with the live row but their (time_start, time_end) are
     *  immutable in this panel, so they make a unique key per ctx. */
    $: rowPreviewUid = `${rowChapter}:${seg.index}:${seg.time_start}:${seg.time_end}`;
    $: previewActive = readOnly && !!previewCtx && _previewActiveUid === rowPreviewUid;
    $: previewPlaying = readOnly && !!previewCtx && _previewPlayingUid === rowPreviewUid;
    // Staged pieces share the parent's (chapter, index): only the piece whose
    // window holds the playhead is the playing one.
    $: cursorInPiece = !staged
        || (!!$stagedPlayheadWindow
            && $stagedPlayheadWindow.start === seg.time_start
            && $stagedPlayheadWindow.end === seg.time_end);
    $: segIsCurrent = !readOnly
        && !!$playingSegmentIndex
        && $playingSegmentIndex.chapter === rowChapter
        && $playingSegmentIndex.index === seg.index;
    $: isPlaying = previewActive || (segIsCurrent && cursorInPiece);
    // `stagedPlayheadWindow` is a live rAF signal — it goes null the moment
    // audio stops, so `cursorInPiece` alone can't say which piece the user is
    // ON once playback ends. Latch the last piece the cursor was inside (only
    // ever one at a time, since a non-null window sets every other piece's
    // latch false) and use that for the keyboard registry below; the visual
    // playing state keeps the live signal.
    let _stagedCursorLatched = false;
    $: if (staged && $stagedPlayheadWindow) {
        _stagedCursorLatched = $stagedPlayheadWindow.start === seg.time_start
            && $stagedPlayheadWindow.end === seg.time_end;
    }
    // flashSegmentIndices is keyed by "chapter:index" — both the main-list
    // and accordion twin for the correctly-matched pair still light up, but
    // a same-index row in a different chapter (validation panel with
    // chapter=null) no longer collides.
    $: rowFlashKey = chapterIndexKey(rowChapter, seg.index);
    $: isFlashing = !readOnly && $flashSegmentIndices.has(rowFlashKey);
    $: highlighted = isPlaying || isFlashing;
    $: playGlyph = (previewPlaying || (isPlaying && $isMainAudioPlaying))
        ? '\u25A0'
        : '\u25B6';

    // Scroll into view when jump target matches, then clear the store so the
    // next write re-fires reliably. Only the main-list instance reacts —
    // accordion / history / preview twins would otherwise race the main-list
    // row (the accordion's short scroll container usually wins), yanking
    // focus onto the accordion instead of the main list the user expects.
    // `rowEl` is bound below; wait for it.
    $: if (
        instanceRole === 'main'
        && !readOnly
        && rowEl
        && $targetSegmentIndex
        && $targetSegmentIndex.chapter === rowChapter
        && $targetSegmentIndex.index === seg.index
    ) {
        rowEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        targetSegmentIndex.set(null);
    }

    // Post-split ref-edit chain handoff is owned by
    // `utils/edit/reference.ts::commitRefEdit` now — it reads-and-clears
    // `pendingChainTarget` after clearEdit() and calls beginRefEdit
    // directly. No reactive store indirection, no subscriber race. The
    // accordion path routes via `mountId=null` which the secondHalf's
    // main-list row claims through the `instanceRole === 'main'` fallback.

    // ---------------------------------------------------------------------
    // Preview-mode subscription (SavePreview / HistoryPanel only)
    // ---------------------------------------------------------------------
    // `previewCtx.activeSeg` / `playingSeg` are svelte stores, but we can't
    // use the `$` prefix on a prop-typed store directly — manual subscribe
    // with a teardown. Re-subscribes if the prop swaps mid-lifetime
    // (defensive; in practice the parent panel mounts the ctx once).
    let _previewActiveUid: string | null = null;
    let _previewPlayingUid: string | null = null;
    let _unsubPreviewActive: (() => void) | null = null;
    let _unsubPreviewPlaying: (() => void) | null = null;
    $: {
        _unsubPreviewActive?.();
        _unsubPreviewPlaying?.();
        _unsubPreviewActive = null;
        _unsubPreviewPlaying = null;
        _previewActiveUid = null;
        _previewPlayingUid = null;
        if (previewCtx) {
            _unsubPreviewActive = previewCtx.activeSeg.subscribe((v) => {
                _previewActiveUid = v?.uid ?? null;
            });
            _unsubPreviewPlaying = previewCtx.playingSeg.subscribe((v) => {
                _previewPlayingUid = v?.uid ?? null;
            });
        }
    }

    // ---------------------------------------------------------------------
    // Waveform observer registration
    // ---------------------------------------------------------------------
    let canvasEl: HTMLCanvasElement | undefined;
    let rowEl: HTMLElement;

    // Unique per-mount identifier — disambiguates twin deregistration so the
    // main-list row and an accordion row for the same segment can coexist
    // and unmount independently without clobbering each other's entry.
    const _mountId = Symbol('seg-row');

    // Track the (chapter, index) we most recently registered under so
    // structural mutations (split/merge/delete reindex) can deregister
    // under the OLD key before re-registering under the new one. Without
    // this, a shifted row would leave a stale entry in the registry keyed
    // to its pre-mutation index.
    let _prevRegChapter: number | null = null;
    let _prevRegIdx: number | null = null;
    let _prevRegWindow: string | null = null;

    onMount(() => {
        // Register every non-readOnly row — both the main-list and any
        // accordion twin. drawActivePlayhead iterates all entries for the
        // playing (chapter, index) so both instances render a synchronized
        // playhead. Keyed by (chapter, index) so same-index rows in different
        // chapters don't collide (validation panel with chapter=null).
        //
        // Staged pre-split pieces register too, carrying themselves as the
        // entry's `segOverride`. They share the parent's (chapter, index), so
        // without that the draw layer would resolve the PARENT's time window
        // from the store and paint the cursor at the wrong x — which is why a
        // cross-verse card showed no playhead until its split was dispatched.
        if (!readOnly && rowEl) {
            registerRow(rowChapter, seg.index, rowEl, canvasEl, _mountId, instanceRole, staged ? seg : null);
            _prevRegChapter = rowChapter;
            _prevRegIdx = seg.index;
            _prevRegWindow = staged ? `${seg.time_start}:${seg.time_end}` : null;
        }
        // Preview-mode registration: snapshot rows in SavePreview / HistoryPanel
        // register with the panel's PreviewPlaybackContext so the play button
        // can drive playback and the rAF onTick can target this row's canvas.
        if (readOnly && previewCtx && canvasEl) {
            const audioUrl = seg.audio_url
                ?? get(segAllData)?.audio_by_chapter?.[String(rowChapter)]
                ?? '';
            if (audioUrl) {
                // Split-leaf history rows render the parent's union peak
                // (via splitHL.wfStart/wfEnd substitution in the observer),
                // but audio playback only spans the leaf slice. Pass both
                // ranges so the preview ctx can drive the playhead against
                // the wider visual range while AudioRange stops at the
                // narrower playback range.
                const wfStartMs = splitHL?.wfStart ?? seg.time_start;
                const wfEndMs = splitHL?.wfEnd ?? seg.time_end;
                previewCtx.registerRow(
                    rowPreviewUid,
                    canvasEl,
                    audioUrl,
                    seg.time_start,
                    seg.time_end,
                    opId ?? undefined,
                    wfStartMs,
                    wfEndMs,
                    rowChapter,
                );
            }
        }
        if (!canvasEl) return;
        // Capture the canvas reference for the cleanup closure. `canvasEl`
        // is a `bind:this` binding that Svelte may null out before the
        // onMount destructor runs (depending on unmount path), which made
        // `observer.unobserve(canvasEl!)` throw "parameter 1 is not of
        // type 'Element'" when an accordion card unmounted.
        const observedCanvas = canvasEl;
        const observer = _ensureWaveformObserver();
        observer.observe(observedCanvas);
        return () => {
            observer.unobserve(observedCanvas);
        };
    });

    // Re-register under the new (chapter, index) key whenever seg.index or
    // rowChapter shifts (split/merge/delete reindex). Without this, the
    // registry would still point at the pre-mutation key, and
    // drawActivePlayhead would draw on the wrong row (or miss this row
    // entirely). Fires after onMount completes — the `_prevRegChapter !==
    // null` guard prevents double-registration with the initial mount.
    //
    // Staged pieces keep a stable (chapter, index) but their time window is
    // what identifies them, so re-register when that shifts as well.
    $: _regWindow = staged ? `${seg.time_start}:${seg.time_end}` : null;
    $: if (
        rowEl
        && !readOnly
        && (rowChapter !== _prevRegChapter || seg.index !== _prevRegIdx || _regWindow !== _prevRegWindow)
    ) {
        if (_prevRegChapter !== null && _prevRegIdx !== null) {
            deregisterRow(_prevRegChapter, _prevRegIdx, _mountId);
        }
        registerRow(rowChapter, seg.index, rowEl, canvasEl, _mountId, instanceRole, staged ? seg : null);
        _prevRegChapter = rowChapter;
        _prevRegIdx = seg.index;
        _prevRegWindow = _regWindow;
    }

    onDestroy(() => {
        if (wordEditing) stopWordTimingPreview(seg.segment_uid ?? '');
        // Use the stored prev values rather than the current (potentially
        // shifted) seg.index — otherwise a row that's been reindexed since
        // mount would deregister under the wrong key, leaving a ghost entry.
        if (!readOnly && _prevRegChapter !== null && _prevRegIdx !== null) {
            deregisterRow(_prevRegChapter, _prevRegIdx, _mountId);
        }
        if (readOnly && previewCtx) {
            previewCtx.deregisterRow(rowPreviewUid);
        }
        _unsubPreviewActive?.();
        _unsubPreviewPlaying?.();
        clearRowActions(_mountId);
        if (_hoverWarmTimer) {
            clearTimeout(_hoverWarmTimer);
            _hoverWarmTimer = null;
        }
    });

    // ---------------------------------------------------------------------
    // Per-button handlers (replace delegated click router for #seg-list rows)
    // ---------------------------------------------------------------------

    function onPreviewPlayClick(e: MouseEvent): void {
        e.stopPropagation();
        if (!previewCtx) return;
        previewCtx.toggle(rowPreviewUid);
    }

    // Hover-warm — fires ~80 ms after the cursor enters the play button.
    // Two warmups in parallel:
    //   1. `shadowPrewarm(cbrSrc)` fills the browser HTTP cache for the
    //      chapter MP3 on a hidden <audio>. Covers cross-chapter clicks
    //      that the panel-level next-card prediction missed (user jumped
    //      to a non-sequential card or played a row in a different
    //      chapter from the main list). When user clicks play, the
    //      primary <audio>'s el.load() hits the warm cache → fast canplay.
    //   2. `warmSeg(seg)` fetches a 64 KB Range around the seg's byte
    //      offset. Primes the server-side OS page cache for the mid-file
    //      seek the audio element will issue when it seeks to seg.time_start.
    //      Hides the "readyState=4 but TTFB 600 ms" case where the chapter
    //      is loaded but the seek target byte range isn't.
    // 80 ms (down from 150) catches more "hover-then-click-fast" cases.
    // Mouseleave cancels the timer if user moves off without clicking.
    let _hoverWarmTimer: ReturnType<typeof setTimeout> | null = null;
    function onPlayHover(): void {
        if (readOnly || _hoverWarmTimer) return;
        _hoverWarmTimer = setTimeout(() => {
            _hoverWarmTimer = null;
            const reciter = get(selectedReciter);
            if (!reciter) return;
            // Server-side byte-Range warmup at seg's byte offset.
            warmSeg(seg, reciter);
            // Browser HTTP cache warmup for the chapter MP3 (cross-chapter
            // jumps). Skip VBR — clip URL is per-seg and shadow can't
            // precache that.
            const audioUrl = seg.audio_url
                ?? get(segAllData)?.audio_by_chapter?.[String(seg.chapter ?? rowChapter)]
                ?? '';
            const ch = seg.chapter ?? rowChapter;
            const isVbr = ch != null && ($segAllData?.reciter_vbr_chapters ?? []).includes(ch as number);
            if (audioUrl && !isVbr) {
                shadowPrewarm(wrapCbrSrcIfBySurah(audioUrl, reciter));
            }
        }, 80);
    }
    function onPlayLeave(): void {
        if (_hoverWarmTimer) {
            clearTimeout(_hoverWarmTimer);
            _hoverWarmTimer = null;
        }
    }

    /** The piece descriptor for a play originating on this row. Staged pieces
     *  share the parent's (chapter, index), so playback needs the window to
     *  bound the range and the uid to move the accordion nav cursor onto this
     *  piece. Non-staged rows are whole segments — null keeps the old path. */
    function _piecePlayArg(): { uid: string; startMs: number; endMs: number } | null {
        if (!staged) return null;
        const uid = seg.segment_uid ?? '';
        if (!uid) return null;
        return { uid, startMs: seg.time_start, endMs: seg.time_end };
    }

    function onPlayClick(e: MouseEvent): void {
        e.stopPropagation();
        if (readOnly) return;
        const idx = seg.index;
        const chapter = seg.chapter ?? fallbackChapter;
        // Use the full (chapter, index) active pair so a context row for a
        // different chapter with the same index doesn't mistake itself for
        // the playing one and pause unrelated playback.
        const active = get(playingSegmentIndex);
        const isSelfPlaying = !!active
            && active.chapter === chapter
            && active.index === idx
            && cursorInPiece
            && !segPort.paused;
        if (isSelfPlaying) {
            segPort.pause();
        } else {
            // Accordion-mounted rows are self-contained playback surfaces.
            // Marking the play as accordion-origin keeps the main list from
            // autoscrolling when the same chapter is open, and keeps the
            // policy gate from advancing into the main display's chapter
            // when global autoplay is on.
            // A staged piece plays as its own segment: bounded to its own
            // window, so autoplay advances piece → piece like any other
            // segment instead of running through the whole parent.
            playFromSegment(idx, chapter, staged ? seg.time_start : undefined, {
                isAccordionPlay: instanceRole !== 'main',
                accordionSiblings,
                piece: _piecePlayArg(),
                groupEndMs,
            });
        }
    }

    function onGotoClick(e: MouseEvent): void {
        e.stopPropagation();
        void doGoto();
    }

    async function doGoto(): Promise<void> {
        const filters = get(activeFilters);
        if (filters.some(f => f.value !== null)) {
            const listEl = get(segListElement);
            savedFilterView.set({
                filters: JSON.parse(JSON.stringify(filters)),
                chapter: get(selectedChapter),
                verse: get(selectedVerse),
                scrollTop: listEl?.scrollTop ?? 0,
            });
        }
        // "Go to" is the explicit "render this chapter's cards" gesture —
        // collapse any open accordion + clear the programmatic picker-
        // display override so the picker tracks `selectedChapter` once
        // jumpToSegment swaps it.
        valUiOpenCategory.set(null);
        clearAccordionPin();
        pickerDisplayChapter.set(null);
        const targetChapter = seg.chapter ?? 0;
        await jumpToSegment(targetChapter, seg.index);
        // Sync audio to the target seg. `loadChapterData` (inside
        // `jumpToSegment` when the chapter changed) tore down the prior
        // playback — start a fresh main-list play so the user lands on the
        // segment they navigated to, with seek + play, not paused at byte 0.
        playFromSegment(seg.index, targetChapter);
    }

    function onAdjustClick(e: MouseEvent): void {
        e.stopPropagation();
        void activate().then(doAdjust);
    }

    function doAdjust(): void {
        enterEditWithBuffer(seg, rowEl, 'trim', validationCategory, _mountId, rowChapter);
    }

    /** Cross-verse, repetitions and hidden-pause accordions show *Auto Split*
     *  on every candidate row. Cursor positions come from offline-precomputed
     *  sidecars (`auto_split_v1.json` / `hidden_pause_v1.json`, keyed by
     *  segment_uid). The whole map is preloaded once on accordion open
     *  (stores/auto-split.ts), so the click below is a zero-network O(1)
     *  lookup; a miss (uid not in map) degrades to manual split, same UX as a
     *  non-candidate row. */
    $: isAutoSplitCandidate = (validationCategory === 'cross_verse' && isCrossVerse(seg.matched_ref))
        || (validationCategory === 'repetitions' && !!(seg as any).wrap_word_ranges)
        || validationCategory === 'hidden_pause';
    $: isAutoSplit = isAutoSplitCandidate && !!seg.segment_uid;

    function onSplitClick(e: MouseEvent): void {
        e.stopPropagation();
        void activate().then(doSplit);
    }

    async function doSplit(): Promise<void> {
        let initialSplits: number[] | null = null;
        let initialRefs: string[] | null = null;
        if (isAutoSplit) {
            const reciter = get(selectedReciter);
            if (reciter && seg.segment_uid) {
                // Fast path: the map is preloaded on accordion open, so this
                // awaits an already-resolved (or in-flight, deduped) promise —
                // no per-click round trip. A miss is "uid not in map", resolved
                // without any server call.
                const map = await ensureAutoSplitMap(reciter);
                if (map) {
                    const hit = map[seg.segment_uid] ?? null;
                    initialSplits = hit?.cursors ?? null;
                    initialRefs = hit?.refs ?? null;
                } else {
                    // Preload failed (network error): fall back to the per-uid
                    // POST so a transient GET failure still yields cursors.
                    const resp = await fetchJsonOrNull<{
                        cursors: number[] | null;
                        refs: string[] | null;
                    }>(
                        `/api/seg/auto-split/${encodeURIComponent(reciter)}`,
                        {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                segment_uid: seg.segment_uid,
                                chapter: rowChapter,
                            }),
                        },
                    );
                    initialSplits = resp?.cursors ?? null;
                    initialRefs = resp?.refs ?? null;
                }
                // cursors === null is the offline-precompute miss case;
                // enterEditWithBuffer with nulls degrades to manual split.
            }
        }
        enterEditWithBuffer(
            seg, rowEl, 'split', validationCategory, _mountId, rowChapter,
            initialSplits, initialRefs,
        );
    }

    function onMergePrevClick(e: MouseEvent): void {
        e.stopPropagation();
        void activate().then(() => mergeAdjacent(seg, 'prev', validationCategory, _mountId));
    }

    function onMergeNextClick(e: MouseEvent): void {
        e.stopPropagation();
        void activate().then(() => mergeAdjacent(seg, 'next', validationCategory, _mountId));
    }

    function onDeleteClick(e: MouseEvent): void {
        e.stopPropagation();
        void activate().then(doDelete);
    }

    function doDelete(): void {
        deleteSegment(seg, rowEl, validationCategory, _mountId);
    }

    function onEditRefClick(e: MouseEvent): void {
        e.stopPropagation();
        void activate().then(doEditRef);
    }

    function doEditRef(): void {
        beginRefEdit(seg, validationCategory, _mountId);
    }

    // ---------------------------------------------------------------------
    // Active-row action registry — publish this row's edit actions while it is
    // the "primary" target (playing, or the main-list current segment when
    // paused), so global keyboard shortcuts (A / S / E / G / L / F / C / 1 / 2)
    // act on it. Accordion cards forward their card-level callbacks via the
    // onCard* props. Only one row is primary at a time (the main list is
    // hidden while an accordion is open).
    //
    // Two primary tests, because a STAGED piece (a pre-applied cross-verse
    // auto-split, the usual shape of a cross_verse card) can't service the
    // edit actions — they need a real store-backed segment — but it CAN take
    // the WASL/WAQF label: 1 / 2 on a staged boundary just record the pick the
    // card commits with the split, exactly like clicking the picker. So the
    // wasl action publishes for staged rows too, and the rest doesn't.
    // ---------------------------------------------------------------------
    $: accordionOpen = $valUiOpenCategory !== null;
    $: isPrimaryRow = !readOnly && !isContext && !!rowEl
        && (isPlaying
            || (staged && segIsCurrent && _stagedCursorLatched)
            || (instanceRole === 'main' && !accordionOpen && $segCurrentIdx === seg.index));
    $: isPrimaryForShortcuts = isPrimaryRow && !staged;
    $: isPrimaryForWasl = isPrimaryRow && !!onCardSetWasl;
    let _pubKey = '';
    $: {
        if (isPrimaryForShortcuts || isPrimaryForWasl) {
            const k = `${rowChapter}:${seg.index}:${isPrimaryForShortcuts}:${!!onCardIgnore}:${!!onCardAutofill}:${!!onCardToggleContext}:${isPrimaryForWasl}`;
            if (k !== _pubKey) {
                _pubKey = k;
                publishRowActions({
                    owner: _mountId,
                    chapter: rowChapter,
                    index: seg.index,
                    uid: seg.segment_uid ?? null,
                    adjust: isPrimaryForShortcuts ? doAdjust : undefined,
                    split: isPrimaryForShortcuts ? () => void doSplit() : undefined,
                    editRef: isPrimaryForShortcuts ? doEditRef : undefined,
                    goto: isPrimaryForShortcuts ? () => void doGoto() : undefined,
                    delete: isPrimaryForShortcuts ? doDelete : undefined,
                    ignore: (isPrimaryForShortcuts && onCardIgnore) || undefined,
                    autofill: (isPrimaryForShortcuts && onCardAutofill) || undefined,
                    toggleContext: (isPrimaryForShortcuts && onCardToggleContext) || undefined,
                    setWasl: (isPrimaryForWasl && onCardSetWasl) || undefined,
                });
            }
        } else if (_pubKey) {
            _pubKey = '';
            clearRowActions(_mountId);
        }
    }

    function onRefTextClick(e: MouseEvent): void {
        if (readOnly) return;
        e.stopPropagation();
        void activate().then(() => beginRefEdit(seg, validationCategory, _mountId));
    }

    // ---------------------------------------------------------------------
    // Flag — manual "needs a second look" thread (local toggle, NOT editMode)
    // ---------------------------------------------------------------------
    // A flag is a comment thread, not a canvas/structural edit, so its inline
    // editor lives in row-local state and never touches `setEdit`/`editMode`.
    // Only the flagger (`seg.flag.mine`) may edit/clear an existing flag;
    // anyone may create one. `flagSegment` handles the dispatch + dirty/save.
    let flagEditing = false;
    let flagDraft = '';
    $: isFlagged = !!seg.flag;
    $: canEditFlag = !isFlagged || !!seg.flag?.mine;
    $: flagAuthor = {
        role: $currentUser.role,
        login: $currentUser.login,
        id: $currentUser.hf_user_id,
    } as FlagAuthor;
    // Apply is disabled when creating with no text; an existing flag allows a
    // blank draft so clearing it removes the flag (unflag).
    $: flagApplyDisabled = !isFlagged && flagDraft.trim().length === 0;

    function openFlagEditor(e: MouseEvent): void {
        e.stopPropagation();
        if (!canEditFlag) return;
        void activate().then(() => {
            flagDraft = seg.flag?.comment ?? '';
            flagEditing = true;
        });
    }
    function cancelFlagEditor(): void {
        flagEditing = false;
        flagDraft = '';
    }
    function applyFlagEditor(): void {
        const ok = flagSegment(seg, flagDraft, isFlagged ? 'edit' : 'set', flagAuthor);
        if (ok) {
            flagEditing = false;
            flagDraft = '';
        }
    }
    function onFlagKeydown(e: KeyboardEvent): void {
        if (e.key === 'Escape') {
            e.preventDefault();
            cancelFlagEditor();
        } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            if (!flagApplyDisabled) applyFlagEditor();
        }
    }

    function onRowClick(e: MouseEvent): void {
        if (wordEditing) return;
        if (get(editMode) || readOnly) return;
        const t = e.target as Element;
        if (t.closest('.seg-row-controls') || t.closest('canvas') || t.closest('.seg-text-ref')) return;
        playFromSegment(seg.index, rowChapter, staged ? seg.time_start : undefined, {
            isAccordionPlay: instanceRole !== 'main',
            accordionSiblings,
            piece: _piecePlayArg(),
            groupEndMs,
        });
    }

    function _timeFromCanvasEvent(e: MouseEvent, canvas: SegCanvas): number {
        const rect = canvas.getBoundingClientRect();
        const progress = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        const hl = canvas._splitHL;
        const tStart = hl ? hl.wfStart : seg.time_start;
        const tEnd = hl ? hl.wfEnd : seg.time_end;
        return tStart + progress * (tEnd - tStart);
    }

    function _seekFromCanvasEvent(e: MouseEvent, canvas: SegCanvas): void {
        const timeMs = _timeFromCanvasEvent(e, canvas);
        const chapter = seg.chapter ?? fallbackChapter;
        const active = get(playingSegmentIndex);
        const isSelfPlaying = !!active
            && active.chapter === chapter
            && active.index === seg.index
            && !segPort.paused;
        if (isSelfPlaying) {
            // Port owns CBR-vs-VBR offset translation: `seek(timeMs)` accepts
            // file-absolute and writes the clip-relative value internally.
            // Was previously a VBR-vs-CBR branch that did its own offset math.
            segPort.seek(timeMs);
        } else {
            playFromSegment(seg.index, chapter, timeMs, {
                isAccordionPlay: instanceRole !== 'main',
                accordionSiblings,
                piece: _piecePlayArg(),
                groupEndMs,
            });
        }
    }

    function onCanvasMousedown(e: MouseEvent): void {
        if (readOnly || get(editMode) || wordEditing) return;
        const canvas = e.currentTarget as SegCanvas;

        e.preventDefault();
        // Initial mousedown: full seek-or-play decision (re-init the
        // AudioRange when the user clicks a non-playing row).
        _seekFromCanvasEvent(e, canvas);

        // Drag (mousemove with the button held): SCRUB only — write the
        // new file-absolute time directly. Routing through
        // `_seekFromCanvasEvent` here would re-enter `playFromSegment` on
        // every pixel of mouse movement (each pixel fires a fresh
        // `mousemove` event), each call disposing the live AudioRange and
        // rebuilding it. That stack of dispose+rebuild churns 3+ ranges
        // per click in practice and inherits stale state across
        // iterations. The seek-only path is what the `isSelfPlaying`
        // branch above does anyway — apply it unconditionally for drag.
        function onMove(ev: MouseEvent): void {
            const timeMs = _timeFromCanvasEvent(ev, canvas);
            segPort.seek(timeMs);
        }
        function onUp(): void {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    }
    function onWordCanvasClick(e: MouseEvent): void {
        if (!wordEditing || !canvasEl) return;
        e.stopPropagation();
        seekWordPreview(_timeFromCanvasEvent(e, canvasEl));
    }
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
<!-- dir="ltr" island: the row pairs a left→right waveform with its confidence
     stripe / time labels / edit controls; the geometry mirrors the waveform, not
     the RTL frame (same rationale as the guide's MockSegCard). Arabic ref text
     still shapes RTL within its own run. -->
<div
    class="seg-row"
    dir="ltr"
    class:dirty
    class:playing={highlighted}
    class:seg-row-context={isContext}
    class:seg-row-staged={staged}
    class:seg-neighbour={isNeighbour}
    class:seg-edit-target={isEditingThisRow}
    class:word-editing={wordEditing}
    class:mode-history={mode === 'history'}
    data-seg-index={seg.index}
    data-seg-chapter={seg.chapter ?? undefined}
    data-seg-uid={seg.segment_uid || undefined}
    data-seg-start={seg.time_start}
    data-seg-end={seg.time_end}
    data-hist-time-start={readOnly || staged ? String(seg.time_start) : undefined}
    data-hist-time-end={readOnly || staged ? String(seg.time_end) : undefined}
    data-hist-audio-url={(readOnly || staged) && seg.audio_url ? seg.audio_url : undefined}
    data-hist-op-id={opId ?? undefined}
    style:max-width={wordEditing ? `min(100%, ${wordEditWidth + 24}px)` : undefined}
    bind:this={rowEl}
    on:click={onRowClick}
>
    <div class="seg-left">
        {#if wordEditing}
            <div class="word-editor-header">
                <span class="word-editor-title">{formatRef(seg.matched_ref, $quranRefs?.verse_word_counts)}</span>
                <span class="word-editor-status">{wordLockedIndex === null ? tr($localeStore, m.segments_word_loop_segment()) : tr($localeStore, m.segments_word_loop_word({ number: String(wordLockedIndex + 1) }))}</span>
            </div>
        {/if}
        {#if readOnly && showPlayBtn}
            {#if previewCtx}
                <button
                    class="btn btn-sm seg-card-play-btn"
                    class:playing={previewActive}
                    title={playButtonTitle}
                    on:click={onPreviewPlayClick}
                >{playGlyph}</button>
            {:else}
                <button class="btn btn-sm seg-card-play-btn" title={playButtonTitle}>&#9654;</button>
            {/if}
        {/if}
        <div class="seg-waveform-viewport" class:word-editing={wordEditing}>
        <div class="seg-waveform-stage" class:word-editing={wordEditing} style:width={wordEditing ? `${wordEditWidth}px` : undefined}>
        <canvas
            bind:this={canvasEl}
            width={wordEditing ? Math.round(wordEditWidth) : SEG_ROW_CANVAS_WIDTH}
            height={SEG_ROW_CANVAS_HEIGHT}
            data-needs-waveform
            on:mousedown={onCanvasMousedown}
            on:click={onWordCanvasClick}
        ></canvas>
        {#if wordEditing}
            <WordTimingEditor words={wordDraft} labels={reviewDisplayWords}
                startMs={seg.time_start} endMs={seg.time_end} width={wordEditWidth}
                lockedIndex={wordLockedIndex} onChange={onWordDraftChange} onSeek={seekWordPreview} onLock={toggleWordLock} />
        {/if}
        </div>
        </div>
        {#if wordEditing}
            <div class="word-editor-toolbar">
                <button class="btn btn-sm seg-card-play-btn" type="button" title={playButtonTitle} on:click|stopPropagation={onSegPlayClick}>{playGlyph}</button>
                <span class="word-editor-hint">{tr($localeStore, m.segments_word_edit_hint())}</span>
                <div class="word-editor-actions">
                    <button class="btn btn-sm" type="button" disabled={wordSaving} on:click|stopPropagation={toggleWordEditor}>{tr($localeStore, m.common_action_cancel())}</button>
                    <button class="btn btn-sm word-editor-save" type="button" disabled={wordSaving || !wordHasChanges} on:click|stopPropagation={applyWordTimings}>{tr($localeStore, wordSaving ? m.segments_word_edit_saving() : m.segments_word_edit_save())}</button>
                </div>
            </div>
            {#if wordError}<div class="word-editor-error" role="alert">{wordError}</div>{/if}
        {:else if isEditingThisRow && $editMode === 'trim' && editSegCanvas}
            <TrimPanel {seg} canvas={editSegCanvas} />
        {:else if isEditingThisRow && $editMode === 'split' && editSegCanvas}
            <SplitPanel {seg} canvas={editSegCanvas} />
        {:else if !readOnly}
            <div class="seg-row-controls">
                {#if showPlayBtn || showGotoBtn || !isContext}
                    <div class="seg-row-play-stack">
                    <div class="seg-row-play-actions">
                        {#if showPlayBtn}
                            <button class="btn btn-sm seg-card-play-btn" title={playButtonTitle} on:click={onPlayClick} on:mouseenter={onPlayHover} on:mouseleave={onPlayLeave}>{playGlyph}</button>
                        {/if}
                        {#if showGotoBtn}
                            <button class="btn btn-sm seg-card-goto-btn" on:click={onGotoClick}>{gotoButtonLabel}</button>
                        {/if}
                        {#if !isContext}
                            <span class="seg-flag-wrap">
                                <button
                                    class="btn btn-sm seg-flag-btn"
                                    class:is-flagged={isFlagged}
                                    class:is-open={flagEditing}
                                    class:is-readonly={isFlagged && !canEditFlag}
                                    aria-pressed={isFlagged}
                                    aria-label={flagAriaLabel}
                                    title={isFlagged ? seg.flag?.comment : flagButtonTitle}
                                    on:click={openFlagEditor}
                                >
                                    <svg class="seg-flag-icon" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                                        <path d="M3.5 1.5v13" />
                                        <path d="M3.5 2.2h8.2l-1.7 2.6 1.7 2.6H3.5z" />
                                    </svg>
                                </button>
                                {#if isFlagged && !flagEditing}
                                    <span class="seg-flag-tip" role="tooltip">
                                        <span class="seg-flag-tip-label">{flagTipLabel}</span>
                                        <span class="seg-flag-tip-body">{seg.flag?.comment}</span>
                                    </span>
                                {/if}
                            </span>
                        {/if}
                    </div>
                        {#if $isSampleMode && instanceRole === 'main' && reviewWordTimings.length > 0 && reviewDisplayWords.length === reviewWordTimings.length}
                            <button class="btn btn-sm seg-word-edit-btn" class:is-open={wordEditing} use:editGate
                                disabled={!!$editMode && !wordEditing}
                                on:click|stopPropagation={toggleWordEditor}>{tr($localeStore, wordEditing ? m.segments_word_close_button() : m.segments_word_edit_button())}</button>
                        {/if}
                    </div>
                {/if}

                {#if !isContext}
                    <div class="seg-actions">
                        <button class="btn btn-sm btn-adjust" use:editGate on:click={onAdjustClick}>{adjustButtonLabel}</button>
                        <button class="btn btn-sm btn-merge-prev"
                            disabled={mergePrevDisabled}
                            title={mergePrevTitle}
                            use:editGate
                            on:click={onMergePrevClick}>{mergeUpButtonLabel}</button>
                        <button class="btn btn-sm btn-delete" use:editGate on:click={onDeleteClick}>{deleteButtonLabel}</button>
                        <button class="btn btn-sm btn-split" use:editGate on:click={onSplitClick}>{splitButtonLabel}</button>
                        <button class="btn btn-sm btn-merge-next"
                            disabled={mergeNextDisabled}
                            title={mergeNextTitle}
                            use:editGate
                            on:click={onMergeNextClick}>{mergeDownButtonLabel}</button>
                        <button class="btn btn-sm btn-edit-ref" use:editGate on:click={onEditRefClick}>{editRefButtonLabel}</button>
                    </div>
                {/if}
            </div>

            {#if flagEditing}
                <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
                <div class="seg-flag-editor" on:click={(e) => e.stopPropagation()}>
                    <textarea
                        class="seg-flag-input"
                        bind:value={flagDraft}
                        rows="2"
                        placeholder={flagPlaceholder}
                        on:keydown={onFlagKeydown}
                        on:mousedown={(e) => e.stopPropagation()}
                    ></textarea>
                    <div class="seg-flag-editor-foot">
                        <span class="seg-flag-hint">
                            {flagHint}
                        </span>
                        <span class="seg-flag-editor-actions">
                            <button class="btn btn-sm seg-flag-cancel" on:click|stopPropagation={cancelFlagEditor}>{tr($localeStore, m.common_action_cancel())}</button>
                            <button
                                class="btn btn-sm seg-flag-apply"
                                disabled={flagApplyDisabled}
                                on:click|stopPropagation={applyFlagEditor}
                            >{flagApplyLabel}</button>
                        </span>
                    </div>
                </div>
            {/if}
        {/if}
    </div>

    {#if !wordEditing}<div class="seg-text {confClass}">
        <div class="seg-text-meta">
            <div class="seg-text-header">
                <span class="seg-text-index">{indexLabel}</span>
                <span class="seg-text-sep">|</span>
                {#if isEditingThisRow && $editMode === 'reference'}
                    <ReferenceEditor {seg} on:preview={(e) => previewState = e.detail} />
                {:else}
                    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
                    <span class="seg-text-ref" class:seg-history-changed={changedRef} use:editGate on:click={onRefTextClick}>{formatRef(seg.matched_ref, $quranRefs?.verse_word_counts)}</span>
                {/if}
                <span class="seg-text-sep">|</span>
                <span class="seg-text-conf {confClass}" class:seg-history-changed={changedConf}>{confText}</span>
                {#each rowChips as chip (chip)}
                    <span class="seg-chip" class:seg-chip-warn={chip !== 'repetition'}>{chipLabel(chip)}</span>
                {/each}
            </div>
            <div class="seg-text-times" class:seg-history-changed={changedDur} title={durTitle}>
                <TimeRange
                    {seg}
                    {rowEl}
                    mountId={_mountId}
                    {validationCategory}
                    {instanceRole}
                    {readOnly}
                    fallbackChapter={rowChapter}
                />
            </div>
            {#if contextLabel}
                <div class="seg-text-label">{contextLabel}</div>
            {/if}
        </div>
        <div class="seg-text-body" class:seg-history-changed={changedRef} class:word-edit-hidden={wordEditing}>
            {#if reviewDisplayWords.length > 0}
                {#each reviewWordTimings as word, wordIndex (`${word.location}:${word.start_ms}`)}
                    <span
                        class="seg-review-word"
                        class:active={$isMainAudioPlaying
                            && $activeWordCursor?.chapter === rowChapter
                            && $activeWordCursor?.index === seg.index
                            && $activeWordCursor?.wordIndex === wordIndex}
                    >{reviewDisplayWords[wordIndex]}</span>{#if wordIndex < reviewWordTimings.length - 1}{' '}{/if}
                {/each}
            {:else}
                {bodyText}
            {/if}
        </div>
    </div>{/if}
</div>

<style>
    :global(.seg-row.word-editing) { display: flex; flex-direction: column; }
    :global(.seg-row.word-editing .seg-left) { flex: none; width: 100%; min-width: 0; }
    .seg-waveform-viewport.word-editing { width: 100%; overflow-x: auto; overflow-y: hidden; }
    .seg-waveform-stage { position: relative; width: 100%; }
    .seg-waveform-stage.word-editing { height: 146px; flex: none; }
    .seg-waveform-stage canvas { display: block; }
    .word-editor-header, .word-editor-toolbar { display: flex; align-items: center; gap: 12px; }
    .word-editor-header { justify-content: space-between; padding: 2px 8px 8px; }
    .word-editor-title { font-family: var(--font-mono); font-size: 12px; color: var(--text-secondary); }
    .word-editor-status { font-size: 12px; color: var(--accent); }
    .word-editor-toolbar { padding: 9px 8px 2px; }
    .word-editor-hint { flex: 1; color: var(--text-muted); font-size: 12px; }
    .word-editor-actions { display: flex; gap: 8px; margin-left: auto; }
    .word-editor-save { background: var(--accent); color: var(--accent-fg); }
    .word-editor-error { padding: 6px 8px; color: var(--state-error-fg); font-size: 12px; }
    .seg-row-play-stack { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
    .seg-word-edit-btn { align-self: stretch; }
    .seg-word-edit-btn.is-open { border-color: var(--accent); color: var(--accent); }
    .word-edit-hidden { display: none; }
    /* ---- Sample-mode row chips ---- */
    .seg-chip {
        display: inline-flex; align-items: center; height: 18px; padding: 0 7px; margin-inline-start: 6px;
        background: var(--panel-2); border: 1px solid var(--border-quiet); border-radius: 999px;
        font-size: 10.5px; font-family: var(--font-mono); color: var(--text-secondary); white-space: nowrap;
    }
    .seg-chip-warn { background: var(--state-error-bg); border-color: oklch(0.86 0.130 75 / 0.4); color: var(--state-error-fg); }
    .seg-review-word {
        display: inline;
        border-radius: 0.25em;
        transition: color var(--t-fast) var(--ease-out-quart), background var(--t-fast) var(--ease-out-quart);
    }
    .seg-review-word.active {
        color: var(--accent-fg);
        background: var(--accent);
    }

    /* ---- Flag button (manual "needs a second look") ---- */
    /* A peer of the play/go-to controls. Idle reads as a quiet outline; an
       active flag fills with the warm amber attention token so a flagged row
       is legible at a glance without competing with the cyan accent. */
    .seg-flag-wrap {
        position: relative;
        display: inline-flex;
    }

    .seg-flag-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 32px;
        padding: 6px 8px;
        color: var(--text-muted);
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-2);
        transition:
            color var(--t-fast) var(--ease-out-quart),
            background var(--t-fast) var(--ease-out-quart),
            border-color var(--t-fast) var(--ease-out-quart);
    }
    .seg-flag-btn:hover {
        color: var(--state-warn-fg);
        border-color: var(--state-warn-border);
        background: var(--state-warn-bg);
    }
    .seg-flag-btn.is-flagged {
        color: var(--state-warn-fg);
        background: var(--state-warn-bg);
        border-color: var(--state-warn-border);
    }
    .seg-flag-btn.is-open {
        color: var(--state-warn-fg);
        border-color: var(--state-warn-fg);
        background: var(--state-warn-bg);
    }
    .seg-flag-btn.is-readonly {
        cursor: default;
    }
    .seg-flag-icon {
        fill: none;
        stroke: currentColor;
        stroke-width: 1.5;
        stroke-linejoin: round;
        stroke-linecap: round;
    }
    /* Flagged pole fills, unflagged stays hollow — a hue-independent shape cue. */
    .seg-flag-btn.is-flagged .seg-flag-icon path:nth-child(2) {
        fill: var(--state-warn-bg);
    }

    /* ---- Hover tooltip: re-read the root comment from the button ---- */
    .seg-flag-tip {
        position: absolute;
        bottom: calc(100% + 6px);
        left: 0;
        z-index: 40;
        display: flex;
        flex-direction: column;
        gap: 3px;
        width: max-content;
        max-width: 260px;
        padding: var(--s-2) var(--s-3);
        background: var(--elevated);
        border: 1px solid var(--state-warn-border);
        border-radius: var(--r-2);
        box-shadow: var(--shadow-pop);
        opacity: 0;
        transform: translateY(3px);
        pointer-events: none;
        transition:
            opacity var(--t-fast) var(--ease-out-quart),
            transform var(--t-fast) var(--ease-out-quart);
    }
    .seg-flag-wrap:hover .seg-flag-tip,
    .seg-flag-btn:focus-visible + .seg-flag-tip {
        opacity: 1;
        transform: translateY(0);
    }
    .seg-flag-tip-label {
        font-family: var(--font-mono);
        font-size: 10px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--state-warn-fg);
    }
    .seg-flag-tip-body {
        font-size: var(--fs-meta);
        line-height: var(--lh-normal);
        color: var(--text-primary);
        white-space: pre-wrap;
        word-break: break-word;
    }

    /* ---- Inline comment editor ---- */
    .seg-flag-editor {
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
        margin-top: var(--s-2);
        padding: var(--s-2) var(--s-3);
        background: var(--canvas-inset);
        border: 1px solid var(--state-warn-border);
        border-radius: var(--r-2);
    }
    .seg-flag-input {
        width: 100%;
        resize: vertical;
        min-height: 44px;
        padding: 6px 8px;
        font: var(--fs-body)/var(--lh-normal) var(--font-sans);
        color: var(--text-primary);
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-1);
    }
    .seg-flag-input::placeholder {
        color: var(--text-muted);
    }
    .seg-flag-input:focus-visible {
        outline: none;
        border-color: var(--state-warn-fg);
        box-shadow: 0 0 0 2px var(--state-warn-bg);
    }
    .seg-flag-editor-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--s-3);
        flex-wrap: wrap;
    }
    .seg-flag-hint {
        font-size: var(--fs-meta);
        color: var(--text-muted);
    }
    .seg-flag-editor-actions {
        display: inline-flex;
        gap: var(--s-2);
        margin-left: auto;
    }
    .seg-flag-cancel {
        color: var(--text-secondary);
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-2);
    }
    .seg-flag-cancel:hover {
        color: var(--text-primary);
        background: var(--panel-2);
    }
    .seg-flag-apply {
        color: var(--accent-fg);
        background: var(--state-warn-fg);
        border: 1px solid var(--state-warn-fg);
        border-radius: var(--r-2);
        font-weight: 600;
    }
    .seg-flag-apply:hover:not(:disabled) {
        background: oklch(from var(--state-warn-fg) calc(l + 0.04) c h);
    }
    .seg-flag-apply:disabled {
        color: var(--text-faint);
        background: var(--panel);
        border-color: var(--border-default);
        cursor: not-allowed;
    }
</style>
