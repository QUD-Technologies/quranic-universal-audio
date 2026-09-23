<script lang="ts">
    /**
     * Samples sub-tab: upload form + the shared list of alignment samples.
     * Polls while any sample is still processing so the Open button unlocks
     * without a reload.
     */
    import { onDestroy, onMount } from 'svelte';

    import * as m from '$lib/paraglide/messages';
    import { listSamples, SampleApiError, uploadSample } from '../../../../lib/api/samples';
    import { pushToast } from '../../../../lib/stores/toast';
    import { samples } from '../../stores/samples';
    import SampleRow from './SampleRow.svelte';

    interface Props {
        onOpen: (_slug: string) => void;
    }
    const { onOpen }: Props = $props();

    const POLL_MS = 5000;

    let name = $state('');
    let audioFile = $state<File | null>(null);
    let jsonFile = $state<File | null>(null);
    let busy = $state(false);
    let error = $state<string | null>(null);
    let listError = $state<string | null>(null);
    let audioInput = $state<HTMLInputElement | null>(null);
    let jsonInput = $state<HTMLInputElement | null>(null);

    const canSubmit = $derived(!busy && name.trim().length > 0 && !!audioFile && !!jsonFile);

    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    async function refresh(): Promise<void> {
        try {
            const list = await listSamples();
            samples.set(list);
            listError = null;
            if (pollTimer) clearTimeout(pollTimer);
            pollTimer = list.some((s) => s.status === 'processing')
                ? setTimeout(() => void refresh(), POLL_MS)
                : null;
        } catch (e) {
            listError = e instanceof SampleApiError ? e.message : String(e);
            if (pollTimer) clearTimeout(pollTimer);
            pollTimer = null;
        }
    }

    async function submit(ev: SubmitEvent): Promise<void> {
        ev.preventDefault();
        if (!canSubmit || !audioFile || !jsonFile) return;
        busy = true;
        error = null;
        try {
            await uploadSample(name.trim(), audioFile, jsonFile);
            pushToast({ kind: 'success', text: m.segments_samples_uploaded_toast() });
            name = '';
            audioFile = null;
            jsonFile = null;
            if (audioInput) audioInput.value = '';
            if (jsonInput) jsonInput.value = '';
            await refresh();
        } catch (e) {
            error = e instanceof SampleApiError ? e.message : String(e);
        } finally {
            busy = false;
        }
    }

    function pick(setter: (_f: File | null) => void) {
        return (ev: Event) => {
            const input = ev.currentTarget as HTMLInputElement;
            setter(input.files?.[0] ?? null);
        };
    }

    onMount(() => void refresh());
    onDestroy(() => {
        if (pollTimer) clearTimeout(pollTimer);
    });
</script>

<section class="samples">
    <h2 class="title">{m.segments_samples_title()}</h2>
    <p class="intro">{m.segments_samples_intro()}</p>

    <form class="upload" onsubmit={submit}>
        <label class="field">
            <span>{m.segments_samples_name_label()}</span>
            <input type="text" bind:value={name} maxlength="120" required disabled={busy} />
        </label>
        <label class="field">
            <span>{m.segments_samples_audio_label()}</span>
            <input
                type="file"
                accept=".mp3,.wav,.flac,.ogg,audio/*"
                bind:this={audioInput}
                onchange={pick((f) => (audioFile = f))}
                disabled={busy}
            />
        </label>
        <label class="field">
            <span>{m.segments_samples_json_label()}</span>
            <input
                type="file"
                accept=".json,application/json"
                bind:this={jsonInput}
                onchange={pick((f) => (jsonFile = f))}
                disabled={busy}
            />
        </label>
        <button type="submit" class="submit" disabled={!canSubmit}>
            {busy ? m.segments_samples_uploading() : m.segments_samples_upload_button()}
        </button>
        {#if error}
            <p class="error" role="alert">{error}</p>
        {/if}
    </form>

    {#if listError}
        <p class="load-error" role="alert">{listError}</p>
    {:else if $samples.length === 0}
        <p class="empty">{m.segments_samples_empty()}</p>
    {:else}
        <div class="list" role="table">
            <div class="head" role="row">
                <span>{m.segments_samples_col_name()}</span>
                <span>{m.segments_samples_col_owner()}</span>
                <span>{m.segments_samples_col_created()}</span>
                <span></span>
            </div>
            {#each $samples as sample (sample.id)}
                <SampleRow {sample} {onOpen} onChanged={refresh} />
            {/each}
        </div>
    {/if}
</section>

<style>
    .samples { display: flex; flex-direction: column; gap: var(--s-3); padding: var(--s-2) 0; }
    .title { margin: 0; font-size: var(--fs-title, 1.05rem); color: var(--text-primary); }
    .intro { margin: 0; color: var(--text-muted); font-size: var(--fs-body); max-width: 70ch; }

    .upload {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--s-2);
        align-items: end; padding: var(--s-3); background: var(--panel); border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
    }
    .field { display: flex; flex-direction: column; gap: 4px; font-size: var(--fs-meta); color: var(--text-secondary); }
    .field input {
        font: inherit; color: var(--text-primary); background: var(--panel-2);
        border: 1px solid var(--border-quiet); border-radius: var(--r-1); padding: 6px 8px;
    }
    .submit {
        font: inherit; padding: 7px 14px; border-radius: var(--r-1); cursor: pointer;
        background: var(--accent); color: var(--accent-contrast, #0b1020); border: 1px solid var(--accent);
    }
    .submit:disabled { opacity: 0.5; cursor: not-allowed; }
    .error { grid-column: 1 / -1; margin: 0; color: var(--state-error-fg); font-size: var(--fs-meta); }
    .load-error { margin: 0; color: var(--state-error-fg); font-size: var(--fs-meta); }
    .empty { color: var(--text-faint); }

    .list { display: flex; flex-direction: column; border: 1px solid var(--border-quiet); border-radius: var(--r-2); overflow: hidden; }
    .head {
        display: grid; grid-template-columns: minmax(180px, 2fr) 1fr 1fr auto; gap: var(--s-2);
        padding: 6px var(--s-3); background: var(--panel-2); color: var(--text-faint);
        font-size: 10px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
    }
</style>
