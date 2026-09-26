<script lang="ts">
    import { createEventDispatcher } from 'svelte';
    import { get } from 'svelte/store';

    import { editGate } from '../../../../lib/actions/editGate';
    import { localeStore, tr } from '../../../../lib/i18n/locale-store';
    import * as m from '../../../../lib/paraglide/messages';
    import type { SegValAnyItem } from '../../../../lib/types/generated/schemas';
    import type { Segment } from '../../../../lib/types/view-models';
    import { IssueRegistry } from '../../domain/registry';
    import { autoSplitMap, ensureAutoSplitMap } from '../../stores/auto-split';
    import {
        getAdjacentSegments,
        getChapterSegments,
        segAllData,
        selectedChapter,
        selectedReciter,
    } from '../../stores/chapter';
    import { segConfig } from '../../stores/config';
    import {
        dirtyTick,
        getChapterOpsSnapshot,
        isSegmentDirty,
    } from '../../stores/dirty';
    import { markWaslPending } from '../../stores/edit';
    import {
        allPicked,
        clearStagedPicks,
        setStagedPick,
        stagedChildUidsFor,
        stagedWaslPicks,
    } from '../../stores/staged-split';
    import { splitGroupIndex } from '../../stores/validation';
    import { ignoreIssueOnSegment } from '../../utils/edit/ignore';
    import { mergeAdjacent } from '../../utils/edit/merge';
    import { waslCommitForPiece, type WaslCommits } from '../../utils/validation/wasl-binding';
    import { commitSplit, finalizeSplit } from '../../utils/edit/split-commit';
    import { isVerseBoundary } from '../../utils/validation/boundary-state';
    import { isIgnoredFor } from '../../utils/validation/classified-issues';
    import { resolveIssueSeg } from '../../utils/validation/resolve-issue';
    import { getSplitGroupMembers } from '../../utils/validation/split-group';
    import {
        buildStagedChildren,
        isStagedSegment,
        type StagedKind,
        stagedCommit,
        stagedPickKey,
        stagedSplitFor,
    } from '../../utils/validation/staged-split';
    import SegmentRow from '../list/SegmentRow.svelte';
    import BoundaryEvidence from './BoundaryEvidence.svelte';
    import WaslBoundary from './WaslBoundary.svelte';

    const dispatch = createEventDispatcher<{ contextchange: boolean }>();

    // ---- Props ----
    export let category: string;
    export let item: SegValAnyItem;

    // ---- State ----
    let showContext = false;
    let isAlreadyIgnored = false;
    // Bind to the first-resolved segment's UID so subsequent resolutions stay
    // pinned to the same logical segment across split/merge reindexing. Once
    // bound, `_resolveIssue` looks up by UID first and only falls back to the
    // (chapter, seg_index) + ref heuristic when the UID is missing.
    let _boundUid: string | null = null;

    // ---- Derived ----
    $: issueMsg = (item as { msg?: string }).msg;
    $: isBoundaryReview = category === 'hidden_pause' || category === 'missed_waqf'
        || category === 'false_split' || category === 'unmarked_wasl';
    // Unmarked Wasl resolves with the WASL/WAQF picker on the join to the next
    // segment, so the card renders it between the row and its next context.
    $: showWaslPicker = category === 'unmarked_wasl';

    // Subscribe to segAllData so resolvedSeg re-derives after split/merge
    // mutates item.seg_index in place. _resolveIssue reads segAllData via
    // getSegByChapterIndex / getChapterSegments; the extra reference here
    // forces the reactive statement to register the dependency.
    $: segStoreTick = $segAllData;
    // Wrap in a local helper — passing `_boundUid` directly as an argument
    // would make Svelte see a cyclical dep (`resolvedSeg` → `_boundUid` →
    // `resolvedSeg`). Reading it inside a called function hides it from the
    // reactive-dep walker; the `segStoreTick` re-fire handles re-derivation.
    function _resolveLocal(it: SegValAnyItem, cat: string): Segment | null {
        return resolveIssueSeg(it, cat, _boundUid);
    }
    $: resolvedSeg = (void segStoreTick, _resolveLocal(item, category));
    // Pin to the first resolution's UID. After this, resolvedSeg only tracks
    // that specific segment — even if the seg is split (firstHalf keeps the
    // UID) or merged into (first.uid is kept). If the seg is deleted,
    // resolvedSeg collapses to null and the card body hides via `{#if resolvedSeg}`.
    //
    // Merge redirect: when the bound UID was consumed by a merge,
    // resolveIssueSeg follows the redirect and returns the surviving segment.
    // Update _boundUid to the survivor's UID so future lookups are direct hits.
    $: if (resolvedSeg) {
        const resolvedUid = resolvedSeg.segment_uid ?? null;
        if (!_boundUid || (_boundUid !== resolvedUid && resolvedUid)) {
            _boundUid = resolvedUid;
        }
    }

    // Base gate from the registry; ``low_confidence`` adds a runtime guard so
    // a segment whose confidence has been promoted to 1.0 (e.g. after a save
    // edit) doesn't keep offering the Ignore button. A missed-waqf item that
    // is already split is labelled by its cuts, not ignored.
    $: canIgnore =
        resolvedSeg != null &&
        (IssueRegistry[category]?.canIgnore ?? false) &&
        (category !== 'low_confidence' || (resolvedSeg.confidence ?? 1) < 1.0) &&
        !isSplitMissedWaqf;

    $: segChapterForBtn =
        resolvedSeg != null ? (resolvedSeg.chapter ?? parseInt(get(selectedChapter))) : 0;

    $: isDirtySegment = (
        void $dirtyTick,
        resolvedSeg != null
            ? realMembers.some((mem) => isSegmentDirty(mem.chapter ?? segChapterForBtn, mem.index))
            : false
    );

    $: ctxMode = $segConfig.accordionContext?.[category] ?? 'hidden';
    $: ctxDefaultOpen = ctxMode !== 'hidden';
    $: ctxNextOnly = ctxMode === 'next_only';

    // Split-group expansion: once a resolvedSeg has been split, render every
    // descendant in the main slot so the accordion card grows with the split
    // rather than hopping between halves. Prev/Next anchor to segments outside
    // the group. `getSplitGroupMembers` returns [] when no split has touched
    // the seg — we fall back to the single resolvedSeg render.
    //
    // Dependencies: $segAllData (segStoreTick) covers chapter-seg re-derivation;
    // the committed-history closure comes pre-attached on the validation item
    // as `split_group_uids`; $dirtyTick ensures the op log snapshot refreshes
    // after each in-progress split mutation.
    $: _groupChapter = ((): number => {
        if (resolvedSeg?.chapter != null) return resolvedSeg.chapter;
        const parsed = parseInt(get(selectedChapter));
        return Number.isFinite(parsed) ? parsed : 0;
    })();
    $: _committedSplitGroupUids = _boundUid != null
        ? $splitGroupIndex[_boundUid]
        : undefined;
    // Memoize the split-group computation by an op-log fingerprint. Backend
    // committed-history closure is invalidated by the post-save validate
    // refresh (which ships a new `split_group_uids`), so we don't track it
    // separately here — the new item identity drives re-render.
    let _splitGroupMemoKey = '';
    let _splitGroupMemoResult: Segment[] = [];
    $: {
        void segStoreTick; void $dirtyTick;
        if (_boundUid != null && _groupChapter > 0) {
            const chapterSegs = getChapterSegments(_groupChapter);
            const ops = getChapterOpsSnapshot(_groupChapter);
            const MUTATING_OPS = new Set([
                'split_segment', 'merge_segments', 'edit_reference',
                'auto_fix_missing_word', 'boundary_adjustment', 'trim_segment',
            ]);
            let mutatingOpsCount = 0;
            for (const op of ops) {
                if (MUTATING_OPS.has(op.op_type)) mutatingOpsCount++;
            }
            const committedLen = _committedSplitGroupUids?.length ?? 0;
            const key = `${_groupChapter}|${_boundUid}|${chapterSegs.length}|${committedLen}|${mutatingOpsCount}`;
            if (key !== _splitGroupMemoKey) {
                _splitGroupMemoKey = key;
                _splitGroupMemoResult = getSplitGroupMembers(
                    _boundUid, chapterSegs, _committedSplitGroupUids, ops,
                );
            }
        } else if (_splitGroupMemoKey !== '') {
            _splitGroupMemoKey = '';
            _splitGroupMemoResult = [];
        }
    }
    $: groupMembers = _splitGroupMemoResult;

    // ---- Staged pre-split (an offline pass names the cut) ----
    //
    // No split has touched the seg yet, but an offline pass already knows the
    // cut: render the pieces up front with a WASL/WAQF picker on every
    // boundary. Nothing is dispatched until all boundaries are labelled. On a
    // cross-verse card (cuts from the Auto Split map) the last pick commits ONE
    // `split` op carrying `wasls[]`, after which the real pieces take over via
    // `groupMembers` and the pickers switch to the default (pending-split
    // amend) path on their own. On a missed-waqf card (cuts from the item's
    // `boundary`) WASL means "no stop here": only the boundaries picked WAQF
    // are cut, and a card with no cut is ignored instead.
    $: isMissedWaqfCard = category === 'missed_waqf';
    $: stagedCategory = (isMissedWaqfCard ? 'missed_waqf' : 'cross_verse') as StagedKind;
    $: if (resolvedSeg && !isMissedWaqfCard && isCrossVerseSeg(resolvedSeg) && $selectedReciter) {
        void ensureAutoSplitMap($selectedReciter);
    }
    // `getSplitGroupMembers` always returns at least the root itself, so
    // "no split has touched the seg" is a group of ≤1.
    $: isSplitMissedWaqf = isMissedWaqfCard && groupMembers.length > 1;
    $: staged = groupMembers.length <= 1
        ? stagedSplitFor(stagedCategory, resolvedSeg, item, $autoSplitMap)
        : null;
    $: stagedUid = staged && resolvedSeg?.segment_uid ? resolvedSeg.segment_uid : null;
    $: stagedKey = stagedUid ? stagedPickKey(stagedCategory, stagedUid) : null;
    // An ignored missed-waqf item had every cut answered WASL: show it so
    // until a pick relabels it.
    $: ignoredAsWasl = isMissedWaqfCard && resolvedSeg != null
        && (void segStoreTick, isIgnoredFor(resolvedSeg, category));
    $: stagedPicks = stagedKey
        ? ($stagedWaslPicks[stagedKey] ?? (ignoredAsWasl && staged ? staged.cursors.map(() => true) : []))
        : [];
    $: stagedChildren = staged && resolvedSeg && stagedKey
        ? buildStagedChildren(
            resolvedSeg,
            staged,
            stagedChildUidsFor(stagedKey, staged.cursors.length),
            stagedPicks,
        )
        : [];

    $: mainMembers = stagedChildren.length > 0
        ? stagedChildren
        : groupMembers.length > 0
            ? groupMembers
            : (resolvedSeg ? [resolvedSeg] : []);
    // Real (store-backed) members — what Ignore and dirty checks act on.
    $: realMembers = mainMembers.filter((mem) => !isStagedSegment(mem));

    function isCrossVerseSeg(seg: Segment): boolean {
        const parts = seg.matched_ref.split('-');
        if (parts.length !== 2) return false;
        return parts[0]!.split(':')[1] !== parts[1]!.split(':')[1];
    }

    /** Picker between `mainMembers[i]` and `[i+1]`: always in the cross-verse
     *  and missed-waqf accordions and while staged; in other categories only
     *  on a join that actually crosses a verse boundary (a cross-verse split
     *  seen from low_confidence, repetitions, …). */
    // Reactive (not a template-called function) so it re-derives when the
    // sidecar map lands after first render and `mainMembers` flips to staged.
    $: boundaryAt = mainMembers.map((a, i) => {
        const b = mainMembers[i + 1];
        if (!b) return false;
        if (staged || category === 'cross_verse' || isSplitMissedWaqf) return true;
        return groupMembers.length > 1 && isVerseBoundary(a, b);
    });

    /** End of this card's contiguous piece group, for rows to extend their
     *  bounded play across it: the pieces are slices of one window, so the
     *  audio must run through the whole group with the cursor walking piece to
     *  piece, not stop at each piece edge. Null for a single-piece card. */
    $: groupPlayEndMs = mainMembers.length > 1
        ? (mainMembers[mainMembers.length - 1]?.time_end ?? null)
        : null;

    // ---- WASL/WAQF keyboard binding (1 = waṣl, 2 = waqf) ----
    // Each mounted picker publishes its commit here, keyed by the uid of the
    // piece ABOVE it. A focused piece labels the boundary directly ABOVE
    // itself; the FIRST piece has none above, so it doubles for the one below
    // it. On a two-piece cross verse both pieces resolve to the single
    // boundary between them.
    let waslCommits: WaslCommits = new Map();

    function takeWaslCommit(uid: string, commit: ((_value: boolean) => void) | null): void {
        const next: WaslCommits = new Map(waslCommits);
        if (commit) next.set(uid, commit);
        else next.delete(uid);
        waslCommits = next;
    }

    function waslCommitFor(
        i: number,
        members: Segment[],
        commits: WaslCommits,
    ): ((_value: boolean) => void) | null {
        return waslCommitForPiece(i, members.map((mem) => mem.segment_uid), commits);
    }

    /** Dispatch the staged split from the picks so far (`stagedCommit`);
     *  returns the committed piece uids, or null when nothing was split. On a
     *  cross-verse card unanswered boundaries commit as WAQF but stay flagged
     *  pending, so their pickers keep asking and amend the same op in place
     *  (the post-split path). On a missed-waqf card only the cuts answered
     *  WAQF are cut; with none, the last answer (all WASL) ignores the item,
     *  while an edit on a piece leaves the seg and its picks untouched. */
    function materializeStaged(fromEdit = false): string[] | null {
        if (!staged || !stagedKey || !resolvedSeg) return null;
        const picks = get(stagedWaslPicks)[stagedKey] ?? [];
        const childUids = stagedChildUidsFor(stagedKey, staged.cursors.length);
        const plan = stagedCommit(stagedCategory, staged, picks, childUids);
        if (plan.kind === 'none') {
            if (fromEdit) return null;
            clearStagedPicks(stagedKey);
            handleIgnore();
            return null;
        }
        const { split: cut, wasls, newUids } = plan;
        try {
            const commit = commitSplit(resolvedSeg, cut.cursors, {
                refs: cut.refs,
                wasls,
                newUids,
                contextCategory: category,
            });
            if (!commit) return null;
            finalizeSplit(commit);
            const pieceUids = commit.pieces.map((p) => p.segment_uid ?? '');
            if (isMissedWaqfCard) return pieceUids;
            for (let i = 0; i < cut.cursors.length; i++) {
                if (picks[i] !== undefined) continue;
                const left = commit.pieces[i]?.segment_uid;
                if (left) markWaslPending(left);
            }
            return pieceUids;
        } catch (err) {
            console.warn('Staged split: commit failed:', err);
            return null;
        } finally {
            clearStagedPicks(stagedKey);
        }
    }

    /** An edit action on staged piece `uid`: commit, then let the action run
     *  only if that piece is now real (a missed-waqf piece whose start cut was
     *  not answered WAQF is merged away, so its action is a no-op). */
    function activateStagedPiece(uid: string | null): boolean {
        const pieceUids = materializeStaged(true);
        return uid != null && pieceUids != null && pieceUids.includes(uid);
    }

    function onStagedPick(i: number, value: boolean): void {
        if (!staged || !stagedKey) return;
        const n = staged.cursors.length;
        // Relabelling an ignored missed-waqf item starts from its all-WASL answer.
        if (ignoredAsWasl && !get(stagedWaslPicks)[stagedKey]) {
            for (let j = 0; j < n; j++) setStagedPick(stagedKey, j, true, n);
        }
        setStagedPick(stagedKey, i, value, n);
        if (allPicked(get(stagedWaslPicks)[stagedKey], n)) materializeStaged();
    }

    /** Relabel a cut of a split missed-waqf item: WASL (no stop) merges the
     *  two pieces back; merging the last cut away ignores the item (all WASL). */
    function onCutPick(i: number, value: boolean): void {
        const left = mainMembers[i];
        if (!value || !left || isStagedSegment(left)) return;
        const lastCut = mainMembers.length === 2;
        try {
            mergeAdjacent(left, 'next', category);
            if (!lastCut || left.chapter == null) return;
            const merged = getChapterSegments(left.chapter)
                .find((s) => s.segment_uid === left.segment_uid);
            if (merged) ignoreIssueOnSegment(merged, category);
        } catch (err) {
            console.warn('Missed waqf: relabel failed:', err);
        }
    }
    $: firstMember = mainMembers[0] ?? null;
    $: lastMember = mainMembers.length > 0 ? mainMembers[mainMembers.length - 1] ?? null : null;

    $: prevSeg = ((): Segment | null => {
        if (!showContext || ctxNextOnly || !firstMember || firstMember.chapter == null) return null;
        const p = getAdjacentSegments(firstMember.chapter, firstMember.index).prev;
        // Guard against prev being itself a split-group member (shouldn't
        // happen given time_start sort + splice, but keeps the contract safe).
        if (p && p.segment_uid && groupMembers.some((m) => m.segment_uid === p.segment_uid)) return null;
        return p;
    })();
    $: nextSeg = ((): Segment | null => {
        if (!showContext || !lastMember || lastMember.chapter == null) return null;
        const n = getAdjacentSegments(lastMember.chapter, lastMember.index).next;
        if (n && n.segment_uid && groupMembers.some((m) => m.segment_uid === n.segment_uid)) return null;
        return n;
    })();

    // Ordered sibling list — render order — passed to every SegmentRow so
    // playback prefetch can warm the next sibling's clip URL by list
    // position. Mirrors the template below exactly.
    $: siblings = ((): Segment[] => {
        const out: Segment[] = [];
        if (prevSeg) out.push(prevSeg);
        for (const m of mainMembers) out.push(m);
        if (nextSeg) out.push(nextSeg);
        return out;
    })();

    // Open default context once resolvedSeg becomes available.
    let _didAutoOpen = false;
    $: if (resolvedSeg && ctxDefaultOpen && !_didAutoOpen) {
        showContext = true;
        _didAutoOpen = true;
    }

    // Track ignored state reactively — the whole group counts as ignored only
    // when every real member is. Pieces of a split missed-waqf item inherit a
    // relabelled parent's ignore, but the split is their label.
    $: if (resolvedSeg) {
        isAlreadyIgnored = (void segStoreTick, realMembers.length > 0 && !isSplitMissedWaqf
            && realMembers.every((mem) => isIgnoredFor(mem, category)));
    }

    // ---- Public interface (forwarded from ErrorCard dispatcher) ----
    export function getIsContextShown(): boolean { return showContext; }
    export function showContextForced(): void { showContext = true; dispatch('contextchange', true); }
    export function hideContextForced(): void { showContext = false; dispatch('contextchange', false); }

    function toggleContext(): void {
        showContext = !showContext;
        dispatch('contextchange', showContext);
    }

    // ---- Ignore handler ----
    // Ignore applies to every real member of the group (a split cross-verse
    // seg seen from another category is N pieces, ignored together). While
    // still staged only the parent is real; its ignore is inherited by the
    // pieces at commit (the reducer clones the parent).
    /** Ignore the item on every real member; a staged card's pieces are not
     *  in the store yet, so the seg itself takes the ignore. */
    function handleIgnore(): void {
        if (!resolvedSeg) return;
        const targets = realMembers.length > 0 ? realMembers : [resolvedSeg];
        try {
            let any = false;
            for (const mem of targets) {
                if (ignoreIssueOnSegment(mem, category)) any = true;
            }
            if (any) isAlreadyIgnored = true;
        } catch (err) {
            console.warn('Ignore: dispatch failed:', err);
        }
    }

    $: contextPreviousLabel = tr($localeStore, m.segments_validation_context_label_previous());
    $: contextNextLabel = tr($localeStore, m.segments_validation_context_label_next());
    $: ignoreTitle = tr($localeStore, isDirtySegment
        ? m.segments_validation_ignore_dirty_title()
        : m.segments_validation_ignore_default_title());
    $: ignoreButtonLabel = tr($localeStore, isAlreadyIgnored ? m.segments_validation_ignored_label() : m.segments_validation_ignore_button());
    $: contextToggleLabel = tr($localeStore, showContext ? m.segments_validation_hide_context_button() : m.segments_validation_show_context_button());
