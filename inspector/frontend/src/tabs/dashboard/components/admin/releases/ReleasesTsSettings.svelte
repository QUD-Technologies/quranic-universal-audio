<script lang="ts">
    /**
     * Unified timestamps generate / regenerate launcher — the in-row expand.
     *
     * One launch trigger for BOTH first generation (Ready-to-generate rows,
     * ``row.ts === null``) and regeneration (any row with existing timestamps).
     * All TS tunables (beam/model/workers/…) come from the shared owner-wide
     * "Timestamps generation" accordion — this row carries no setting inputs.
     * The only per-launch choice is the Full-vs-Affected scope: when the row's
     * timestamps are behind specific chapters (``ts.affected_chapters``) the
     * chooser folds in inline. Affected re-aligns only the changed verses of
     * those chapters; Full re-aligns every verse of the reciter.
     *
     * Launches via the shared ``generateTimestamps`` route (same endpoint for
     * gen + regen) sending only the scope; the parent switches the row's
     * expansion to "Past jobs" on success so the live log is immediately visible.
     */
    import { generateTimestamps } from '../../../../../lib/api/admin-reviews';
    import type { ReleaseStatusRow } from '../../../../../lib/api/admin-releases';
    import { surahOptionText } from '../../../../../lib/utils/surah-info';

    interface Props {
        row: ReleaseStatusRow;
        /** Fired with the launched job id after a successful launch. */
        onlaunched: (_jobId: string) => void;
    }
    let { row, onlaunched }: Props = $props();

    const isRegen = $derived(row.ts !== null);
    const affected = $derived(row.ts?.affected_chapters ?? []);
    const hasAffected = $derived(affected.length > 0);

    let scope = $state<'affected' | 'full'>('affected');
    let launching = $state(false);
    let formError = $state<string | null>(null);

    const buttonLabel = $derived.by(() => {
        if (launching) return 'Launching…';
        if (!isRegen) return 'Generate timestamps';
        if (hasAffected && scope === 'affected') {
            return `Regenerate ${affected.length} chapter${affected.length === 1 ? '' : 's'}`;
        }
        return 'Regenerate full';
    });

    function affectedText(): string {
        return affected.map((c) => surahOptionText(c)).join(', ');
    }

    async function launch(): Promise<void> {
        if (launching) return;
        formError = null;
        const chapters =
            isRegen && hasAffected && scope === 'affected' ? affected : undefined;
        launching = true;
        try {
            const { job_id } = await generateTimestamps(row.slug, {
                chapters: chapters ?? null,
                full: isRegen && !chapters,
            });
            onlaunched(job_id);
        } catch (e) {
            formError = (e as Error).message ?? 'Launch failed';
        } finally {
            launching = false;
        }
    }
</script>

<div class="ts-settings">
    <div class="launch-row">
        <span class="defaults-note">Uses the shared Timestamps generation settings.</span>
        <span class="spacer"></span>
        <button class="launch" type="button" onclick={launch} disabled={launching}>
            {buttonLabel}
        </button>
    </div>

    {#if isRegen && hasAffected}
        <div class="scope-row">
            <label class="radio" class:active={scope === 'affected'}>
                <input type="radio" value="affected" bind:group={scope} />
                <span>Changed verses only</span>
            </label>
            <label class="radio" class:active={scope === 'full'}>
                <input type="radio" value="full" bind:group={scope} />
                <span>Full reciter</span>
            </label>
            <span class="affected-chips" class:dim={scope !== 'affected'}>{affectedText()}</span>
        </div>
    {/if}

    {#if formError}
        <div class="form-err" role="alert">{formError}</div>
    {/if}
</div>

<style>
    .ts-settings {
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
        padding: var(--s-2) var(--s-1);
    }
    .launch-row,
    .scope-row {
        display: flex;
        align-items: center;
        gap: var(--s-3);
        flex-wrap: wrap;
    }
    .defaults-note {
        font-size: var(--fs-meta);
        color: var(--text-muted);
    }
    .spacer { flex: 1; }

    .launch {
        background: var(--accent-tint-soft);
        border: 1px solid var(--accent);
        color: var(--accent-strong);
        font: inherit;
        font-size: var(--fs-meta);
        font-weight: 500;
        padding: 4px 14px;
        border-radius: var(--r-1);
        cursor: pointer;
        white-space: nowrap;
        transition: background-color var(--t-fast);
    }
    .launch:hover:not(:disabled) { background: var(--accent-tint); }
    .launch:disabled { opacity: 0.5; cursor: not-allowed; }

    .radio {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: var(--fs-meta);
        color: var(--text-secondary);
        cursor: pointer;
    }
    .radio.active { color: var(--text-primary); }
    .radio input { accent-color: var(--accent); margin: 0; }
    .affected-chips {
        font-family: var(--font-mono);
        font-size: 10.5px;
        color: var(--text-muted);
    }
    .affected-chips.dim { opacity: 0.4; }

    .form-err {
        font-size: var(--fs-meta);
        color: var(--state-error-fg);
    }
</style>
