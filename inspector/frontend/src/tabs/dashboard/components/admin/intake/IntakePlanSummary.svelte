<script lang="ts">
    /**
     * Headline of a ready intake plan: where the files come from, how many
     * are included and how long they run (live, from the unsaved edits), for
     * typed links the chapter coverage, and the server's blocking errors /
     * advisory warnings for the saved plan.
     */
    import type { IntakePlanView } from '../../../../../lib/types/generated/schemas';
    import { formatDuration, formatRanges, LAST_CHAPTER, type LocalCoverage } from './plan-utils';

    interface Props {
        plan: IntakePlanView;
        fileCount: number;
        includedCount: number;
        totalSec: number | null;
        /** Host `links` only — other hosts detect surahs when aligning. */
        coverage: LocalCoverage | null;
        rebuilding: boolean;
        disabled: boolean;
        onRebuild: () => void;
    }
    let { plan, fileCount, includedCount, totalSec, coverage, rebuilding, disabled, onRebuild }: Props =
        $props();

    const HOST_LABEL: Record<string, string> = {
        links: 'Direct links',
        youtube: 'YouTube',
        drive: 'Google Drive',
        soundcloud: 'SoundCloud',
        archive: 'archive.org',
        other: 'Web',
    };
    const hostLabel = $derived(HOST_LABEL[plan.host ?? 'other'] ?? 'Web');
</script>

<div class="summary">
    <div class="line">
        <span class="host">{hostLabel}</span>
        <span class="sep">·</span>
        <span>{fileCount} file{fileCount === 1 ? '' : 's'}</span>
        <span class="sep">·</span>
        <span>{includedCount} included</span>
        {#if totalSec !== null}
            <span class="sep">·</span>
            <span>{formatDuration(totalSec)}</span>
        {/if}
        {#if coverage}
            <span class="sep">·</span>
            <span class:full={coverage.covered.length === LAST_CHAPTER}>
                {coverage.covered.length}/{LAST_CHAPTER} chapters
            </span>
            {#if coverage.combined > 0}
                <span class="sep">·</span>
                <span>{coverage.combined} combined</span>
            {/if}
        {/if}
        {#if plan.uploader}
            <span class="sep">·</span>
            {#if plan.uploader_url}
                <a href={plan.uploader_url} target="_blank" rel="noopener noreferrer">{plan.uploader}</a>
            {:else}
                <span>{plan.uploader}</span>
            {/if}
        {/if}
        <span class="spacer"></span>
        <button class="btn tiny" {disabled} onclick={onRebuild}>
            {rebuilding ? 'Starting…' : 'Rebuild'}
        </button>
    </div>
    {#if plan.source_url}
        <a class="src" href={plan.source_url} target="_blank" rel="noopener noreferrer">{plan.source_url}</a>
    {/if}

    {#if coverage}
        {#if coverage.missing.length > 0}
            <p class="missing">Missing: <span class="mono">{formatRanges(coverage.missing)}</span></p>
        {/if}
        {#if coverage.duplicates.size > 0}
            <p class="err">Covered twice: <span class="mono">{formatRanges([...coverage.duplicates])}</span></p>
        {/if}
    {:else}
        <p class="missing">Surahs are detected from the audio when aligning — titles are not used.</p>
    {/if}

    {#if (plan.errors?.length ?? 0) > 0}
        <ul class="issues err">
            {#each plan.errors ?? [] as msg, i (i)}<li>{msg}</li>{/each}
        </ul>
    {/if}
    {#if (plan.warnings?.length ?? 0) > 0}
        <ul class="issues warn">
            {#each plan.warnings ?? [] as msg, i (i)}<li>{msg}</li>{/each}
        </ul>
    {/if}
</div>

<style>
    .summary { display: flex; flex-direction: column; gap: var(--s-2); font-size: var(--fs-meta); }
    .line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); color: var(--text-secondary); font-variant-numeric: tabular-nums; }
    .host { color: var(--text-primary); font-weight: 500; }
    .sep { color: var(--text-faint); }
    .full { color: var(--state-published-fg); }
    .spacer { flex: 1; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .src { font-family: var(--font-mono); font-size: 11px; word-break: break-all; }
    .mono { font-family: var(--font-mono); }
    p { margin: 0; line-height: var(--lh-normal); }
    .missing { color: var(--text-muted); }
    .err { color: var(--state-error-fg); }
    .issues { margin: 0; padding-left: var(--s-4); display: flex; flex-direction: column; gap: 2px; line-height: var(--lh-normal); }
    .issues.warn { color: var(--state-warn-fg); }
    .btn { padding: 3px 9px; border-radius: var(--r-2); font: 500 10.5px/1 var(--font-sans); border: 1px solid var(--border-default); background: transparent; color: var(--text-secondary); cursor: pointer; transition: color var(--t-fast), border-color var(--t-fast); }
    .btn:hover { color: var(--text-primary); border-color: var(--border-strong); }
    .btn:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
