<script lang="ts">
    /**
     * The chapter's recorded openers and closers (Isti'adha, Basmala, Amin …),
     * read-only, above the segment list.
     *
     * The align pipeline keeps these out of `detailed.json` and records them in
     * `specials.json` (`docs/reference/specials.md`), so the list alone reads as if
     * the chapter's Basmala were cropped. This strip shows each one with its text,
     * time span and a play button. Entries that already are segment rows — Al-Fatiha's
     * 1:1 verse and rows a reviewer labelled with a special's name — are left to the
     * list. Playback rides an isolated preview context, like the voice notice.
     */
    import { onDestroy, untrack } from 'svelte';

    import * as m from '$lib/paraglide/messages';

    import AudioElement from '../../../../lib/components/AudioElement.svelte';
    import { specialLabel } from '../../../../lib/recitation-animation/special-label';
    import { quranRefs } from '../../../../lib/refs/quran-refs';
    import type { SegSpecialsResponse, SpecialEntry } from '../../../../lib/types/generated/schemas';
    import { segAllData, segData, selectedChapter, selectedReciter } from '../../stores/chapter';
    import { segPort } from '../../stores/playback';
    import { formatTimeMs, specialTextForRef } from '../../utils/data/references';
    import { createPreviewPlaybackContext } from '../../utils/playback/preview';
    import { listedSpecials, loadSpecials, specialPreviewUid } from '../../utils/specials/chapter-specials';

    const previewCtx = createPreviewPlaybackContext({ persistPeaks: false });
    const playingSeg = previewCtx.playingSeg;
    onDestroy(() => previewCtx.dispose());

    let doc = $state<SegSpecialsResponse | null>(null);
    let loadedFor = $state('');
    let loadFailed = $state(false);
    let audio = $state<AudioElement>();

    const reciter = $derived($selectedReciter);
    const chapter = $derived(Number($selectedChapter) || 0);
    const entries = $derived(chapter ? listedSpecials(doc, chapter) : []);
    const audioUrl = $derived(
        $segAllData?.audio_by_chapter?.[String(chapter)] ?? $segData?.audio_url ?? '',
    );

    // Fetch the reciter's specials once per reciter.
    $effect(() => {
        const slug = reciter;
        if (!slug || untrack(() => loadedFor) === slug) return;
        doc = null;
        loadFailed = false;
        let stale = false;
        loadSpecials(slug)
            .then((res) => {
                if (stale) return;
                doc = res;
                loadedFor = slug;
            })
            .catch((err: unknown) => {
                if (stale) return;
                console.error('[chapter-specials] specials load failed', slug, err);
                loadFailed = true;
                loadedFor = slug;
            });
        return () => {
            stale = true;
        };
    });

    $effect(() => {
        if (audio) previewCtx.attachAudioEl(audio.element());
    });

    /** Register one entry's waveform canvas as a preview row while it is mounted. */
    function previewRow(canvas: HTMLCanvasElement, entry: SpecialEntry) {
        const uid = specialPreviewUid(chapter, entry);
        if (audioUrl) {
            previewCtx.registerRow(
                uid, canvas, audioUrl, entry.start_ms, entry.end_ms,
                undefined, undefined, undefined, chapter,
            );
        }
        return { destroy: () => previewCtx.deregisterRow(uid) };
    }

    function togglePlay(entry: SpecialEntry): void {
        // One voice at a time: the main list's audio yields to the clip.
        if (!segPort.paused) segPort.pause();
        previewCtx.toggle(specialPreviewUid(chapter, entry));
    }
</script>

{#if chapter && loadFailed}
    <p class="cs-strip cs-load-error" role="status">{m.segments_specials_load_error()}</p>
{:else if entries.length > 0}
    <section class="cs-strip" aria-label={m.segments_specials_label()}>
        <AudioElement bind:this={audio} preload="metadata" />
        <p class="cs-hint">{m.segments_specials_hint()}</p>
        {#each entries as entry (`${entry.type}:${entry.start_ms}`)}
            {@const playing = $playingSeg?.uid === specialPreviewUid(chapter, entry)}
            <div class="cs-row">
                <button
                    type="button"
                    class="cs-play"
                    aria-pressed={playing}
                    aria-label={playing ? m.segments_specials_pause() : m.segments_specials_play()}
                    disabled={!audioUrl}
                    onclick={() => togglePlay(entry)}
                >
                    <span aria-hidden="true">{playing ? '❚❚' : '▶'}</span>
                </button>
                <canvas use:previewRow={entry} class="cs-wave" width="160" height="28" aria-hidden="true"></canvas>
                <span class="cs-name">{specialLabel(entry.type)}</span>
                <span class="cs-times">{formatTimeMs(entry.start_ms)} – {formatTimeMs(entry.end_ms)}</span>
                {#if specialTextForRef(entry.type, $quranRefs)}
                    <span class="cs-text" lang="ar" dir="rtl">{specialTextForRef(entry.type, $quranRefs)}</span>
                {/if}
            </div>
        {/each}
    </section>
{/if}

<style>
    .cs-strip {
        display: flex;
        flex-direction: column;
        gap: var(--s-1);
        margin: 0 0 var(--s-2);
        padding: var(--s-2) var(--s-3);
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        font-size: var(--fs-meta);
        color: var(--text-secondary);
    }
    .cs-load-error {
        color: var(--text-muted);
    }
    .cs-hint {
        margin: 0;
        color: var(--text-muted);
    }
    .cs-row {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: var(--s-2);
        min-width: 0;
    }
    .cs-play {
        background: transparent;
        border: 1px solid var(--border-quiet);
        color: var(--text-secondary);
        font: inherit;
        padding: 3px 10px;
        border-radius: var(--r-1);
        cursor: pointer;
    }
    .cs-play:hover:not(:disabled) {
        border-color: var(--border-default);
        color: var(--text-primary);
    }
    .cs-play:disabled {
        opacity: 0.45;
        cursor: not-allowed;
    }
    .cs-wave {
        width: 160px;
        height: 28px;
        flex: 0 0 auto;
        border-radius: var(--r-1);
        background: var(--canvas-inset);
    }
    .cs-name {
        color: var(--text-primary);
    }
    .cs-times {
        font-variant-numeric: tabular-nums;
        color: var(--text-muted);
    }
    .cs-text {
        flex: 1 1 200px;
        min-width: 0;
        font-family: var(--font-quran, 'DigitalKhatt', 'Traditional Arabic', 'Scheherazade New', 'Amiri', serif);
        font-size: 1.3rem;
        color: var(--text-primary);
        text-align: end;
    }
</style>
