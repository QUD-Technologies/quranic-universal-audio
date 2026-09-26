<script lang="ts">
    /**
     * WaslBoundary — always-shown WASL/WAQF picker rendered between adjacent
     * pieces inside a cross-verse accordion card's split-group.
     *
     * Both labels are always visible. Three states:
     *
     *   pending  (leftUid ∈ pendingWaslConfirm, no prior pick; or leftUid ∈
     *       waslRecheck, a settled answer that is re-asked):
     *       both labels muted; picker auto-scrolls + focuses when
     *       focusWaslBoundary names this UID. Chain is paused here.
     *
     *   wasl    (leftSeg.is_wasl === true):
     *       WASL in accent, WAQF muted-clickable. User can switch any time.
     *
     *   waqf    (leftSeg.is_wasl === false and NOT pending):
     *       WAQF in accent, WASL muted-clickable.
     *
     * Commit branch:
     *   • If a pending split op for this seg exists in the dirty buffer,
     *     amend the split op's snapshots in place via amendSegInOp — no
     *     new ``set_is_wasl`` op emitted. Keeps the history-row count = 1
     *     per logical split regardless of how many wasl decisions or
     *     mind-changes happen during the chain.
     *   • Otherwise (post-save ad-hoc edit), dispatch a ``set_is_wasl``
     *     op via the standard setIsWaslOnSegment dispatcher. A re-asked
     *     boundary always records the op, even when the answer matches the
     *     stored flag, so the answer persists and closes the re-check.
     *
     * After commit, the picker clears its UID from pendingWaslConfirm and
     * waslRecheck, unhooks focus, and calls resumePendingChain() so the
     * post-split chain advances to the next piece's ref-edit.
     *
     * Keyboard (while this boundary is the paused chain step): ← highlights
     * WASL, → highlights WAQF (positional), Tab toggles between them, and
     * Enter commits the highlighted choice — advancing the chain to the next
     * child's ref-edit. Handled locally (the picker auto-focuses a button)
     * and stopPropagation'd so the global Segments dispatcher doesn't also
     * seek / cycle focus away on those keys. Space + other keys fall through.
     *
     * Separately, the picker exports its commit via `onCommitReady` so the
     * card can bind it to the tab-wide 1 (waṣl) / 2 (waqf) shortcut, which
     * labels a boundary from the focused piece without touching the picker.
     */

    import { onMount, tick } from 'svelte';

    import { editGate } from '../../../../lib/actions/editGate';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import type { EditOp, Segment } from '../../../../lib/types/view-models';
    import { refreshSegInStore } from '../../stores/chapter';
    import {
        amendSegInOp,
        getChapterOps,
        markDirty,
    } from '../../stores/dirty';
    import {
        clearWaslPending,
        focusWaslBoundary,
        pendingWaslConfirm,
    } from '../../stores/edit';
    import { resolveWaslRecheck, waslRecheck } from '../../stores/validation';
    import { resumePendingChain } from '../../utils/edit/reference';
    import { setIsWaslOnSegment } from '../../utils/edit/setIsWasl';

    export let leftSeg: Segment;
    export let rightSeg: Segment;
    /**
     * Staged mode — the boundary belongs to a split that has NOT been
     * dispatched yet (pre-applied cross-verse auto-split). The picker then
     * owns no store/op work: it renders `stagedValue` (undefined = pending)
     * and hands every click to `onPick`; the card commits the split once all
     * of its boundaries are answered. A split missed-waqf card uses the same
     * mode on its cuts (`stagedValue` false): its `onPick` merges on WASL.
     */
    export let onPick: ((_value: boolean) => void) | null = null;
    export let stagedValue: boolean | undefined = undefined;
    /**
     * Hand this picker's commit callback to the owning card — `(uid, commit)`
     * on mount, `(uid, null)` on unmount, keyed by the LEFT piece's uid so the
     * card can map a focused row to the boundary under it. The card republishes
     * it as the keyboard's `setWasl` action (1 = waṣl, 2 = waqf).
     */
    export let onCommitReady:
        | ((_uid: string, _commit: ((_value: boolean) => void) | null) => void)
        | null = null;

    let waslBtnEl: HTMLButtonElement | undefined;
    let waqfBtnEl: HTMLButtonElement | undefined;

    /** Transient keyboard highlight while this boundary is the paused chain
     *  step. ←/→ pick positionally (WASL · WAQF), Tab toggles, Enter commits.
     *  Null whenever this picker isn't the active step. */
    let highlighted: 'wasl' | 'waqf' | null = null;

    $: leftUid = leftSeg.segment_uid ?? '';
    $: sameChapter =
        leftSeg.chapter != null
        && rightSeg.chapter != null
        && leftSeg.chapter === rightSeg.chapter;

    $: isStaged = onPick !== null;
    $: isWasl = isStaged ? stagedValue === true : leftSeg.is_wasl === true;
    $: isRecheck = !isStaged && leftUid !== '' && $waslRecheck.has(leftUid);
    $: isPending = isStaged
        ? stagedValue === undefined
        : isRecheck || (leftUid !== '' && $pendingWaslConfirm.has(leftUid));

    // Reactive auto-focus when the chain pauses on this boundary.
    // Defer to next tick so the smooth-scroll lands before the focus ring
    // appears; otherwise the user sees the ring mid-scroll and may miss it.
    $: if (leftUid && $focusWaslBoundary === leftUid && waslBtnEl) {
        const el = waslBtnEl;
        highlighted = 'wasl';
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        void tick().then(() => el.focus());
    }

    // Publish the commit to the owning card for as long as this picker is
    // mounted, so the card's rows can offer it as a keyboard action.
    onMount(() => {
        const uid = leftSeg.segment_uid ?? '';
        if (!uid) return;
        // Hand up the KEYBOARD flavour: labelling with 1 / 2 is a pure data
        // edit — it never resumes the post-split chain (which opens a
        // ref-edit and moves focus) and never disturbs playback or the nav
        // cursor. Clicking the picker keeps the chain behaviour below.
        onCommitReady?.(uid, (value: boolean) => commit(value, { silent: true }));
        return () => onCommitReady?.(uid, null);
    });

    /** Move the keyboard highlight to `side` and pull DOM focus with it so the
     *  focus ring tracks the staged choice. */
    function setHighlight(side: 'wasl' | 'waqf', e: KeyboardEvent): void {
        e.preventDefault();
        e.stopPropagation();
        highlighted = side;
        (side === 'wasl' ? waslBtnEl : waqfBtnEl)?.focus();
    }

    /** Keyboard nav for the paused chain step: ← / → highlight WASL / WAQF
     *  positionally, Tab toggles, Enter commits the highlighted choice and
     *  advances the chain. Only intercepts while this boundary is pending —
     *  other keys (Space preview, …) bubble to the global Segments dispatcher. */
    function onPickerKeydown(e: KeyboardEvent): void {
        if (!isPending) return;
        switch (e.key) {
            case 'ArrowLeft':
                setHighlight('wasl', e);
                break;
            case 'ArrowRight':
                setHighlight('waqf', e);
                break;
            case 'Tab':
                setHighlight(highlighted === 'wasl' ? 'waqf' : 'wasl', e);
                break;
            case 'Enter':
                e.preventDefault();
                e.stopPropagation();
                commit(highlighted !== 'waqf');
                break;
        }
    }

    /**
     * Apply the label. `silent` is the keyboard flavour: same data write, but
     * no chain hand-off — no ref-edit, no focus move. Neither flavour touches
     * playback: the pieces keep the windows their staged slices had, so the
     * live range stays valid and the audio is left exactly as it was.
     */
    function commit(value: boolean, opts?: { silent?: boolean }): void {
        const silent = opts?.silent === true;
        if (onPick) {
            highlighted = null;
            onPick(value);
            return;
        }
        if (!leftUid || leftSeg.chapter == null) return;
        const chapter = leftSeg.chapter;

        try {
            const parentSplit = _findPendingSplitFor(chapter, leftUid);
            if (parentSplit) {
                // While the parent split is still in the dirty buffer,
                // amend its targets_after snapshots in place. No new op,
                // no history-row noise from mind-changes during the chain.
                amendSegInOp(parentSplit, leftUid, { is_wasl: value });
                leftSeg.is_wasl = value;
                delete (leftSeg as Segment & { _derived?: unknown })._derived;
                refreshSegInStore(leftSeg);
                markDirty(chapter, leftSeg.index);
            } else if (isRecheck) {
                setIsWaslOnSegment(leftSeg, value, { force: true });
            } else if (value !== isWasl) {
                // Post-save ad-hoc edit: emit a real set_is_wasl op.
                setIsWaslOnSegment(leftSeg, value);
            }
            resolveWaslRecheck(leftUid);
        } catch (err) {
            console.warn('WaslBoundary: commit failed:', err);
        } finally {
            highlighted = null;
            clearWaslPending(leftUid);
            if ($focusWaslBoundary === leftUid) focusWaslBoundary.set(null);
            // Re-enter the chain handoff. If a pending wasl gate was the
            // only thing blocking this entry, it now clears and the
            // chain pops + advances to the next ref-edit.
            //
            // Keyboard labels skip it: the chain hand-off opens a ref-edit on
            // the next piece, which steals focus from wherever the user is.
            if (!silent) resumePendingChain();
        }
    }

    $: boundaryAriaLabel = tr($localeStore, m.segments_validation_wasl_boundary_aria_label());
    $: waslButtonTitle = tr($localeStore, m.segments_validation_wasl_button_title());
    $: waqfButtonTitle = tr($localeStore, m.segments_validation_waqf_button_title());

    function _findPendingSplitFor(chapter: number, uid: string): EditOp | null {
        for (const op of getChapterOps(chapter)) {
            if (op.op_type !== 'split_segment') continue;
            const after = op.targets_after as
                | Array<{ segment_uid?: string }>
                | undefined;
            if (!Array.isArray(after)) continue;
            if (after.some((t) => t.segment_uid === uid)) return op;
        }
        return null;
    }
