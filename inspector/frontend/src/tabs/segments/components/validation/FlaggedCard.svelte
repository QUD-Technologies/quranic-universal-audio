<script lang="ts">
    /**
     * FlaggedCard — one entry in the "Flagged Issues" accordion.
     *
     * Renders the flagged segment via the live `SegmentRow` (full controls,
     * NOT readOnly) followed by the flag's comment thread: the root comment,
     * any append-only follow-up replies, and an "add reply" affordance open to
     * any editor. The flagger (root, `mine`) also gets an edit/clear affordance
     * mirroring the SegmentRow inline editor.
     *
     * Identity is server-redacted: every comment always shows the author's
     * role ("a contributor"); the login is appended only when `canSeeFlagger`
     * (the viewer holds `segments.see_flagger_identity`). `mine` — not the
     * identity — decides whose comment carries the edit affordance.
     */
    import { i18n } from '../../../../lib/i18n/locale.svelte';
    import * as m from '../../../../lib/paraglide/messages';
    import { currentUser } from '../../../../lib/stores/current-user';
    import type { FlagAuthor, FlagComment, SegmentFlagView } from '../../../../lib/types/generated/schemas';
import type { Segment } from '../../../../lib/types/view-models';
    import { relativeTime } from '../../../../lib/utils/relative-time';
    import { getAdjacentSegments, segAllData } from '../../stores/chapter';
    import { flagSegment } from '../../utils/edit/flag';
    import SegmentRow from '../list/SegmentRow.svelte';

    interface Props {
        seg: Segment;
        canSeeFlagger: boolean;
    }

    const { seg, canSeeFlagger }: Props = $props();

    // Re-resolve the flag from the live store (by uid) on every republish so an
    // optimistic flag/reply/edit op surfaces immediately — the `seg` prop object
    // is mutated in place by `flagSegment`, which alone wouldn't re-trigger a
    // `$derived` keyed on the unchanged prop reference.
    const flag = $derived<SegmentFlagView | null>(
        ($segAllData?.segments ?? []).find((s) => s.segment_uid === seg.segment_uid)?.flag
            ?? seg.flag
            ?? null,
    );
    const followUps = $derived<FlagComment[]>(flag?.follow_ups ?? []);

    // Locale-reactive labels (reuse the SegmentRow flag-editor keys so the two stay in lockstep).
    const L = $derived.by(() => {
        void i18n.locale;
        return {
            notePlaceholder: m.segments_row_flag_placeholder(),
            noteHint: m.segments_row_flag_hint_clear(),
            cancel: m.common_action_cancel(),
            remove: m.segments_row_flag_remove_button(),
            update: m.segments_row_flag_update_button(),
            editLink: m.segments_row_flag_edit_link(),
            replyPlaceholder: m.segments_row_flag_reply_placeholder(),
            reply: m.segments_row_flag_reply_button(),
            ctxPrev: m.segments_validation_context_label_previous(),
            ctxNext: m.segments_validation_context_label_next(),
            ctxToggle: showContext
                ? m.segments_validation_hide_context_button()
                : m.segments_validation_show_context_button(),
        };
    });

    // ---- Context (prev / next neighbours), same shape as GenericIssueCard ----
    let showContext = $state(false);
    const adjacent = $derived.by(() => {
        void $segAllData;
        if (!showContext || seg.chapter == null) return { prev: null, next: null };
        return getAdjacentSegments(seg.chapter, seg.index);
    });
    const siblings = $derived<Segment[]>(
        [adjacent.prev, seg, adjacent.next].filter((s): s is Segment => s != null),
    );

    const author = $derived<FlagAuthor>({
        role: $currentUser.role,
        login: $currentUser.login,
        id: $currentUser.hf_user_id,
    });

    /** Indefinite role phrase, e.g. "a contributor", "a maintainer", "the owner". */
    function roleLabel(role: string | null | undefined): string {
        if (role === 'owner') return 'the owner';
        if (role === 'maintainer') return 'a maintainer';
        if (role === 'contributor') return 'a contributor';
        return 'an editor';
    }

    /** Display name: role always; login appended only when permitted + present. */
    function authorName(a: FlagAuthor): { role: string; login: string | null } {
        const login = canSeeFlagger ? (a.login ?? null) : null;
        return { role: roleLabel(a.role), login };
    }

    function when(at: string | null | undefined): string {
        return at ? relativeTime(at) : 'just now';
    }

    // ---- Root edit / clear (flagger only) ----
    let editingRoot = $state(false);
    let rootDraft = $state('');

    function openRootEditor(): void {
        rootDraft = flag?.comment ?? '';
        editingRoot = true;
    }
    function cancelRootEditor(): void {
        editingRoot = false;
        rootDraft = '';
    }
    function applyRootEditor(): void {
        if (flagSegment(seg, rootDraft, 'edit', author)) cancelRootEditor();
    }

    // ---- Add follow-up (any editor) ----
    let replyDraft = $state('');
    const replyDisabled = $derived(replyDraft.trim().length === 0);

    function submitReply(): void {
        if (replyDisabled) return;
        if (flagSegment(seg, replyDraft, 'followup', author)) replyDraft = '';
    }
    function onReplyKeydown(e: KeyboardEvent): void {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submitReply();
        }
    }
