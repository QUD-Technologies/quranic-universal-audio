<script lang="ts">
    /**
     * SegmentsFooter — unified pinned-bottom bar for the Segments tab.
     *
     * Combines reciter identity + state pill + claim-actions on the left,
     * a 3×2 stacked player cluster in the centre (play · Surah · Ayah |
     * speed · auto-play · auto-scroll), and the save controls on the right.
     * Owns the `<audio>` element and the `segPort` lifecycle — the legacy
     * `SegmentsAudioControls.svelte` is removed.
     *
     * A 4px progress fill across the top of the footer tracks the
     * currently-loaded chapter audio via segPort.currentTimeMs /
     * (audio element duration).
     *
     * Reads tab-scoped stores directly; mutating coordination (reciter task
     * refresh, chapter load, verse jump, picker change) is bubbled to the
     * parent via events so SegmentsTab stays the single owner of the
     * reciter-task subscription and chapter/verse data fetches.
     */
    import { createEventDispatcher, onMount, tick } from 'svelte';
    import { get } from 'svelte/store';

    import * as m from '$lib/paraglide/messages';
    import { clickOutside } from '../../../../lib/actions/click-outside';
    import { localizeDigits } from '../../../../lib/i18n/format';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import { markReadyBypass } from '../../../../lib/api/claims-client';
    import type { ReciterTask } from '../../../../lib/api/reciter-task';
    import ClaimButton from '../../../../lib/components/ClaimButton.svelte';
    import type { CombinationSelection } from '../../../../lib/components/picker/combination-picker-types';
    import CombinationPicker from '../../../../lib/components/picker/CombinationPicker.svelte';
    import LoadingSpinner from '../../../../lib/components/player/LoadingSpinner.svelte';
    import SurahPicker from '../../../../lib/components/player/SurahPicker.svelte';
    import ReciterChip from '../../../../lib/components/ReciterChip.svelte';
    import Icon from '../../../../lib/icons/Icon.svelte';
    import type { IconName } from '../../../../lib/icons/index';
    import { editingMode } from '../../../../lib/stores/editing-mode';
    import type { PublicBucket } from '../../../../lib/types/public-bucket';
    import { displayTimeMs } from '../../../../lib/playback/audio-graph';
    import { LS_KEYS } from '../../../../lib/utils/constants';
    import { vocabLabel } from '../../../../lib/i18n/vocab';
    import { getSurahInfo, surahInfoReady } from '../../../../lib/utils/surah-info';
    import { SEGMENTS_SPEEDS } from '../../../../lib/utils/speed-control';
    import { autoSaveEnabled, toggleAutoSave } from '../../stores/autosave';
    import { activeSample, isSampleMode, segmentsSubTab } from '../../stores/samples';
    import {
        livePlayingVerse,
        pickerDisplayChapter,
        segAllData,
        segData,
        selectedChapter,
        selectedReciter,
        selectedVerse,
        verseOptions,
    } from '../../stores/chapter';
    import { isDirtyStore } from '../../stores/dirty';
    import { historyLoadState, historyVisible } from '../../stores/history';
    import {
        autoPlayEnabled,
        segmentEndChimeEnabled,
        autoScrollEnabled,
        isMainAudioPlaying,
        playbackSpeed,
        playingSegmentIndex,
        segAudioBuffering,
        segAudioElement,
        segPort,
        segPortReady,
    } from '../../stores/playback';
    import { saveButtonLabel, savePreviewVisible } from '../../stores/save';
    import { hideHistoryView, showHistoryView } from '../../utils/history/actions';
    import { resolveChapterDurationMs } from '../../utils/playback/footer-duration';
    import { editPreviewPlaying } from '../../utils/playback/play-range';
    import {
        onSegAudioEnded,
        onSegPlayClick,
        onSegTimeUpdate,
        startSegAnimation,
        stopSegAnimation,
    } from '../../utils/playback/playback';
    import {
        confirmSaveFromPreview,
        hideSavePreview,
        onSegSaveClick,
    } from '../../utils/save/actions';
    import MarkReadyModal from './MarkReadyModal.svelte';
    import ShortcutsGuide from './ShortcutsGuide.svelte';

    export let reciterTask: ReciterTask | null = null;
    export let chipActionBusy: '' | 'unclaim' | 'mark' = '';
    export let contextName: string | null = null;
    export let contextNameAr: string | null = null;
    export let contextCountry: string | null = null;
    export let contextBucket: PublicBucket | null = null;
    export let contextRiwayah: string | null = null;
    export let contextStyle: string | null = null;

    const dispatch = createEventDispatcher<{
        reciterChange: {
            slug: string;
            name: string;
            nameAr: string | null;
            country: string | null;
            bucket: PublicBucket;
            riwayah: string;
            style: string;
        };
        chapterChange: string;
        verseJump: string;
        unclaim: void;
        markReady: void;
        claimed: void;
    }>();

    let pickerOpen = false;
    let markReadyOpen = false;
    let surahOpen = false;
    let ayahOpen = false;
    let ayahFilterInput: HTMLInputElement | null = null;
    let ayahQuery = '';
    let footerEl: HTMLDivElement | null = null;
    let audioEl: HTMLAudioElement | null = null;

    // Local mirror of the AUDIBLE playback position (ms) so the progress bar
    // and elapsed clock can be reactive without polling. Written by the
    // onTimeUpdate subscription below, which compensates the raw media clock
    // (`el.currentTime`, the decode position) for platform output latency via
    // `displayTimeMs` — the same compensation the waveform cursor uses
    // (`playback.ts::drawActivePlayhead`). Without this the bar leads the
    // recitation by the output latency (issue #172). Display-only: control
    // paths (seek, segment-crossing) keep reading the raw clock off the port.
    let currentMs = 0;

    // Live mirror of `<audio>.duration` (ms), kept fresh by the
    // loadedmetadata/durationchange listeners wired in onMount. 0 while
    // non-finite (browser hasn't resolved a streamed/headerless duration yet).
    let elDurationMs = 0;
    function onElDuration(): void {
        const d = audioEl?.duration;
        elDurationMs = d && isFinite(d) ? d * 1000 : 0;
    }

    // ResizeObserver + port-subscription handles live at module scope so
    // the unified teardown in `onMount`'s return can reach them.
    let footerResizeObs: ResizeObserver | null = null;
    let playbackUnsubs: Array<() => void> = [];

    onMount(() => {
        // -------------------------------------------------------------
        // Footer height publication — drives the segments panel's
        // padding-bottom so the list never hides under a taller-than-
        // default footer (the stacked player makes it ~108px).
        // -------------------------------------------------------------
        if (footerEl) {
            const apply = (): void => {
                if (!footerEl) return;
                document.documentElement.style.setProperty(
                    '--seg-footer-actual-h',
                    `${footerEl.offsetHeight}px`,
                );
            };
            apply();
            footerResizeObs = new ResizeObserver(apply);
            footerResizeObs.observe(footerEl);
        }

        // -------------------------------------------------------------
        // <audio> element + segPort wiring (moved from the deleted
        // SegmentsAudioControls.svelte). Seed `playbackSpeed` from
        // localStorage so a 1.25× session persists across reloads.
        // -------------------------------------------------------------
        const stored = localStorage.getItem(LS_KEYS.SEG_SPEED);
        if (stored) {
            const v = parseFloat(stored);
            if (!Number.isNaN(v)) playbackSpeed.set(v);
        }

        if (audioEl) {
            // Legacy mirror — still read by a handful of utils. Cleared in
            // teardown below to keep `get(segAudioElement)` accurate.
            segAudioElement.set(audioEl);
            segPort.attachElement(audioEl);
            segPortReady.set(true);
            // Re-read `<audio>.duration` whenever the browser resolves it.
            // Headerless MP3s (CDN stream-through) report duration LATE via
            // `durationchange` rather than at the first `loadedmetadata`;
            // without this listener the footer's once-sampled read missed it
            // and the progress bar stayed hidden.
            audioEl.addEventListener('loadedmetadata', onElDuration);
            audioEl.addEventListener('durationchange', onElDuration);
            playbackUnsubs = [
                segPort.onPlay(startSegAnimation),
                segPort.onPause(stopSegAnimation),
                segPort.onEnded(onSegAudioEnded),
                // First audible frame — the click→sound gap is over, so drop
                // the play-button buffering spinner (issue #172).
                segPort.onPlaying(() => segAudioBuffering.set(false)),
                // A media error means the audible frame will never come —
                // don't strand the spinner.
                segPort.onError(() => segAudioBuffering.set(false)),
                segPort.onTimeUpdate((fileMs) => {
                    // Display the audible position; `onSegTimeUpdate` keeps the
                    // raw clock for segment-crossing / highlight control logic.
                    // While buffering (play committed but not yet audible) the
                    // synchronous seek fires a `timeupdate` at the destination
                    // position — leave the bar where it was so it doesn't jump
                    // AHEAD of the still-silent audio (issue #172). The first
                    // post-`playing` timeupdate resumes normal tracking.
                    if (!get(segAudioBuffering)) currentMs = displayTimeMs(fileMs);
                    onSegTimeUpdate(fileMs);
                }),
            ];
        }

        return () => {
            footerResizeObs?.disconnect();
            footerResizeObs = null;
            document.documentElement.style.removeProperty('--seg-footer-actual-h');
            for (const off of playbackUnsubs) off();
            playbackUnsubs = [];
            if (audioEl) {
                audioEl.removeEventListener('loadedmetadata', onElDuration);
                audioEl.removeEventListener('durationchange', onElDuration);
            }
            segPort.attachElement(null);
            segPortReady.set(false);
            segAudioElement.set(null);
        };
    });

    // Seed `segPort.playbackRate` on first ready transition. Reading the
    // port's playbackRate gates the write to once per session — avoids
    // racing with the per-keystroke speed cycle. (Same pattern that lived
    // in SegmentsAudioControls.)
    $: if ($segPortReady && segPort.playbackRate === 1 && $playbackSpeed !== 1) {
        segPort.setPlaybackRate($playbackSpeed);
    }

    $: hasReciter = !!$selectedReciter;

    // What number to show in the Surah picker. `pickerDisplayChapter` is the
    // programmatic override written by `playFromSegment` when a cross-chapter
    // accordion row is played — visual-only, never triggers a chapter swap.
    // Falls back to `selectedChapter` (the authoritative load gate).
    $: displaySurahNum =
        $pickerDisplayChapter ?? ($selectedChapter ? parseInt($selectedChapter) : null);

    let _surahMap: ReturnType<typeof getSurahInfo> = {};
    void surahInfoReady.then(() => { _surahMap = getSurahInfo(); });
    $: displaySurahName = ((): string | null => {
        const info = _surahMap[String(displaySurahNum)];
        if (!info) return null;
        return $localeStore === 'ar' && info.name_ar ? info.name_ar.replace(/^سُورَةُ\s*/, '') : info.name_en;
    })();

    $: chipMeta = tr(
        $localeStore,
        [vocabLabel('riwayah', contextRiwayah), vocabLabel('style', contextStyle)]
            .filter(Boolean)
            .join(' · '),
    );

    // Filtered to surahs the reciter actually has in their audio manifest.
    // Manifest keys are either "<surah>" or "<surah>:<ayah>" — take the
    // numeric prefix, dedupe, sort. Fall back to 1..114 while segAllData
    // is still loading so the picker isn't briefly empty.
    $: allSurahs = (() => {
        const byCh = $segAllData?.audio_by_chapter;
        if (!byCh) return Array.from({ length: 114 }, (_, i) => i + 1);
        const nums = new Set<number>();
        for (const key of Object.keys(byCh)) {
            const n = parseInt(key.split(':')[0] ?? '', 10);
            if (Number.isFinite(n) && n >= 1 && n <= 114) nums.add(n);
        }
        if (nums.size === 0) return Array.from({ length: 114 }, (_, i) => i + 1);
        return Array.from(nums).sort((a, b) => a - b);
    })();

    $: filteredAyahs = ayahQuery.trim()
        ? $verseOptions.filter((v) => String(v).startsWith(ayahQuery.trim()))
        : $verseOptions;

    $: historyButtonLabel = tr($localeStore, $historyVisible
        ? m.segments_footer_history_back()
        : $historyLoadState === 'loading'
          ? m.segments_footer_history_loading()
          : m.segments_footer_history_label());

    // ---- Progress bar -----------------------------------------------
    // % through the currently-loaded CHAPTER audio. Under chapter-continuous
    // playback the user sees a single timeline spanning the full chapter;
    // clicking seeks anywhere within it. `chapterDurationMs` prefers the live
    // `<audio>.duration` (kept fresh by the durationchange listener) but falls
    // back to the manifest's chapter duration when the browser can't resolve
    // one up-front (headerless CDN stream) or is playing a short VBR clip —
    // see `resolveChapterDurationMs`.
    let chapterDurationMs = 0;
    $: {
        // Recompute on any playback/chapter change. `elDurationMs` is itself
        // reactive (durationchange/loadedmetadata listeners); the other reads
        // are reactive deps so this block re-runs when playback moves.
        void $isMainAudioPlaying;
        void $segData?.audio_url;
        const inClip = !!segPort.window?.isClip;
        const playingCh = $playingSegmentIndex?.chapter
            ?? ($selectedChapter ? parseInt($selectedChapter) : NaN);
        const manifestDurMs = Number.isFinite(playingCh)
            ? ($segAllData?.chapter_duration_ms_by_chapter?.[String(playingCh)] ?? 0)
            : 0;
        chapterDurationMs = resolveChapterDurationMs({ elDurationMs, inClip, manifestDurMs });
    }

    // Progress bar DISPLAY is always chapter-wide — `currentMs` /
    // chapterDurationMs — regardless of accordion vs chapter playback.
    // The accordion bound only affects the click-seek ACTION (see
    // `onProgressClick` / `onProgressKey` below), not the visual.
    $: accordionActive = $playingSegmentIndex?.origin === 'accordion';

    $: progressPct =
        chapterDurationMs > 0
            ? Math.max(0, Math.min(100, (currentMs / chapterDurationMs) * 100))
            : 0;

    $: progressVisible = chapterDurationMs > 0;

    // ---- Live verse tracking ----------------------------------------
    // The Ayah cell mirrors whichever segment is actually playing —
    // main-list or accordion — the same way `displaySurahNum` mirrors
    // `pickerDisplayChapter`. Falls back to the user's manual
    // `selectedVerse` pick once playback stops so a paused/idle picker
    // still reflects their last explicit choice.
    $: surahLive = !!$livePlayingVerse && $isMainAudioPlaying;
    $: ayahLive = surahLive;
    $: displayVerseStr =
        surahLive && $livePlayingVerse ? String($livePlayingVerse.verse) : $selectedVerse;

    // ---- Player handlers --------------------------------------------
    // Pure delegate — every play/pause click (footer ▶ + spacebar shortcut)
    // routes through `onSegPlayClick`, which is the universal entry point.
    // It handles normal-mode toggling AND edit-mode preview pause/resume +
    // cold-start. Both paths share the same state machine.
    function handlePlayClick(): void {
        onSegPlayClick();
    }

    function handleAutoPlayToggle(): void {
        const next = !get(autoPlayEnabled);
        autoPlayEnabled.set(next);
        localStorage.setItem(LS_KEYS.SEG_AUTOPLAY, String(next));
    }

    function handleAutoScrollToggle(): void {
        const next = !get(autoScrollEnabled);
        autoScrollEnabled.set(next);
        localStorage.setItem(LS_KEYS.SEG_AUTOSCROLL, String(next));
    }

    // The chime only has meaning under autoplay (autoplay OFF stops at the
    // segment end, so nothing follows to announce), so the button is disabled
    // rather than hidden — a hidden control would read as a missing feature.
    // The stored preference is left untouched while disabled so flipping
    // autoplay back on restores the user's choice.
    function handleEndChimeToggle(): void {
        if (!get(autoPlayEnabled)) return;
        const next = !get(segmentEndChimeEnabled);
        segmentEndChimeEnabled.set(next);
        localStorage.setItem(LS_KEYS.SEG_END_CHIME, String(next));
    }

    function cyclePlaybackSpeed(): void {
        const cur = get(playbackSpeed);
        const curIdx = SEGMENTS_SPEEDS.findIndex((s) => Math.abs(s - cur) < 0.01);
        const idx = curIdx === -1 ? SEGMENTS_SPEEDS.indexOf(1) : curIdx;
        const next = SEGMENTS_SPEEDS[(idx + 1) % SEGMENTS_SPEEDS.length] ?? 1;
        playbackSpeed.set(next);
        localStorage.setItem(LS_KEYS.SEG_SPEED, String(next));
        segPort.setPlaybackRate(next);
    }

    function onProgressClick(ev: MouseEvent): void {
        // Seek anywhere in the chapter — but only in chapter-mode playback.
        // Accordion plays are bounded to a single segment; the click is a
        // no-op there so the playhead can't escape the bounded range.
        if (chapterDurationMs <= 0 || !$segPortReady) return;
        if (accordionActive) return;
        const target = ev.currentTarget as HTMLElement;
        const rect = target.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
        segPort.seek(pct * chapterDurationMs);
    }

    function onProgressKey(ev: KeyboardEvent): void {
        // Arrow keys nudge in 2% steps. Same accordion guard as click-seek.
        if (chapterDurationMs <= 0 || !$segPortReady) return;
        if (accordionActive) return;
        const step = chapterDurationMs * 0.02;
        if (ev.key === 'ArrowLeft') {
            ev.preventDefault();
            segPort.seek(Math.max(0, segPort.currentTimeMs() - step));
        } else if (ev.key === 'ArrowRight') {
            ev.preventDefault();
            segPort.seek(Math.min(chapterDurationMs - 1, segPort.currentTimeMs() + step));
        }
    }

    // ---- Picker / event handlers (unchanged from prior footer) ------
    function onPickerSelect(ev: CustomEvent<CombinationSelection>): void {
        const { reciter, delivery } = ev.detail;
        dispatch('reciterChange', {
            slug: delivery.slug,
            name: reciter.name,
            nameAr: reciter.name_ar ?? null,
            country: reciter.country ?? null,
            bucket: delivery.bucket,
            riwayah: delivery.riwayah,
            style: delivery.style,
        });
        pickerOpen = false;
    }

    function onSurahPick(n: number): void {
        surahOpen = false;
        dispatch('chapterChange', String(n));
    }

    async function openAyah(): Promise<void> {
        if (!$selectedChapter) return;
        ayahQuery = '';
        surahOpen = false;
        ayahOpen = true;
        await tick();
        ayahFilterInput?.focus();
    }

    function onAyahPick(v: number): void {
        ayahOpen = false;
        dispatch('verseJump', String(v));
    }

    function onAyahKey(ev: KeyboardEvent): void {
        if (ev.key === 'Enter' && filteredAyahs.length > 0) {
            onAyahPick(filteredAyahs[0]!);
        }
    }

    function onUnclaim(): void {
        if (chipActionBusy) return;
        dispatch('unclaim');
    }
    function onMarkReady(): void {
        if (chipActionBusy) return;
        // Owner-bypass shortcut: holders of ``claim.mark_ready_skip_gates``
        // skip the modal entirely. POST an empty body, then bubble the
        // existing ``markReady`` event so the parent refreshes reciter-task.
        // ``claims-client.markReadyBypass`` surfaces its own error toast on
        // failure — same envelope as the normal markReady path.
        if (reciterTask?.predicates.can_skip_mark_ready_gates) {
            const slug = $selectedReciter ?? '';
            if (!slug) return;
            chipActionBusy = 'mark';
            markReadyBypass(slug)
                .then(() => dispatch('markReady'))
                .catch(() => {/* toast already surfaced */})
                .finally(() => (chipActionBusy = ''));
            return;
        }
        // Default path: open the form modal. The modal owns the request;
        // on success it calls ``onMarkReadyDone`` which bubbles up so the
        // parent refreshes reciter-task.
        markReadyOpen = true;
    }
    function onMarkReadyDone(): void {
        // The modal already POSTed and got 200; bubble the existing
        // ``markReady`` event so SegmentsTab._refreshTask runs as before.
        dispatch('markReady');
    }
    function onClaimed(): void {
        dispatch('claimed');
    }

    function toggleHistory(): void {
        if ($historyVisible) hideHistoryView();
        else showHistoryView();
    }

    $: writeable = $editingMode.kind !== 'view';
    $: showSavePreview = $savePreviewVisible;
    $: saveDisabled = $autoSaveEnabled || !$isDirtyStore;
    $: saveLabel = tr($localeStore, $isDirtyStore
        ? $autoSaveEnabled && get(saveButtonLabel) === 'Save'
            ? m.segments_footer_save_saving()
            : $saveButtonLabel
        : m.segments_footer_save_saved());

    // Play button glyph: pause when normal-mode audio is playing OR an
    // edit-mode preview loop is in its "play" state. `editPreviewPlaying`
    // flips on cold-start / resume, off on user-initiated pause — distinct
    // from `previewLooping` (which stays set across pause/resume so the
    // rAF can resume seamlessly).
    $: playGlyph = ($isMainAudioPlaying || $editPreviewPlaying ? 'pause' : 'play') as IconName;

    $: canPlay = $segPortReady && !!($segData?.audio_url || $playingSegmentIndex);

    $: identityTitle = tr($localeStore, $isSampleMode
        ? m.segments_samples_back_to_list_title()
        : hasReciter ? m.segments_footer_switch_reciter_title() : m.segments_footer_pick_reciter_title());
    $: sampleTag = tr($localeStore, m.segments_samples_tag());
    $: pickReciterLabel = tr($localeStore, m.segments_footer_pick_reciter_title());
    $: awaitingAdminTitle = tr($localeStore, m.segments_footer_awaiting_admin_title());
    $: markedReadyPill = tr($localeStore, m.segments_footer_marked_ready_pill());
    $: markedReadyHint = tr($localeStore, m.segments_footer_marked_ready_hint());
    $: markReadyTitle = tr($localeStore, reciterTask?.predicates.can_skip_mark_ready_gates
        ? m.segments_footer_mark_ready_owner_title()
        : m.segments_footer_mark_ready_review_title());
    $: markReadyButtonLabel = tr($localeStore, m.segments_footer_mark_ready_button());
    $: unclaimButtonLabel = tr($localeStore, m.segments_footer_unclaim_button());
    $: speedTitle = tr($localeStore, m.segments_footer_speed_title());
    $: cancelLabel = tr($localeStore, m.common_action_cancel());
    $: speedAriaLabel = tr($localeStore, m.segments_footer_speed_aria_label({ speed: $playbackSpeed }));
    $: speedLabel = tr($localeStore, localizeDigits(`${$playbackSpeed}×`));
    $: autoplayTitle = tr($localeStore, m.segments_footer_autoplay_title());
    $: endChimeTitle = tr($localeStore, $autoPlayEnabled
        ? m.segments_footer_end_chime_title()
        : m.segments_footer_end_chime_disabled_title());
    $: autoscrollTitle = tr($localeStore, m.segments_footer_autoscroll_title());
    $: playPauseAriaLabel = tr($localeStore, $segAudioBuffering
        ? m.segments_footer_loading_audio_aria_label()
        : playGlyph === 'pause' ? m.segments_footer_pause_aria_label() : m.segments_footer_play_aria_label());
    $: surahEmptyLabel = tr($localeStore, m.segments_footer_surah_label());
    $: surahTriggerLabel = displaySurahNum
        ? (displaySurahName ?? localizeDigits(displaySurahNum))
        : surahEmptyLabel;
    $: ayahLabel = tr($localeStore, m.segments_footer_ayah_label());
    $: ayahAllLabel = tr($localeStore, m.segments_footer_ayah_all());
    $: ayahSearchPlaceholder = tr($localeStore, m.segments_footer_ayah_search_placeholder());
    $: ayahPickerAriaLabel = tr($localeStore, m.segments_footer_ayah_picker_aria_label());
    $: ayahNoMatchesLabel = tr($localeStore, m.segments_footer_ayah_no_matches());
    $: progressAriaLabel = tr($localeStore, m.segments_footer_progress_aria_label());
    $: historyToggleTitle = tr($localeStore, $historyVisible
        ? m.segments_footer_history_back_title()
        : m.segments_footer_history_label());
    $: historyBackLabel = tr($localeStore, m.segments_footer_history_back());
    $: historyLabel = tr($localeStore, m.segments_footer_history_label());
    $: autosaveTitle = tr($localeStore, $autoSaveEnabled
        ? m.segments_footer_autosave_on_title()
        : m.segments_footer_autosave_off_title());
    $: autosaveLabel = tr($localeStore, m.segments_footer_autosave_label());
    $: savedGlyphLabel = tr($localeStore, m.segments_footer_save_saved());
    $: autosavingLabel = tr($localeStore, m.segments_footer_save_autosaving());
    $: saveConfirmLabel = tr($localeStore, m.segments_footer_save_confirm());

    // Time display for the progress row.
    function fmt(ms: number): string {
        if (!ms || !isFinite(ms)) return '0:00:00';
        const total = Math.floor(ms / 1000);
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    $: elapsedMs = progressVisible ? Math.max(0, currentMs) : 0;
    $: totalMs = progressVisible ? chapterDurationMs : 0;
</script>

<div class="segs-footer" class:is-empty={!hasReciter} bind:this={footerEl}>
    <!-- dir="ltr" island: the scrub bar is a time axis (fill width %, pointer math
         from the left edge) — must run left→right even under RTL. -->
    <div class="progress" dir="ltr" class:active={progressVisible}>
        <span class="time pos">{fmt(elapsedMs)}</span>
        <div
            class="bar"
            on:click={onProgressClick}
            on:keydown={onProgressKey}
            role="slider"
            tabindex="0"
            aria-label={progressAriaLabel}
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={Math.round(progressPct)}
        >
            <div class="track">
                <div class="fill" style:width="{progressPct}%"></div>
            </div>
        </div>
        <span class="time dur">{fmt(totalMs)}</span>
    </div>

    <div class="row">
        <!-- Left zone: reciter identity + actions + playback prefs -->
        <div class="zone zone-left">
            <button
                type="button"
                class="identity"
                class:placeholder={!hasReciter}
                on:click={() => ($isSampleMode ? segmentsSubTab.set('samples') : (pickerOpen = true))}
                aria-haspopup="dialog"
                title={identityTitle}
            >
                {#if $isSampleMode}
                    <span class="sample-identity">
                        <span class="sample-tag">{sampleTag}</span>
                        <span class="sample-name">{$activeSample?.name ?? $selectedReciter}</span>
                    </span>
                {:else if hasReciter && contextName}
                    <ReciterChip
                        name={contextName}
                        nameAr={contextNameAr}
                        country={contextCountry}
                        subline={chipMeta || null}
                        bucket={contextBucket}
                        switchable={true}
                    />
                {:else}
                    <span class="identity-placeholder-label">{pickReciterLabel}</span>
                    <span class="identity-switch" aria-hidden="true">⇄</span>
                {/if}
            </button>

            {#if hasReciter && !showSavePreview && !$isSampleMode}
                <div class="reciter-actions">
                    {#if reciterTask?.row.marked_ready}
                        <span class="status-pill marked-ready" title={awaitingAdminTitle}>
                            <span class="pill-main">{markedReadyPill}</span>
                            <span class="pill-hint">{markedReadyHint}</span>
                        </span>
                    {:else}
                        <ClaimButton slug={$selectedReciter || ''} task={reciterTask} {onClaimed} />
                        {#if reciterTask?.predicates.can_mark_ready}
                            <button
                                type="button"
                                class="action ghost-accent"
                                disabled={chipActionBusy !== ''}
                                title={markReadyTitle}
                                on:click={onMarkReady}>{markReadyButtonLabel}</button
                            >
                        {/if}
                        {#if reciterTask?.predicates.can_release}
                            <button
                                type="button"
                                class="action ghost"
                                disabled={chipActionBusy !== ''}
                                on:click={onUnclaim}>{unclaimButtonLabel}</button
                            >
                        {/if}
                    {/if}
                </div>
            {/if}

        </div>

        <!-- Center transport: 1fr [play 40px] 1fr — play is pinned at viewport
             center; secondary controls flank it symmetrically. -->
        <div class="controls">
            {#if hasReciter}
                <!-- dir="ltr": media transport keeps its stable order (prefs · play ·
                     location) even under RTL, matching the transport-stays-LTR decision. -->
                <div class="transport" dir="ltr" use:clickOutside={() => { surahOpen = false; ayahOpen = false; }}>
                    <div class="transport-left">
                        <ShortcutsGuide />
                        <button
                            type="button"
                            class="speed-cell"
                            class:boosted={$playbackSpeed !== 1}
                            on:click={cyclePlaybackSpeed}
                            title={speedTitle}
                            aria-label={speedAriaLabel}>{speedLabel}</button
                        >
                        <button
                            type="button"
                            class="pref-cell"
                            class:on={$autoPlayEnabled}
                            aria-pressed={$autoPlayEnabled}
                            title={autoplayTitle}
                            on:click={handleAutoPlayToggle}
                        ><Icon name="autoplay" size={16} /></button>
                        <button
                            type="button"
                            class="pref-cell"
                            class:on={$autoScrollEnabled}
                            aria-pressed={$autoScrollEnabled}
                            title={autoscrollTitle}
                            on:click={handleAutoScrollToggle}
                        ><Icon name="autoscroll" size={16} /></button>
                        <button
                            type="button"
                            class="pref-cell"
                            class:on={$autoPlayEnabled && $segmentEndChimeEnabled}
                            disabled={!$autoPlayEnabled}
                            aria-pressed={$autoPlayEnabled && $segmentEndChimeEnabled}
                            title={endChimeTitle}
                            on:click={handleEndChimeToggle}
                        ><Icon name="whistle" size={16} /></button>
                    </div>

                    <button
                        type="button"
                        class="play-btn"
                        class:buffering={$segAudioBuffering}
                        disabled={!canPlay}
                        on:click={handlePlayClick}
                        aria-busy={$segAudioBuffering}
                        aria-label={playPauseAriaLabel}
                    >{#if $segAudioBuffering}<LoadingSpinner color="var(--accent-fg)" />{:else}<Icon name={playGlyph} size={18} />{/if}</button>

                    <div class="transport-right">
                        <SurahPicker
                            compact
                            surahNums={allSurahs}
                            value={displaySurahNum}
                            label={surahTriggerLabel}
                            hasValue={!!displaySurahNum}
                            live={surahLive}
                            open={surahOpen}
                            ontoggle={() => { surahOpen = !surahOpen; ayahOpen = false; }}
                            onchange={onSurahPick}
                        />
                        <button
                            type="button"
                            class="loc-cell"
                            class:has-value={!!displayVerseStr}
                            class:live={ayahLive}
                            disabled={!$selectedChapter}
                            on:click={openAyah}
                            aria-haspopup="dialog"
                            aria-expanded={ayahOpen}
                        >
                            <span class="loc-label">{ayahLabel}</span>
                            {#if displayVerseStr}<span class="loc-value">{tr($localeStore, localizeDigits(displayVerseStr))}</span
                            >{:else}<span class="loc-empty">{ayahAllLabel}</span>{/if}
                        </button>
                        {#if ayahOpen}
                            <div class="pop pop-ayah" role="dialog" aria-label={ayahPickerAriaLabel}>
                                <input
                                    bind:this={ayahFilterInput}
                                    bind:value={ayahQuery}
                                    on:keydown={onAyahKey}
                                    class="ayah-search"
                                    type="text"
                                    inputmode="numeric"
                                    placeholder={ayahSearchPlaceholder}
                                    autocomplete="off"
                                />
                                <div class="ayah-grid" role="listbox">
                                    {#each filteredAyahs as v (v)}
                                        <button type="button" class="ayah-cell"
                                            class:active={String(v) === displayVerseStr}
                                            role="option" aria-selected={String(v) === displayVerseStr}
                                            on:click={() => onAyahPick(v)}>{tr($localeStore, localizeDigits(v))}</button>
                                    {:else}
                                        <div class="empty">{ayahNoMatchesLabel}</div>
                                    {/each}
                                </div>
                            </div>
                        {/if}
                    </div>
                </div>
            {/if}
        </div>

        <!-- Right zone: save controls only -->
        <div class="zone zone-right">
            <div class="save-cluster">
                {#if hasReciter}
                    {#if showSavePreview}
                        <button class="action ghost" on:click={() => hideSavePreview()}>{cancelLabel}</button>
                        <button class="action primary" on:click={confirmSaveFromPreview}>{saveConfirmLabel}</button>
                    {:else}
                        <button
                            type="button"
                            class="utility"
                            class:on={$historyVisible}
                            title={historyToggleTitle}
                            aria-label={historyButtonLabel}
                            on:click={toggleHistory}
                        >
                            {#if $historyVisible}
                                <Icon name="arrow-left" size={14} />
                                <span class="util-label">{historyBackLabel}</span>
                            {:else}
                                <Icon name="history" size={14} />
                                <span class="util-label">{historyLabel}</span>
                            {/if}
                        </button>

                        {#if writeable}
                            <div class="save-group">
                                <button
                                    type="button"
                                    class="autosave-toggle"
                                    class:on={$autoSaveEnabled || $isSampleMode}
                                    aria-pressed={$autoSaveEnabled || $isSampleMode}
                                    disabled={$isSampleMode}
                                    title={autosaveTitle}
                                    on:click={() => toggleAutoSave(!$autoSaveEnabled)}
                                >
                                    <Icon name="bolt" size={12} />
                                    <span>{autosaveLabel}</span>
                                </button>

                                <button
                                    type="button"
                                    class="action save"
                                    class:primary={$isDirtyStore && !$autoSaveEnabled && !$isSampleMode}
                                    class:saved={!$isDirtyStore}
                                    class:auto-busy={($autoSaveEnabled || $isSampleMode) && $isDirtyStore}
                                    disabled={saveDisabled || $isSampleMode}
                                    on:click={onSegSaveClick}
                                >
                                    {#if !$isDirtyStore}
                                        <span class="save-glyph" aria-hidden="true">✓</span>
                                        <span>{savedGlyphLabel}</span>
                                    {:else if $autoSaveEnabled || $isSampleMode}
                                        <span class="save-pulse" aria-hidden="true"></span>
                                        <span>{autosavingLabel}</span>
                                    {:else}
                                        <span>{saveLabel}</span>
                                    {/if}
                                </button>
                            </div>
                        {/if}
                    {/if}
                {/if}
            </div>
        </div>
    </div>

    <audio bind:this={audioEl} preload="none"></audio>
</div>

{#if pickerOpen}
    <CombinationPicker
        open={pickerOpen}
        title={m.common_picker_switch_reciter_title()}
        on:select={onPickerSelect}
        on:close={() => (pickerOpen = false)}
    />
{/if}

<MarkReadyModal
    bind:open={markReadyOpen}
    slug={$selectedReciter || ''}
    onClose={() => (markReadyOpen = false)}
    onSubmitted={onMarkReadyDone}
/>

<style>
    .segs-footer {
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100vw;
        z-index: 100;
        display: flex;
        flex-direction: column;
        background: var(--panel);
        border-top: 1px solid var(--border-default);
        box-shadow: var(--shadow-pop);
        padding: 0 var(--s-4) var(--s-2); /* container-level padding matches BottomPlayer */
    }

    /* The <audio> element is the source of all playback DOM events. It
       has no visual representation; hidden via display:none keeps the
       intrinsic 0×0 size from contributing to the footer height. */
    audio {
        display: none;
    }

    /* Progress row: times flanking the scrub bar, matching the dashboard
       PlayerProgress format. Visible only when a segment range is queued
       (`active` class). The fill width is set inline via `style:width="{pct}%"`. */
    .progress {
        display: flex;
        align-items: center;
        gap: var(--s-3);
        flex-shrink: 0;
        /* no padding — container handles horizontal; no top gap matches BottomPlayer */
    }
    .progress:not(.active) {
        opacity: 0;
        pointer-events: none;
    }
    .time {
        font-family: var(--font-mono);
        font-size: 10.5px;
        color: var(--text-muted);
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
        flex-shrink: 0;
    }
    .bar {
        position: relative;
        flex: 1;
        height: 14px;
        cursor: pointer;
        display: flex;
        align-items: center;
        touch-action: none;
    }
    .track {
        position: relative;
        width: 100%;
        height: 3px;
        background: var(--canvas-inset);
        overflow: visible;
        transition: height var(--t-fast) ease;
    }
    .bar:hover .track,
    .bar:focus-visible .track {
        height: 5px;
    }
    .progress .fill {
        height: 100%;
        background: var(--accent);
        transition: width 80ms linear;
    }

    /* Three-column grid matching BottomPlayer: equal minmax(220px,1fr) flanks
       with an auto transport column — the play button (center of the symmetric
       5-button cluster) lands at the exact viewport center on every tab. */
    .row {
        display: grid;
        grid-template-columns: minmax(220px, 1fr) auto minmax(220px, 1fr);
        align-items: center;
        gap: var(--s-4);
        height: calc(var(--player-h, 72px) - 14px);
    }

    .zone {
        display: flex;
        align-items: center;
        gap: var(--s-3);
        min-width: 0;
        justify-content: space-between;
    }
    .zone-left {
        justify-content: flex-start;
        gap: var(--s-3);
        flex-wrap: nowrap;
    }
    .zone-right {
        justify-content: flex-end;
        gap: var(--s-3);
    }

    /* Transport center — mirrors BottomPlayer's .controls */
    .controls {
        display: flex;
        align-items: center;
        justify-content: center;
    }

    .sample-identity { display: inline-flex; align-items: center; gap: var(--s-2); min-width: 0; }
    .sample-tag {
        font-size: 9.5px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
        padding: 1px 6px; border-radius: 999px; background: var(--state-available-bg);
        color: var(--state-available-fg); border: 1px solid oklch(0.84 0.110 300 / 0.4);
    }
    .sample-name { color: var(--text-primary); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

    .reciter-actions {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        flex-shrink: 0;
    }

    /* Marked-ready status pill — replaces the entire action button group
       once the reviewer has submitted. Passive: no hover, no cursor. */
    .status-pill.marked-ready {
        display: inline-flex;
        flex-direction: column;
        align-items: flex-start;
        gap: 1px;
        padding: 4px 12px;
        font: 500 var(--fs-meta)/1.25 var(--font-sans);
        color: var(--state-warn-fg);
        background: oklch(from var(--state-warn-fg) l c h / 0.10);
        border: 1px solid oklch(from var(--state-warn-fg) l c h / 0.40);
        border-radius: var(--r-pill);
        letter-spacing: 0.01em;
        white-space: nowrap;
    }
    .status-pill.marked-ready .pill-hint {
        font-weight: 400;
        opacity: 0.75;
    }
    .save-group {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
    }

    /* ---------- Identity (chip wrapper) ----------
       The interactive button hosts the shared `<ReciterChip>` body.
       Padding + border + hover state live here so the chip itself
       stays presentation-only and can be reused inside non-button
       contexts (catalog rows, dashboard meta). */
    .identity {
        display: inline-flex;
        align-items: center;
        gap: var(--s-3);
        max-width: 100%;
        min-width: 0;
        flex: 0 1 auto;
        padding: 5px var(--s-3) 5px 5px;
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: 999px;
        color: inherit;
        cursor: pointer;
        font: inherit;
        transition:
            border-color var(--t-fast),
            background var(--t-fast);
    }
    .identity:hover {
        border-color: var(--border-strong);
        background: var(--panel);
    }
    .identity:focus-visible {
        outline: none;
        border-color: var(--accent);
    }
    .identity.placeholder {
        background: var(--accent-tint-soft);
        border-color: var(--accent-border-soft);
        color: var(--accent);
        padding: 8px var(--s-3);
        gap: var(--s-2);
    }
    .identity.placeholder:hover {
        background: var(--accent-tint);
    }

    .identity-placeholder-label {
        font-size: var(--fs-row);
        font-weight: 600;
        color: inherit;
        padding-inline-start: var(--s-2);
    }
    .identity-switch {
        margin-inline-start: var(--s-2);
        color: var(--text-faint);
        font-size: var(--fs-meta);
        transition: color var(--t-fast);
    }
    .identity:hover .identity-switch {
        color: var(--text-secondary);
    }
    .identity.placeholder .identity-switch {
        color: var(--accent);
    }

    /* ---------- Transport: 1fr [play 40px] 1fr ----------
       Fixed width anchors the 1fr columns so the play button sits exactly
       at the transport center — and since .controls centers the transport
       in the auto column, play lands at the viewport center. */
    .transport {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 40px minmax(0, 1fr);
        align-items: center;
        gap: var(--s-3);
        width: 440px;
    }
    .transport-left {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: var(--s-2);
    }
    .transport-right {
        position: relative;
        display: flex;
        align-items: center;
        justify-content: flex-start;
        gap: var(--s-2);
    }

    /* Play button — 40px accent circle, matches BottomPlayer's .btn.primary */
    .play-btn {
        width: 40px;
        height: 40px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--accent);
        color: var(--accent-fg);
        border: 0;
        border-radius: 50%;
        cursor: pointer;
        transition: background var(--t-fast);
        position: relative;
        flex-shrink: 0;
    }
    .play-btn:hover:not(:disabled) { background: var(--accent-strong); }
    .play-btn:disabled { opacity: 0.35; cursor: not-allowed; }

    /* Buffering: the play was committed but the first audible frame hasn't
       arrived (cold segment fetch). The shared <LoadingSpinner> replaces the
       glyph so the control doesn't falsely read as "playing" during the
       silent gap. */

    /* Secondary transport buttons — 36px, visually subordinate to the 40px play */
    .speed-cell {
        height: 36px;
        padding: 0 9px;
        font-family: var(--font-mono);
        font-size: 11.5px;
        font-variant-numeric: tabular-nums;
        color: var(--text-secondary);
        background: transparent;
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        cursor: pointer;
        transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast);
        min-width: 38px;
        text-align: center;
    }
    .speed-cell:hover { color: var(--text-primary); border-color: var(--border-strong); }
    .speed-cell.boosted { color: var(--accent); border-color: var(--accent-border); }
    .speed-cell.boosted:hover { background: var(--accent-tint); border-color: var(--accent); }

    .pref-cell {
        width: 36px;
        height: 36px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        color: var(--text-muted);
        background: transparent;
        border: 1px solid transparent;
        border-radius: var(--r-2);
        cursor: pointer;
        transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast);
    }
    .pref-cell:hover:not(:disabled) { color: var(--text-primary); background: var(--panel-2); border-color: var(--border-quiet); }
    .pref-cell.on { color: var(--accent); border-color: var(--accent-border-soft); background: var(--accent-tint-soft); }
    .pref-cell.on:hover { background: var(--accent-tint); border-color: var(--accent); }
    /* Chime toggle while autoplay is off — visibly inert, but still hoverable
       so its title can explain WHY it is unavailable. */
    .pref-cell:disabled { opacity: 0.4; cursor: not-allowed; }

    .loc-cell {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        height: 36px;
        padding: 0 10px;
        color: var(--text-secondary);
        font-size: var(--fs-meta);
        background: transparent;
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        cursor: pointer;
        transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast);
    }
    .loc-cell .loc-label {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-faint);
    }
    .loc-cell .loc-value {
        font-family: var(--font-mono);
        font-size: 12px;
        font-variant-numeric: tabular-nums;
        color: var(--text-primary);
    }
    .loc-cell .loc-empty {
        font-style: italic;
        font-size: 11px;
        opacity: 0.5;
    }
    .loc-cell:hover:not(:disabled) { border-color: var(--border-strong); color: var(--text-primary); background: var(--panel-2); }
    .loc-cell.has-value { border-color: var(--border-default); }
    .loc-cell:hover:not(:disabled) .loc-label { color: var(--text-muted); }
    .loc-cell:disabled { opacity: 0.35; cursor: not-allowed; }
    .loc-cell.live { color: var(--accent); border-color: var(--accent-border-soft); }
    .loc-cell.live .loc-label { color: var(--accent-soft); }

    .save-cluster {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
    }

    .pop {
        position: absolute;
        bottom: calc(100% + var(--s-2));
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        box-shadow: var(--shadow-pop);
        padding: var(--s-2);
        z-index: 50;
    }
    .pop-ayah {
        left: 50%;
        transform: translateX(-50%);
        width: min(360px, calc(100vw - var(--s-4) * 2));
        display: flex;
        flex-direction: column;
        max-height: min(420px, 60vh);
    }

    .ayah-search {
        background: var(--canvas-inset);
        border: 1px solid var(--border-quiet);
        color: var(--text-primary);
        padding: 6px var(--s-2);
        border-radius: var(--r-2);
        font-size: var(--fs-meta);
        outline: none;
        margin-bottom: var(--s-2);
    }
    .ayah-search:focus {
        border-color: var(--accent);
    }
    .ayah-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(44px, 1fr));
        gap: 4px;
        overflow-y: auto;
    }
    .ayah-cell {
        padding: 6px 0;
        background: transparent;
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        color: var(--text-secondary);
        font-family: var(--font-mono);
        font-variant-numeric: tabular-nums;
        font-size: var(--fs-meta);
        cursor: pointer;
        transition:
            border-color var(--t-fast),
            color var(--t-fast),
            background var(--t-fast);
    }
    .ayah-cell:hover {
        border-color: var(--border-strong);
        color: var(--text-primary);
        background: var(--panel-2);
    }
    .ayah-cell.active {
        border-color: var(--accent);
        color: var(--accent);
        background: var(--accent-tint);
    }
    .empty {
        grid-column: 1 / -1;
        padding: var(--s-3);
        text-align: center;
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }

    /* ---------- Actions ---------- */
    .action {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px var(--s-2);
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        color: var(--text-primary);
        font: inherit;
        font-size: var(--fs-meta);
        cursor: pointer;
        transition:
            background var(--t-fast),
            border-color var(--t-fast),
            color var(--t-fast);
    }
    .action:hover:not(:disabled) {
        background: var(--panel);
        border-color: var(--border-strong);
    }
    .action:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
    .action.ghost {
        background: transparent;
        color: var(--text-secondary);
    }
    .action.ghost:hover:not(:disabled) {
        background: var(--panel-2);
        color: var(--text-primary);
    }
    .action.ghost-accent {
        background: transparent;
        color: var(--accent);
        border-color: var(--accent-border-soft);
    }
    .action.ghost-accent:hover:not(:disabled) {
        background: var(--accent-tint);
        border-color: var(--accent);
    }
    .action.primary {
        background: var(--accent);
        border-color: var(--accent);
        color: var(--accent-fg);
        font-weight: 500;
    }
    .action.primary:hover:not(:disabled) {
        background: var(--accent-strong);
        border-color: var(--accent-strong);
    }

    .action.save {
        min-width: 104px;
        justify-content: center;
        padding-inline: var(--s-3);
    }
    .action.save.saved {
        background: transparent;
        border-color: var(--border-quiet);
        color: var(--text-muted);
    }
    .save-glyph {
        color: var(--ok-fg);
        font-weight: 600;
    }
    .action.save.auto-busy {
        background: transparent;
        border-color: var(--accent-border-soft);
        color: var(--accent);
    }
    .save-pulse {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--accent);
        animation: pulse 1.4s ease-out-quart infinite;
    }
    @keyframes pulse {
        0%,
        100% {
            opacity: 0.35;
        }
        50% {
            opacity: 1;
        }
    }

    .autosave-toggle {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px var(--s-2);
        background: transparent;
        border: 1px dashed var(--border-quiet);
        border-radius: var(--r-2);
        color: var(--text-muted);
        cursor: pointer;
        font: inherit;
        font-size: var(--fs-meta);
        transition:
            color var(--t-fast),
            border-color var(--t-fast),
            background var(--t-fast);
    }
    .autosave-toggle:hover {
        color: var(--text-primary);
        border-color: var(--border-strong);
    }
    .autosave-toggle.on {
        border-style: solid;
        border-color: var(--accent-border);
        background: var(--accent-tint);
        color: var(--accent);
    }

    .utility {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px var(--s-2);
        background: transparent;
        border: 1px solid transparent;
        border-radius: var(--r-2);
        color: var(--text-muted);
        cursor: pointer;
        font: inherit;
        font-size: var(--fs-meta);
        transition:
            color var(--t-fast),
            background var(--t-fast),
            border-color var(--t-fast);
    }
    .utility:hover {
        color: var(--text-primary);
        background: var(--panel-2);
        border-color: var(--border-quiet);
    }
    .utility.on {
        color: var(--accent);
        background: var(--accent-tint);
        border-color: var(--accent-border-soft);
    }

    /* ---------- Responsive ---------- */
    @media (max-width: 960px) {
        .row {
            grid-template-columns: 1fr;
            height: auto;
            padding: var(--s-2) 0;
            gap: var(--s-2);
        }
        .zone-left  { justify-content: flex-start; flex-wrap: wrap; }
        .zone-right { justify-content: flex-start; flex-wrap: wrap; }
        .pop-ayah {
            left: 0;
            right: 0;
            transform: none;
            width: auto;
        }
    }
    @media (max-width: 540px) {
        .util-label {
            display: none;
        }
        .action.save {
            min-width: 92px;
        }
    }
</style>
