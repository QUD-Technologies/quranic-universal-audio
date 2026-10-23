<script lang="ts">
    /**
     * Al-Fatiha Basmala voice call — a compact notice above the chapter-1 list.
     *
     * The aligner scores whether Al-Fatiha's Basmala is in the reciter's own
     * voice; a holder of `segments.confirm_basmala_voice` listens to the clip
     * and settles (or overrules) it. Playback rides an isolated preview context
     * (its own hidden `<audio>` + bounded AudioRange, as SavePreview does), so
     * it never disturbs the main list's playback state. Hidden for everyone
     * else, on any other chapter, and for a reciter with no voice-scored Basmala.
     */
    import { onDestroy, untrack } from 'svelte';

    import * as m from '$lib/paraglide/messages';

    import AudioElement from '../../../../lib/components/AudioElement.svelte';
    import { can } from '../../../../lib/stores/capabilities';
    import type { SpecialEntry } from '../../../../lib/types/generated/schemas';
    import { segAllData, segData, selectedChapter, selectedReciter } from '../../stores/chapter';
    import { segPort } from '../../stores/playback';
    import { createPreviewPlaybackContext } from '../../utils/playback/preview';
    import {
        BASMALA_VOICE_CAPABILITY,
        BasmalaVoiceError,
        confirmBasmalaVoice,
        FATIHA_CHAPTER,
        fatihaBasmalaEntry,
        loadSpecials,
    } from '../../utils/specials/basmala-voice';

    /** Preview-context row id for the one clip this notice plays. */
    const PREVIEW_UID = 'specials:basmala-voice';

    const canConfirm = can(BASMALA_VOICE_CAPABILITY);
    const previewCtx = createPreviewPlaybackContext({ persistPeaks: false });
    const playingSeg = previewCtx.playingSeg;
    onDestroy(() => previewCtx.dispose());

    let entry = $state<SpecialEntry | null>(null);
    let loadedFor = $state('');
    let loadFailed = $state(false);
    let saving = $state(false);
    let saveError = $state<string | null>(null);
    let changing = $state(false);
    let audio = $state<AudioElement>();
    let canvasEl = $state<HTMLCanvasElement>();

    const reciter = $derived($selectedReciter);
    const active = $derived($canConfirm && !!reciter && $selectedChapter === String(FATIHA_CHAPTER));
    const audioUrl = $derived(
        $segAllData?.audio_by_chapter?.[String(FATIHA_CHAPTER)] ?? $segData?.audio_url ?? '',
    );
    const confirmed = $derived(entry?.voice_confirmed ?? null);
    const playing = $derived($playingSeg?.uid === PREVIEW_UID);

    const message = $derived.by((): string => {
        if (confirmed !== null && !changing) {
            return confirmed ? m.segments_basmala_voice_saved_same() : m.segments_basmala_voice_saved_different();
        }
        if (entry?.voice_verdict === 'same') return m.segments_basmala_voice_prompt_same();
        if (entry?.voice_verdict === 'different') return m.segments_basmala_voice_prompt_different();
        return m.segments_basmala_voice_prompt_unsure();
    });

    // Fetch the reciter's specials once per reciter while the notice applies.
    $effect(() => {
        if (!active) return;
        const slug = reciter;
        if (untrack(() => loadedFor) === slug) return;
        entry = null;
        loadFailed = false;
        saveError = null;
        changing = false;
        let stale = false;
        loadSpecials(slug)
            .then((doc) => {
                if (stale) return;
                entry = fatihaBasmalaEntry(doc);
                loadedFor = slug;
            })
            .catch((err: unknown) => {
                if (stale) return;
                console.error('[basmala-voice] specials load failed', slug, err);
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

    $effect(() => {
        if (!canvasEl || !entry || !audioUrl) return;
        previewCtx.registerRow(
            PREVIEW_UID, canvasEl, audioUrl, entry.start_ms, entry.end_ms,
            undefined, undefined, undefined, FATIHA_CHAPTER,
        );
        return () => previewCtx.deregisterRow(PREVIEW_UID);
    });

    function togglePlay(): void {
        // One voice at a time: the main list's audio yields to the clip.
        if (!segPort.paused) segPort.pause();
        previewCtx.toggle(PREVIEW_UID);
    }

    async function decide(sameVoice: boolean): Promise<void> {
        const current = entry;
        if (!current || saving) return;
        saving = true;
        saveError = null;
        try {
            await confirmBasmalaVoice(reciter, current.start_ms, sameVoice);
            entry = { ...current, voice_confirmed: sameVoice };
            changing = false;
        } catch (err) {
            if (!(err instanceof BasmalaVoiceError)) console.error('[basmala-voice] save failed', err);
            saveError = err instanceof BasmalaVoiceError ? err.message : m.errors_generic_fallback();
        } finally {
            saving = false;
        }
    }
</script>

{#if active && loadFailed}
    <p class="bv-notice bv-load-error" role="status">{m.segments_basmala_voice_load_error()}</p>
{:else if active && entry}
    <section
        class="bv-notice"
        class:bv-pending={confirmed === null && entry.voice_verdict === 'needs_confirmation'}
        aria-label={m.segments_basmala_voice_label()}
    >
        <AudioElement bind:this={audio} preload="metadata" />
        <button
            type="button"
            class="bv-btn bv-play"
            aria-pressed={playing}
            disabled={!audioUrl}
            onclick={togglePlay}
        >
            <span aria-hidden="true">{playing ? '❚❚' : '▶'}</span>
            {playing ? m.segments_basmala_voice_pause() : m.segments_basmala_voice_play()}
        </button>
        <canvas bind:this={canvasEl} class="bv-wave" width="160" height="28" aria-hidden="true"></canvas>
        <p class="bv-text">{message}</p>
        <div class="bv-actions">
            {#if confirmed !== null && !changing}
                <button type="button" class="bv-btn" onclick={() => (changing = true)}>
                    {m.segments_basmala_voice_change()}
                </button>
            {:else}
                <button
                    type="button"
                    class="bv-btn"
                    class:primary={entry.voice_verdict === 'same'}
                    disabled={saving}
                    onclick={() => decide(true)}
                >{m.segments_basmala_voice_same()}</button>
                <button
                    type="button"
                    class="bv-btn"
                    class:primary={entry.voice_verdict === 'different'}
                    disabled={saving}
                    onclick={() => decide(false)}
                >{m.segments_basmala_voice_different()}</button>
            {/if}
        </div>
        {#if saving}
            <span class="bv-status" role="status">{m.segments_basmala_voice_saving()}</span>
        {/if}
        {#if saveError}
            <span class="bv-error" role="alert">{saveError}</span>
        {/if}
    </section>
{/if}

<style>
    .bv-notice {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: var(--s-2);
        margin: 0 0 var(--s-2);
        padding: var(--s-2) var(--s-3);
        background: var(--panel-2);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        font-size: var(--fs-meta);
        color: var(--text-secondary);
    }
    .bv-notice.bv-pending {
        border-inline-start: 3px solid var(--accent);
    }
    .bv-load-error {
        color: var(--text-muted);
    }
    .bv-wave {
        width: 160px;
        height: 28px;
        flex: 0 0 auto;
        border-radius: var(--r-1);
        background: var(--canvas-inset);
    }
    .bv-text {
        flex: 1 1 220px;
        min-width: 0;
        margin: 0;
    }
    .bv-actions {
        display: inline-flex;
        gap: var(--s-1);
    }
    .bv-btn {
        background: transparent;
        border: 1px solid var(--border-quiet);
        color: var(--text-secondary);
        font: inherit;
        font-size: var(--fs-meta);
        padding: 3px 10px;
        border-radius: var(--r-1);
        cursor: pointer;
        white-space: nowrap;
    }
    .bv-btn:hover:not(:disabled) {
        border-color: var(--border-default);
        color: var(--text-primary);
    }
    .bv-btn:disabled {
        opacity: 0.45;
        cursor: not-allowed;
    }
    .bv-btn.primary {
        border-color: var(--accent);
        color: var(--accent);
    }
    .bv-play {
        display: inline-flex;
        align-items: center;
        gap: var(--s-1);
    }
    .bv-status {
        color: var(--text-muted);
    }
    .bv-error {
        color: var(--state-error-fg);
    }
</style>
