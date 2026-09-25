<script lang="ts">
    import { onDestroy, untrack } from 'svelte';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import { contiguousWordDraft, moveWordBlock, moveWordBoundary, type WordBounds } from '../../utils/samples/word-timing-draft';

    type DragKind = 'boundary' | 'word';

    let { words, labels, startMs, endMs, width, lockedIndex, activeIndex = null, onChange, onSeek, onLock }: {
        words: WordBounds[];
        labels: string[];
        startMs: number;
        endMs: number;
        width: number;
        lockedIndex: number | null;
        activeIndex?: number | null;
        onChange: (_boundaries: WordBounds[]) => void;
        onSeek: (_timeMs: number) => void;
        onLock: (_index: number) => void;
    } = $props();

    let draft = $state<WordBounds[]>(untrack(() => contiguousWordDraft(words)));
    let drag: { index: number; kind: DragKind; atX: number; original: WordBounds[] } | null = null;
    let duration = $derived(Math.max(1, endMs - startMs));
    const x = (ms: number) => ((ms - startMs) / duration) * width;

    function stopDrag(event?: PointerEvent) {
        if (drag?.kind === 'word' && event?.type === 'pointerup' && Math.abs(event.clientX - drag.atX) < 4) {
            onSeek(draft[drag.index]!.start_ms);
        }
        drag = null;
        window.removeEventListener('pointermove', moveDrag);
        window.removeEventListener('pointerup', stopDrag);
        window.removeEventListener('pointercancel', stopDrag);
    }
    onDestroy(() => stopDrag());

    function beginDrag(event: PointerEvent, index: number, kind: DragKind) {
        event.preventDefault();
        event.stopPropagation();
        drag = { index, kind, atX: event.clientX, original: draft.map(word => ({ ...word })) };
        window.addEventListener('pointermove', moveDrag);
        window.addEventListener('pointerup', stopDrag);
        window.addEventListener('pointercancel', stopDrag);
    }

    function moveDrag(event: PointerEvent) {
        if (!drag) return;
        const { index, kind, atX, original } = drag;
        const delta = Math.round((event.clientX - atX) * duration / width);
        const next = kind === 'word'
            ? moveWordBlock(original, index, delta, startMs, endMs)
            : moveWordBoundary(original, index,
                (index === original.length ? original[index - 1]!.end_ms : original[index]!.start_ms) + delta,
                startMs, endMs);
        if (next.every((word, i) => word.start_ms === draft[i]!.start_ms && word.end_ms === draft[i]!.end_ms)) return;
        draft = next;
        onChange(next);
    }
</script>