</script>

<div style:opacity={isAlreadyIgnored ? 0.5 : null}>
    {#if issueMsg}
        <div class="val-card-issue-label">{issueMsg}</div>
    {/if}
    {#if isBoundaryReview}
        <BoundaryEvidence {category} {item} />
    {/if}
    {#if resolvedSeg}
        {#if prevSeg}
            <SegmentRow
                seg={prevSeg}
                isContext={true}
                contextLabel={contextPreviousLabel}
                showPlayBtn={true}
                showChapter={true}
                accordionSiblings={siblings}
            />
        {/if}
        {#each mainMembers as mem, i (mem.segment_uid ?? `${mem.chapter}:${mem.index}`)}
            {@const memStaged = isStagedSegment(mem)}
            <SegmentRow
                seg={mem}
                showGotoBtn={true}
                showPlayBtn={true}
                showChapter={true}
                staged={memStaged}
                onStagedActivate={memStaged ? activateStagedPiece : null}
                validationCategory={category}
                accordionSiblings={siblings}
                onCardIgnore={canIgnore ? handleIgnore : null}
                onCardToggleContext={toggleContext}
                onCardSetWasl={waslCommitFor(i, mainMembers, waslCommits)}
                groupEndMs={groupPlayEndMs}
            />
            {#if boundaryAt[i]}
                {@const next = mainMembers[i + 1]}
                {#if next && memStaged}
                    <WaslBoundary
                        leftSeg={mem}
                        rightSeg={next}
                        stagedValue={stagedPicks[i]}
                        onPick={(v) => onStagedPick(i, v)}
                        onCommitReady={takeWaslCommit}
                    />
                {:else if next && isSplitMissedWaqf}
                    <WaslBoundary
                        leftSeg={mem}
                        rightSeg={next}
                        stagedValue={false}
                        onPick={(v) => onCutPick(i, v)}
                        onCommitReady={takeWaslCommit}
                    />
                {:else if next}
                    <WaslBoundary leftSeg={mem} rightSeg={next} onCommitReady={takeWaslCommit} />
                {/if}
            {/if}
        {/each}
        {#if showWaslPicker && lastMember && nextSeg}
            <WaslBoundary leftSeg={lastMember} rightSeg={nextSeg} onCommitReady={takeWaslCommit} />
        {/if}
        {#if nextSeg}
            <SegmentRow
                seg={nextSeg}
                isContext={true}
                contextLabel={contextNextLabel}
                showPlayBtn={true}
                showChapter={true}
                accordionSiblings={siblings}
            />
        {/if}
    {/if}
    <div class="val-card-actions">
        {#if canIgnore}
            <button
                class="val-action-btn ignore-btn"
                disabled={isAlreadyIgnored || isDirtySegment}
                title={ignoreTitle}
                use:editGate
                on:click={handleIgnore}
            >{ignoreButtonLabel}</button>
        {/if}
        <button
            class="val-action-btn val-action-btn-muted val-ctx-toggle-btn"
            on:click={toggleContext}
        >{contextToggleLabel}</button>
    </div>
</div>