</script>

{#if sameChapter && leftUid}
    <div
        class="wasl-boundary"
        class:is-pending={isPending}
        class:is-wasl={!isPending && isWasl}
        class:is-waqf={!isPending && !isWasl}
        role="group"
        aria-label={boundaryAriaLabel}
    >
        <span class="rule" aria-hidden="true"></span>
        <span class="picker">
            <button
                bind:this={waslBtnEl}
                type="button"
                class="choice"
                class:active={!isPending && isWasl}
                class:highlighted={isPending && highlighted === 'wasl'}
                aria-pressed={!isPending && isWasl}
                title={waslButtonTitle}
                use:editGate
                on:keydown={onPickerKeydown}
                on:click={() => commit(true)}
            >WASL</button>
            <span class="sep" aria-hidden="true">·</span>
            <button
                bind:this={waqfBtnEl}
                type="button"
                class="choice"
                class:active={!isPending && !isWasl}
                class:highlighted={isPending && highlighted === 'waqf'}
                aria-pressed={!isPending && !isWasl}
                title={waqfButtonTitle}
                use:editGate
                on:keydown={onPickerKeydown}
                on:click={() => commit(false)}
            >WAQF</button>
        </span>
        <span class="rule" aria-hidden="true"></span>
    </div>
{/if}

<style>
    .wasl-boundary {
        display: flex;
        align-items: center;
        gap: var(--s-3);
        padding: var(--s-2) var(--s-3);
    }

    .rule {
        flex: 1 1 auto;
        height: 0;
        border-top: 1px solid var(--border-quiet);
        transition: border-color var(--t-fast) var(--ease-out-quart);
    }
    .is-pending .rule {
        border-top-color: var(--accent-tint);
    }
    .is-wasl .rule,
    .is-waqf .rule {
        border-top-color: var(--border-default);
    }

    .picker {
        display: inline-flex;
        align-items: baseline;
        gap: var(--s-2);
        padding: 2px 8px;
        border-radius: var(--r-1);
        font-family: var(--font-sans);
        font-size: var(--fs-meta);
        line-height: var(--lh-tight);
        letter-spacing: 0.12em;
        font-weight: 500;
        transition:
            background var(--t-fast) var(--ease-out-quart),
            box-shadow var(--t-fast) var(--ease-out-quart);
    }
    .is-pending .picker {
        background: var(--accent-tint-soft);
        box-shadow: 0 0 0 1px var(--accent-tint);
    }

    .choice {
        background: transparent;
        border: 0;
        padding: 0;
        cursor: pointer;
        color: var(--text-muted);
        font: inherit;
        letter-spacing: inherit;
        transition: color var(--t-fast) var(--ease-out-quart);
    }
    .choice:hover {
        color: var(--text-primary);
    }
    .choice.active {
        color: var(--accent);
    }
    .choice.active:hover {
        color: var(--accent-strong);
    }
    /* Staged keyboard choice during the paused chain step — accent it so the
       pending selection reads clearly before Enter commits it. */
    .choice.highlighted {
        color: var(--accent);
        font-weight: 600;
    }
    .choice:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 3px;
        border-radius: var(--r-1);
    }

    .sep {
        color: var(--text-faint);
    }
</style>