<div class="word-timing-overlay" style:width={`${width}px`}>
    {#if draft.length}
        <button class="word-boundary outer" type="button" style:left={`${x(draft[0]!.start_ms)}px`}
            aria-label={tr($localeStore, m.segments_word_edit_start_label({ number: '1', word: labels[0] ?? '' }))}
            onpointerdown={(e) => beginDrag(e, 0, 'boundary')}></button>
    {/if}
    {#each draft as word, i}
        {@const left = x(word.start_ms)}
        {@const right = x(word.end_ms)}
        <div class="word-range" class:is-locked={lockedIndex === i} class:is-active={activeIndex === i} style:left={`${left}px`} style:width={`${Math.max(2, right - left)}px`} aria-hidden="true"></div>
        <button class="word-boundary" class:outer={i === draft.length - 1} type="button" style:left={`${right}px`}
            aria-label={i === draft.length - 1
                ? tr($localeStore, m.segments_word_edit_end_label({ number: String(i + 1), word: labels[i] ?? '' }))
                : tr($localeStore, m.segments_word_shared_boundary_label({ left: labels[i] ?? '', right: labels[i + 1] ?? '' }))}
            onpointerdown={(e) => beginDrag(e, i + 1, 'boundary')}></button>
        <div class="word-card" class:is-locked={lockedIndex === i} class:is-active={activeIndex === i} style:left={`${(left + right) / 2}px`} dir="rtl">
            <button class="word-lock" class:is-locked={lockedIndex === i} type="button"
                aria-pressed={lockedIndex === i}
                aria-label={tr($localeStore, lockedIndex === i
                    ? m.segments_word_unlock_label({ word: labels[i] ?? '' })
                    : m.segments_word_lock_label({ word: labels[i] ?? '' }))}
                title={tr($localeStore, lockedIndex === i ? m.segments_word_unlock_hint() : m.segments_word_lock_hint())}
                onclick={(e) => { e.stopPropagation(); onLock(i); }}>
                <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                    <rect x="3.5" y="7" width="9" height="7" rx="1.3" />
                    {#if lockedIndex === i}<path d="M5.3 7V4.7a2.7 2.7 0 0 1 5.4 0V7" />
                    {:else}<path d="M5.3 7V4.7a2.7 2.7 0 0 1 5.4 0" />{/if}
                </svg>
            </button>
            <button class="word-seek" type="button"
                title={tr($localeStore, m.segments_word_seek_hint())}
                aria-label={tr($localeStore, m.segments_word_edit_move_label({ number: String(i + 1), word: labels[i] ?? '' }))}
                onpointerdown={(e) => beginDrag(e, i, 'word')}>
                <span class="arabic">{labels[i] ?? ''}</span>
                <span class="timing">{((word.start_ms - startMs) / 1000).toFixed(2)}–{((word.end_ms - startMs) / 1000).toFixed(2)}s</span>
            </button>
        </div>
    {/each}
</div>

<style>
    .word-timing-overlay { position: absolute; inset: 0 auto auto 0; height: 140px; pointer-events: none; }
    .word-range { position: absolute; top: 0; height: 60px; background: var(--accent); opacity: 0.12; pointer-events: none; }
    .word-range.is-locked { opacity: 0.25; }
    .word-range.is-active { opacity: 0.35; }
    .word-boundary { position: absolute; top: 0; width: 14px; height: 68px; padding: 0; border: 0; background: transparent; transform: translateX(-50%); cursor: ew-resize; pointer-events: auto; touch-action: none; z-index: 2; }
    .word-boundary::before { content: ''; display: block; position: absolute; inset: 0 6px; background: var(--accent); box-shadow: 0 0 0 1px var(--panel); }
    .word-boundary.outer::before { opacity: 0.65; }
    .word-boundary:hover::before, .word-boundary:focus-visible::before { inset-inline: 5px; }
    .word-card { position: absolute; top: 77px; transform: translateX(-50%); width: max-content; min-width: 76px; max-width: 180px; border: 1px solid var(--border-default); border-radius: var(--r-2); background: var(--panel); color: var(--text-primary); pointer-events: auto; }
    .word-card.is-locked { border-color: var(--accent); background: var(--panel-2); }
    .word-card.is-active { border-color: var(--accent); background: var(--accent); color: var(--accent-fg); box-shadow: 0 0 0 2px var(--accent); }
    .word-card.is-active .timing { color: inherit; opacity: 0.85; }
    .word-seek { display: flex; flex-direction: column; align-items: center; width: 100%; padding: 2px 6px; border: 0; background: transparent; color: inherit; cursor: grab; touch-action: none; }
    .word-seek:active { cursor: grabbing; }
    .word-lock { position: absolute; top: -10px; right: -10px; z-index: 3; display: grid; place-items: center; width: 26px; height: 26px; padding: 0; border: 1px solid var(--accent); border-radius: 50%; background: var(--elevated); color: var(--accent); cursor: pointer; }
    .word-lock:hover, .word-lock:focus-visible, .word-lock.is-locked { border-color: var(--accent); background: var(--accent); color: var(--accent-fg); }
    .arabic { font-family: var(--font-quran, 'DigitalKhatt', 'Traditional Arabic', serif); font-size: 1.5rem; line-height: 1.6; white-space: nowrap; }
    .timing { font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); direction: ltr; }
</style>