</script>

<div class="flagged-card" data-flag-uid={seg.segment_uid}>
    {#if adjacent.prev}
        <SegmentRow
            seg={adjacent.prev}
            isContext={true}
            contextLabel={L.ctxPrev}
            showPlayBtn={true}
            showChapter={true}
            accordionSiblings={siblings}
        />
    {/if}
    <SegmentRow
        {seg}
        instanceRole="accordion"
        showGotoBtn={true}
        showPlayBtn={true}
        showChapter={true}
        accordionSiblings={siblings}
        onCardToggleContext={() => (showContext = !showContext)}
    />
    {#if adjacent.next}
        <SegmentRow
            seg={adjacent.next}
            isContext={true}
            contextLabel={L.ctxNext}
            showPlayBtn={true}
            showChapter={true}
            accordionSiblings={siblings}
        />
    {/if}
    <div class="val-card-actions">
        <button
            class="val-action-btn val-action-btn-muted val-ctx-toggle-btn"
            onclick={() => (showContext = !showContext)}
        >{L.ctxToggle}</button>
    </div>

    {#if flag}
        {@const rootName = authorName(flag.author)}
        <div class="flag-thread">
            <!-- Root comment -->
            <div class="flag-comment flag-root">
                <div class="flag-comment-head">
                    <span class="flag-author">{rootName.role}</span>
                    {#if rootName.login}
                        <span class="flag-author-login">@{rootName.login}</span>
                    {/if}
                    {#if flag.mine}
                        <span class="flag-mine-tag">you</span>
                    {/if}
                    <span class="flag-time">{when(flag.at)}</span>
                </div>

                {#if editingRoot}
                    <div class="flag-edit">
                        <textarea
                            class="flag-input"
                            bind:value={rootDraft}
                            rows="2"
                            placeholder={L.notePlaceholder}
                        ></textarea>
                        <div class="flag-edit-foot">
                            <span class="flag-hint">{L.noteHint}</span>
                            <span class="flag-edit-actions">
                                <button class="flag-btn flag-btn-ghost" onclick={cancelRootEditor}>{L.cancel}</button>
                                <button class="flag-btn flag-btn-primary" onclick={applyRootEditor}>
                                    {rootDraft.trim().length === 0 ? L.remove : L.update}
                                </button>
                            </span>
                        </div>
                    </div>
                {:else}
                    <blockquote class="flag-body">{flag.comment}</blockquote>
                    {#if flag.mine}
                        <div class="flag-root-actions">
                            <button class="flag-link" onclick={openRootEditor}>{L.editLink}</button>
                        </div>
                    {/if}
                {/if}
            </div>

            <!-- Follow-up replies (append-only) -->
            {#each followUps as fu, i (i)}
                {@const fuName = authorName(fu.author)}
                <div class="flag-comment flag-followup">
                    <div class="flag-comment-head">
                        <span class="flag-author">{fuName.role}</span>
                        {#if fuName.login}
                            <span class="flag-author-login">@{fuName.login}</span>
                        {/if}
                        {#if fu.mine}
                            <span class="flag-mine-tag">you</span>
                        {/if}
                        <span class="flag-time">{when(fu.at)}</span>
                    </div>
                    <blockquote class="flag-body">{fu.comment}</blockquote>
                </div>
            {/each}

            <!-- Add a reply -->
            <div class="flag-reply">
                <textarea
                    class="flag-input"
                    bind:value={replyDraft}
                    rows="1"
                    placeholder={L.replyPlaceholder}
                    onkeydown={onReplyKeydown}
                ></textarea>
                <button
                    class="flag-btn flag-btn-primary flag-reply-send"
                    disabled={replyDisabled}
                    onclick={submitReply}
                >{L.reply}</button>
            </div>
        </div>
    {/if}
</div>

<style>
    .flagged-card {
        display: flex;
        flex-direction: column;
        margin-bottom: var(--s-3);
    }

    .flag-thread {
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
        margin: var(--s-2) 0 0 var(--s-3);
        padding: var(--s-3);
        background: var(--canvas-inset);
        border: 1px solid var(--state-warn-border);
        border-radius: var(--r-2);
    }

    .flag-comment {
        display: flex;
        flex-direction: column;
        gap: 5px;
    }
    .flag-followup {
        padding-inline-start: var(--s-3);
        border-inline-start: 1px solid var(--border-quiet);
    }

    .flag-comment-head {
        display: flex;
        align-items: baseline;
        gap: var(--s-2);
        flex-wrap: wrap;
    }
    .flag-author {
        font-size: var(--fs-meta);
        font-weight: 600;
        color: var(--state-warn-fg);
    }
    .flag-followup .flag-author {
        color: var(--text-secondary);
    }
    .flag-author-login {
        font-family: var(--font-mono);
        font-size: 10.5px;
        color: var(--text-muted);
    }
    .flag-mine-tag {
        font-family: var(--font-mono);
        font-size: 9.5px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--accent);
        padding: 1px 5px;
        background: var(--accent-tint);
        border-radius: var(--r-pill);
    }
    .flag-time {
        margin-inline-start: auto;
        font-size: 10.5px;
        color: var(--text-faint);
        font-variant-numeric: tabular-nums;
    }

    .flag-body {
        margin: 0;
        padding: var(--s-2) var(--s-3);
        background: var(--panel);
        border-radius: var(--r-1);
        font-size: var(--fs-body);
        line-height: var(--lh-normal);
        color: var(--text-primary);
        white-space: pre-wrap;
        word-break: break-word;
    }
    .flag-root > .flag-body {
        border-inline-start: 2px solid var(--state-warn-border);
    }

    .flag-root-actions {
        display: flex;
    }
    .flag-link {
        font-size: var(--fs-meta);
        color: var(--text-muted);
        text-decoration: underline;
        text-underline-offset: 2px;
    }
    .flag-link:hover {
        color: var(--state-warn-fg);
    }

    /* ---- inline editors (root edit + reply) ---- */
    .flag-edit,
    .flag-reply {
        display: flex;
        gap: var(--s-2);
    }
    .flag-edit {
        flex-direction: column;
    }
    .flag-reply {
        align-items: flex-end;
    }
    .flag-input {
        width: 100%;
        flex: 1;
        resize: vertical;
        min-height: 38px;
        padding: 6px 8px;
        font: var(--fs-body)/var(--lh-normal) var(--font-sans);
        color: var(--text-primary);
        background: var(--panel);
        border: 1px solid var(--border-default);
        border-radius: var(--r-1);
    }
    .flag-input::placeholder {
        color: var(--text-muted);
    }
    .flag-input:focus-visible {
        outline: none;
        border-color: var(--state-warn-fg);
        box-shadow: 0 0 0 2px var(--state-warn-bg);
    }

    .flag-edit-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--s-3);
        flex-wrap: wrap;
    }
    .flag-hint {
        font-size: var(--fs-meta);
        color: var(--text-muted);
    }
    .flag-edit-actions {
        display: inline-flex;
        gap: var(--s-2);
        margin-inline-start: auto;
    }

    .flag-btn {
        padding: 4px 12px;
        font-size: 0.78rem;
        border-radius: var(--r-2);
        border: 1px solid transparent;
        white-space: nowrap;
    }
    .flag-btn-ghost {
        color: var(--text-secondary);
        background: var(--panel);
        border-color: var(--border-default);
    }
    .flag-btn-ghost:hover {
        color: var(--text-primary);
        background: var(--panel-2);
    }
    .flag-btn-primary {
        color: var(--accent-fg);
        background: var(--state-warn-fg);
        border-color: var(--state-warn-fg);
        font-weight: 600;
    }
    .flag-btn-primary:hover:not(:disabled) {
        background: oklch(from var(--state-warn-fg) calc(l + 0.04) c h);
    }
    .flag-btn-primary:disabled {
        color: var(--text-faint);
        background: var(--panel);
        border-color: var(--border-default);
        cursor: not-allowed;
    }
    .flag-reply-send {
        flex: 0 0 auto;
    }
</style>
