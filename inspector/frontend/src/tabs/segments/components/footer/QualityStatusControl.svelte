<script lang="ts">
    /**
     * Footer control for the delivery's recitation quality rating.
     *
     * - Holder of `reciter.set_quality_status` on a published / marked-ready
     *   row: a select (Clean / Almost clean / Needs work, plus Clear rating
     *   when one is set). Saves immediately, independent of segment Save and
     *   the edit lock.
     * - Rated row outside those states: the holder can still clear it; the
     *   other options are disabled.
     * - Everyone else: a read-only pill (nothing when unrated).
     */
    import { setQualityStatus } from '../../../../lib/api/claims-client';
    import type { ReciterTask } from '../../../../lib/api/reciter-task';
    import QualityPill from '../../../../lib/components/QualityPill.svelte';
    import { i18n } from '../../../../lib/i18n/locale.svelte';
    import * as m from '../../../../lib/paraglide/messages';
    import { pushToast } from '../../../../lib/stores/toast';
    import type { QualityStatus } from '../../../../lib/types/generated/schemas';
    import { QUALITY_ORDER, qualityHint, qualityLabel } from '../../../../lib/utils/quality-status';

    const CLEAR = '';
    const TOAST_TTL_MS = 2500;

    let { slug, task, onChanged }: {
        slug: string;
        task: ReciterTask | null;
        onChanged: () => void;
    } = $props();

    let busy = $state(false);

    const current = $derived((task?.row.quality_status ?? null) as QualityStatus | null);
    const canSet = $derived(task?.predicates.can_set_quality_status ?? false);
    const canClear = $derived(task?.predicates.can_clear_quality_status ?? false);
    const editable = $derived(canSet || canClear);
    const title = $derived(
        (i18n.locale, canSet ? m.common_quality_select_title() : m.common_quality_locked_title()),
    );

    async function onPick(e: Event): Promise<void> {
        const select = e.currentTarget as HTMLSelectElement;
        const next = select.value === CLEAR ? null : (select.value as QualityStatus);
        if (next === current) return;
        busy = true;
        try {
            await setQualityStatus(slug, next);
            pushToast({
                kind: 'success',
                text: next ? m.common_quality_saved_toast({ label: qualityLabel(next) }) : m.common_quality_cleared_toast(),
                ttl: TOAST_TTL_MS,
            });
            onChanged();
        } catch {
            // claims-client already surfaced the error toast; snap back.
            select.value = current ?? CLEAR;
        } finally {
            busy = false;
        }
    }
</script>

{#if editable}
    <label class="quality-control" {title}>
        <span class="label">{(i18n.locale, m.common_quality_label())}</span>
        <select class="quality-select {current ?? 'unrated'}" value={current ?? CLEAR} disabled={busy} onchange={onPick}>
            {#if current === null}
                <option value={CLEAR} disabled>{(i18n.locale, m.common_quality_unrated())}</option>
            {/if}
            {#each QUALITY_ORDER as status (status)}
                <option value={status} disabled={!canSet} title={qualityHint(status)}>
                    {(i18n.locale, qualityLabel(status))}
                </option>
            {/each}
            {#if current !== null}
                <option value={CLEAR}>{(i18n.locale, m.common_quality_clear())}</option>
            {/if}
        </select>
    </label>
{:else}
    <QualityPill status={current} />
{/if}

<style>
    .quality-control {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        flex-shrink: 0;
    }

    .label {
        font-size: 11px;
        color: var(--text-muted);
    }

    .quality-select {
        font: inherit;
        font-size: 12px;
        padding: 2px 6px;
        border-radius: var(--r-1);
        border: 1px solid var(--border-default);
        background: var(--panel-2);
        color: var(--text-primary);
    }

    .quality-select.clean {
        color: var(--ok-fg);
        border-color: currentColor;
    }

    .quality-select.almost_clean {
        color: var(--warn-fg);
        border-color: currentColor;
    }

    .quality-select.needs_work {
        color: var(--bad-fg);
        border-color: currentColor;
    }

    .quality-select option {
        color: var(--text-primary);
    }
</style>
