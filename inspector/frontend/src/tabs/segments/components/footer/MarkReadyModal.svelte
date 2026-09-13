<script lang="ts">
    /**
     * MarkReadyModal — the reviewer's attestation form.
     *
     * Five checkboxes (all required) + two optional textareas. Submit is
     * blocked when any of five validation `category_counts` is non-zero;
     * the offending categories are listed inline with links into the
     * accordion. Once submitted the reciter is locked from further edits
     * by the reviewer; only an admin can send back or move forward.
     *
     * All long-form text lives in sibling .md files under
     * `../../../copy/mark-ready/` — edit those to change wording, never
     * inline strings here.
     */
    import { i18n } from '../../../../lib/i18n/locale.svelte';
    import { markReady } from '../../../../lib/api/claims-client';
    import * as m from '../../../../lib/paraglide/messages';
    import type { MarkReadyChecklist } from '../../../../lib/types/generated/schemas';
    import {
        BLOCKING_COUNT_KEYS,
        BLOCKING_LABELS,
        type BlockingCountKey,
        emptyChecklist,
        getMarkReadyCopy,
        isAllChecked,
    } from '../../copy/mark-ready';
    import { segAllData } from '../../stores/chapter';
    import { segConfig } from '../../stores/config';
    import { segValidation, valUiOpenCategory } from '../../stores/validation';
    import { blockingCountFor } from '../../utils/validation/blocking-count';

    interface Props {
        open: boolean;
        slug: string;
        onClose: () => void;
        onSubmitted: () => void;
    }
    let { open = $bindable(), slug, onClose, onSubmitted }: Props = $props();

    const markReadyCopy = $derived(getMarkReadyCopy(i18n.locale));

    let checklist = $state<MarkReadyChecklist>(emptyChecklist());
    let commentChecks = $state('');
    let commentIssues = $state('');
    let busy = $state(false);
    let error = $state<string | null>(null);

    // Reset state every time the modal opens — a previous failed attempt
    // shouldn't pre-fill the next session, and a re-open after submission
    // (e.g. unmark → re-mark) starts fresh.
    $effect(() => {
        if (open) {
            checklist = emptyChecklist();
            commentChecks = '';
            commentIssues = '';
            busy = false;
            error = null;
        }
    });

    const liveUids = $derived.by(() => {
        const segs = $segAllData?.segments ?? [];
        const set = new Set<string>();
        for (const s of segs) {
            if (s.segment_uid) set.add(s.segment_uid);
        }
        return set;
    });

    const blockingNonZero = $derived.by(() => {
        const v = $segValidation;
        const lcDefault = $segConfig.lcDefaultThreshold;
        const uids = liveUids;
        return BLOCKING_COUNT_KEYS
            .map((k) => ({ key: k, count: blockingCountFor(k, v, uids, lcDefault) }))
            .filter((row) => row.count > 0);
    });

    const allChecked = $derived(isAllChecked(checklist));
    const canSubmit = $derived(allChecked && blockingNonZero.length === 0 && !busy);

    function jumpToCategory(key: BlockingCountKey): void {
        valUiOpenCategory.set(key);
        open = false;
        onClose();
    }

    async function submit(): Promise<void> {
        if (!canSubmit) return;
        busy = true;
        error = null;
        try {
            await markReady(slug, {
                checklist,
                comment_checks: commentChecks,
                comment_issues: commentIssues,
            });
            onSubmitted();
            open = false;
            onClose();
        } catch (e) {
            // `markReady` already surfaced a toast; this duplicates the
            // message inline so the reviewer doesn't lose the form context.
            const msg = e instanceof Error ? e.message : m.segments_mark_ready_submit_error_fallback();
            error = msg;
        } finally {
            busy = false;
        }
    }

    function cancel(): void {
        if (busy) return;
        open = false;
        onClose();
    }

    function onKey(e: KeyboardEvent): void {
        if (!open) return;
        if (e.key === 'Escape') cancel();
    }

    const closeAriaLabel = $derived((i18n.locale, m.segments_mark_ready_close_aria_label()));
    const disabledChecklistTitle = $derived((i18n.locale, m.segments_mark_ready_submit_disabled_checklist_title()));
    const disabledBlockingTitle = $derived((i18n.locale, m.segments_mark_ready_submit_disabled_blocking_title()));
</script>

