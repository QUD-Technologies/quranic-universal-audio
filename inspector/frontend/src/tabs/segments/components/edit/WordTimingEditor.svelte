<script lang="ts">
    import { onDestroy, untrack } from 'svelte';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';

    type Boundary = { start_ms: number; end_ms: number };
    type DragKind = 'start' | 'end' | 'word';

    let { words, labels, startMs, endMs, width, onSave, onCancel }: {
        words: Boundary[];
        labels: string[];
        startMs: number;
        endMs: number;
        width: number;
        onSave: (_boundaries: Boundary[]) => Promise<boolean>;
        onCancel: () => void;
    } = $props();

    let draft = $state<Boundary[]>(untrack(() => words.map(w => ({ start_ms: w.start_ms, end_ms: w.end_ms }))));
    let saving = $state(false);
    let error = $state('');
    let drag: { index: number; kind: DragKind; atX: number; original: Boundary } | null = null;
    const minWordMs = 20;
    let duration = $derived(Math.max(1, endMs - startMs));
    const x = (ms: number) => ((ms - startMs) / duration) * width;

    function stopDrag() {
        drag = null;
        window.removeEventListener('pointermove', moveDrag);
        window.removeEventListener('pointerup', stopDrag);
        window.removeEventListener('pointercancel', stopDrag);
    }
    onDestroy(stopDrag);

    function beginDrag(event: PointerEvent, index: number, kind: DragKind) {
        if (saving) return;
        event.preventDefault();
        event.stopPropagation();
        drag = { index, kind, atX: event.clientX, original: { ...draft[index]! } };
        window.addEventListener('pointermove', moveDrag);
        window.addEventListener('pointerup', stopDrag);
        window.addEventListener('pointercancel', stopDrag);
    }

    function moveDrag(event: PointerEvent) {
        if (!drag) return;
        const { index, kind, atX, original } = drag;
        const delta = Math.round((event.clientX - atX) * duration / width);
        const previousEnd = index ? draft[index - 1]!.end_ms : startMs;
        const nextStart = index + 1 < draft.length ? draft[index + 1]!.start_ms : endMs;
        let start = original.start_ms;
        let end = original.end_ms;
        if (kind === 'start') start = Math.max(previousEnd, Math.min(end - minWordMs, start + delta));
        if (kind === 'end') end = Math.min(nextStart, Math.max(start + minWordMs, end + delta));
        if (kind === 'word') {
            const offset = Math.max(previousEnd - start, Math.min(nextStart - end, delta));
            start += offset;
            end += offset;
        }
        draft[index] = { start_ms: start, end_ms: end };
        error = '';
    }

    async function save() {
        if (saving || !draft.some((w, i) => w.start_ms !== words[i]?.start_ms || w.end_ms !== words[i]?.end_ms)) return;
        saving = true;
        error = '';
        try {
            const ok = await onSave(draft.map(w => ({ ...w })));
            if (!ok) error = tr($localeStore, m.segments_word_edit_error());
        } catch (cause) {
            error = cause instanceof Error ? cause.message : tr($localeStore, m.segments_word_edit_error());
        } finally {
            saving = false;
        }
    }
</script>

<div class="word-timing-overlay" style:width={`${width}px`}>
    {#each draft as word, i}
        {@const left = x(word.start_ms)}
        {@const right = x(word.end_ms)}
        <div class="word-range" style:left={`${left}px`} style:width={`${Math.max(2, right - left)}px`} aria-hidden="true"></div>
        <button class="word-boundary start" type="button" style:left={`${left}px`}
            aria-label={tr($localeStore, m.segments_word_edit_start_label({ number: String(i + 1), word: labels[i] ?? '' }))}
            onpointerdown={(e) => beginDrag(e, i, 'start')}></button>
        <button class="word-boundary end" type="button" style:left={`${right}px`}
            aria-label={tr($localeStore, m.segments_word_edit_end_label({ number: String(i + 1), word: labels[i] ?? '' }))}
            onpointerdown={(e) => beginDrag(e, i, 'end')}></button>
        <button class="word-label" type="button" dir="rtl"
            style:left={`${(left + right) / 2}px`}
            title={`${Math.round(word.start_ms)}–${Math.round(word.end_ms)} ms — drag to move whole word`}
            aria-label={tr($localeStore, m.segments_word_edit_move_label({ number: String(i + 1), word: labels[i] ?? '' }))}
            onpointerdown={(e) => beginDrag(e, i, 'word')}>
            <span class="arabic">{labels[i] ?? ''}</span>
            <span class="timing">{((word.start_ms - startMs) / 1000).toFixed(2)}–{((word.end_ms - startMs) / 1000).toFixed(2)}s</span>
        </button>
    {/each}
    <div class="word-timing-actions">
        <span>{tr($localeStore, m.segments_word_edit_hint())}</span>
        {#if error}<span class="word-timing-error" role="alert">{error}</span>{/if}
        <button type="button" class="btn btn-sm" onclick={onCancel} disabled={saving}>{tr($localeStore, m.common_action_cancel())}</button>
        <button type="button" class="btn btn-sm word-timing-save" onclick={save} disabled={saving || !draft.some((w, i) => w.start_ms !== words[i]?.start_ms || w.end_ms !== words[i]?.end_ms)}>{tr($localeStore, saving ? m.segments_word_edit_saving() : m.segments_word_edit_save())}</button>
    </div>
</div>

<style>
    .word-timing-overlay { position: absolute; inset: 0 auto auto 0; height: 168px; pointer-events: none; }
    .word-range { position: absolute; top: 0; height: 60px; background: var(--accent); opacity: 0.11; pointer-events: none; }
    .word-boundary { position: absolute; top: 0; width: 14px; height: 68px; padding: 0; border: 0; background: transparent; transform: translateX(-50%); cursor: ew-resize; pointer-events: auto; touch-action: none; z-index: 2; }
    .word-boundary::before { content: ''; display: block; position: absolute; inset: 0 6px; background: var(--accent); box-shadow: 0 0 0 1px var(--panel); }
    .word-boundary.end::before { background: var(--state-warn-fg); }
    .word-boundary:hover::before, .word-boundary:focus-visible::before { inset-inline: 5px; }
    .word-label { position: absolute; top: 75px; transform: translateX(-50%); width: max-content; min-width: 76px; max-width: 180px; padding: 2px 5px; display: flex; flex-direction: column; align-items: center; border: 1px solid var(--border-default); border-radius: var(--r-2); background: var(--panel); color: var(--text-primary); cursor: grab; pointer-events: auto; touch-action: none; }
    .word-label:active { cursor: grabbing; }
    .arabic { font-family: var(--font-quran, 'DigitalKhatt', 'Traditional Arabic', serif); font-size: 1.5rem; line-height: 1.6; white-space: nowrap; }
    .timing { font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); direction: ltr; }
    .word-timing-actions { position: absolute; top: 136px; left: 0; display: flex; align-items: center; gap: 8px; width: 100%; font-size: 11px; color: var(--text-secondary); pointer-events: auto; }
    .word-timing-actions > span:first-child { margin-right: auto; }
    .word-timing-error { color: var(--state-error-fg); }
    .word-timing-save { background: var(--accent); color: var(--accent-fg); }
</style>