<svelte:window onkeydown={onKey} />

{#if open}
    <div
        class="backdrop"
        role="presentation"
        onclick={(e) => e.target === e.currentTarget && cancel()}
    >
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="mr-title">
            <header>
                <h2 id="mr-title">{markReadyCopy.form.title}</h2>
                <button class="close" aria-label={closeAriaLabel} onclick={cancel}>×</button>
            </header>

            {#if blockingNonZero.length > 0}
                <section class="blocking" role="alert">
                    <h3>{markReadyCopy.form.blockingLede}</h3>
                    <ul>
                        {#each blockingNonZero as row (row.key)}
                            <li>
                                <button
                                    type="button"
                                    class="jump"
                                    onclick={() => jumpToCategory(row.key)}
                                >
                                    {BLOCKING_LABELS[row.key]}
                                    <span class="count">{row.count}</span>
                                </button>
                            </li>
                        {/each}
                    </ul>
                </section>
            {/if}

            <section class="checklist">
                {#each markReadyCopy.checklist as item (item.key)}
                    <label class="check-row" class:checked={checklist[item.key]}>
                        <input
                            type="checkbox"
                            bind:checked={checklist[item.key]}
                            disabled={busy}
                        />
                        <span class="check-label">{item.label}</span>
                    </label>
                {/each}
            </section>

            <section class="comments">
                <label>
                    <span class="label">{markReadyCopy.comments.checks.label}</span>
                    <textarea
                        rows="3"
                        bind:value={commentChecks}
                        placeholder={markReadyCopy.comments.checks.placeholder}
                        disabled={busy}
                        maxlength="4000"
                    ></textarea>
                </label>
                <label>
                    <span class="label">{markReadyCopy.comments.issues.label}</span>
                    <textarea
                        rows="3"
                        bind:value={commentIssues}
                        placeholder={markReadyCopy.comments.issues.placeholder}
                        disabled={busy}
                        maxlength="4000"
                    ></textarea>
                </label>
            </section>

            {#if error}
                <p class="err" role="alert">{error}</p>
            {/if}

            <footer>
                <span class="warn-inline" role="note">
                    <span class="warn-icon" aria-hidden="true">!</span>
                    <span>{markReadyCopy.form.warning}</span>
                </span>
                <span class="footer-actions">
                    <button class="ghost" onclick={cancel} disabled={busy}>
                        {markReadyCopy.form.cancel}
                    </button>
                    <button
                        class="primary"
                        onclick={submit}
                        disabled={!canSubmit}
                        title={!allChecked
                            ? disabledChecklistTitle
                            : blockingNonZero.length > 0
                              ? disabledBlockingTitle
                              : ''}
                    >
                        {busy ? markReadyCopy.form.submitting : markReadyCopy.form.submit}
                    </button>
                </span>
            </footer>
        </div>
    </div>
{/if}

<style>
    .backdrop {
        position: fixed;
        inset: 0;
        z-index: 140;
        background: oklch(0.06 0.005 268 / 0.72);
        backdrop-filter: blur(3px);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: var(--s-6);
        animation: backdrop-in var(--t-slow) var(--ease-out-quart);
    }
    @keyframes backdrop-in { from { opacity: 0; } to { opacity: 1; } }

    .dialog {
        width: min(620px, 96vw);
        max-height: 88vh;
        overflow: auto;
        background: var(--canvas);
        border: 1px solid var(--border-default);
        border-radius: var(--r-3);
        padding: var(--s-5);
        display: flex;
        flex-direction: column;
        gap: var(--s-4);
        box-shadow: 0 32px 80px oklch(0 0 0 / 0.45);
        animation: modal-in var(--t-slow) var(--ease-out-expo);
    }
    @keyframes modal-in {
        from { opacity: 0; transform: translateY(12px) scale(0.985); }
        to   { opacity: 1; transform: none; }
    }

    header { display: flex; align-items: center; justify-content: space-between; }
    h2 { margin: 0; font-size: var(--fs-h3); font-weight: 500; color: var(--text-primary); }
    h3 {
        margin: 0 0 var(--s-2);
        font-size: var(--fs-meta);
        font-weight: 500;
        color: var(--text-secondary);
    }
    .close {
        width: 28px; height: 28px;
        display: inline-flex; align-items: center; justify-content: center;
        color: var(--text-muted); border-radius: var(--r-2);
        font-size: 18px; line-height: 1;
        background: transparent; border: 0; cursor: pointer;
    }
    .close:hover { color: var(--text-primary); background: var(--panel); }

    /* Inline warning that lives inside the footer to the left of the
       action buttons — saves the vertical real estate a standalone
       warning banner used to occupy. */
    .warn-inline {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        min-width: 0;
        flex: 1 1 auto;
        font-size: var(--fs-meta);
        color: var(--text-muted);
        line-height: var(--lh-normal);
    }
    .warn-icon {
        flex: 0 0 16px;
        width: 16px; height: 16px;
        display: inline-flex; align-items: center; justify-content: center;
        background: var(--state-warn-fg);
        color: var(--canvas);
        border-radius: 50%;
        font-weight: 600;
        font-size: 10px;
        line-height: 1;
    }

    .blocking {
        padding: var(--s-3);
        background: oklch(from var(--state-error-fg) l c h / 0.06);
        border: 1px solid oklch(from var(--state-error-fg) l c h / 0.35);
        border-radius: var(--r-2);
    }
    .blocking ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; }
    .blocking .jump {
        width: 100%;
        display: flex; align-items: center; justify-content: space-between;
        padding: 6px 8px;
        background: transparent;
        border: 1px solid transparent;
        border-radius: var(--r-2);
        font: inherit;
        font-size: var(--fs-meta);
        color: var(--text-primary);
        cursor: pointer;
        text-align: start;
    }
    .blocking .jump:hover { background: var(--panel); border-color: var(--border-quiet); }
    .blocking .count {
        font-family: var(--font-mono);
        font-size: 11px;
        color: var(--state-error-fg);
        padding: 2px 6px;
        border-radius: var(--r-pill);
        background: oklch(from var(--state-error-fg) l c h / 0.12);
    }

    .checklist {
        display: flex; flex-direction: column; gap: var(--s-2);
    }
    .check-row {
        display: flex; align-items: flex-start; gap: var(--s-2);
        padding: var(--s-3);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        cursor: pointer;
        transition: border-color var(--t-fast), background var(--t-fast);
    }
    .check-row:hover { border-color: var(--border-default); }
    .check-row.checked {
        border-color: var(--accent);
        background: oklch(from var(--accent) l c h / 0.06);
    }
    .check-row input[type='checkbox'] {
        margin-top: 2px;
        flex: 0 0 auto;
        accent-color: var(--accent);
        cursor: pointer;
    }
    .check-label {
        font-size: var(--fs-meta);
        line-height: var(--lh-normal);
        color: var(--text-primary);
    }

    .comments { display: flex; flex-direction: column; gap: var(--s-3); }
    .comments label { display: flex; flex-direction: column; gap: 4px; }
    .comments .label {
        font-size: var(--fs-meta);
        color: var(--text-muted);
    }
    .comments textarea {
        font: inherit;
        font-size: var(--fs-meta);
        background: var(--panel);
        color: var(--text-primary);
        border: 1px solid var(--border-default);
        border-radius: var(--r-2);
        padding: 8px 10px;
        resize: vertical;
        min-height: 64px;
        line-height: var(--lh-normal);
    }
    .comments textarea:focus { outline: none; border-color: var(--accent); }

    .err { margin: 0; font-size: var(--fs-meta); color: var(--state-error-fg); }

    footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--s-3);
        padding-top: var(--s-2);
        border-top: 1px solid var(--border-quiet);
    }
    .footer-actions {
        display: inline-flex;
        align-items: center;
        gap: var(--s-2);
        flex: 0 0 auto;
    }
    .ghost, .primary {
        padding: 8px 16px;
        border-radius: var(--r-2);
        font: 500 var(--fs-meta)/1 var(--font-sans);
        border: 1px solid var(--border-default);
        cursor: pointer;
        transition: background var(--t-fast), color var(--t-fast), border-color var(--t-fast);
    }
    .ghost { background: transparent; color: var(--text-muted); }
    .ghost:hover:not(:disabled) { color: var(--text-primary); border-color: var(--border-strong); }
    .primary { background: var(--accent); color: var(--accent-fg); border-color: var(--accent); }
    .primary:hover:not(:disabled) { background: var(--accent-strong); border-color: var(--accent-strong); }
    .primary:disabled, .ghost:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
