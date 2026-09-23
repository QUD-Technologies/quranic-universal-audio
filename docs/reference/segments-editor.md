# Segments editor

The Segments tab is the full WIP editor: trim / split / merge / re-reference / delete / ignore, with auto-validation on save. Edits are `SegmentCommand`s applied by a pure reducer to a normalized client store, recorded as an op log, POSTed as a save payload, and replayed server-side against the same domain layer onto `detailed.json`. Save and undo round-trip through the Pydantic schemas in `qua_shared/schemas/` — writers never construct dict literals.

## Module map

| Concern | Path |
|---|---|
| FE command union | `inspector/frontend/src/tabs/segments/domain/command.ts` |
| FE reducer | `inspector/frontend/src/tabs/segments/domain/apply-command.ts` |
| FE identity | `inspector/frontend/src/tabs/segments/domain/identity.ts` |
| FE registry (validation twin) | `inspector/frontend/src/tabs/segments/domain/registry.ts` |
| FE inverse-patch (client undo) | `inspector/frontend/src/tabs/segments/domain/inverse-patch.ts` |
| FE normalized store | `inspector/frontend/src/tabs/segments/stores/segments.ts` |
| FE dirty / op log | `inspector/frontend/src/tabs/segments/stores/dirty.ts` |
| FE history store | `inspector/frontend/src/tabs/segments/stores/history.ts` |
| FE edit utils | `inspector/frontend/src/tabs/segments/utils/edit/` |
| FE save utils | `inspector/frontend/src/tabs/segments/utils/save/` |
| FE history utils | `inspector/frontend/src/tabs/segments/utils/history/` |
| BE save | `inspector/services/segments/save.py` |
| BE undo | `inspector/services/segments/undo.py` |
| BE read query | `inspector/services/segments/segments_query.py` |
| BE auto-split lookup | `inspector/services/segments/auto_split.py` |
| BE history query | `inspector/services/activity/history_query.py` |
| BE patch domain | `inspector/domain/command.py` (`SegmentPatch`, `apply_inverse_patch`) |
| BE identity | `inspector/domain/identity.py` |
| Adapters | `inspector/adapters/{save_payload,segments_json,detailed_json}.py` |
| Routes (mutations) | `inspector/routes/segments/edit.py` |
| Routes (read history) | `inspector/routes/segments/validation.py` |
| On-disk schemas | `qua_shared/schemas/bucket/{segment,edit_history}.py` |

### Service-module import contract

Service modules live in domain subpackages (`services/segments/`, `services/activity/`, …). `inspector/services/__init__.py` re-aliases every submodule into `sys.modules` under its legacy flat name. Both spellings resolve to the same module object:

| Canonical | Legacy alias (still used by routes/tests) |
|---|---|
| `services.segments.save` | `services.save` |
| `services.segments.undo` | `services.undo` |
| `services.segments.auto_split` | `services.auto_split` |
| `services.activity.history_query` | `services.history_query` |

When adding a service module, drop it in the subpackage and add one re-export line in `services/__init__.py`.

## Edit operations

Each user action dispatches a `SegmentCommand` through `applyCommand`. `confidence` is set to `1.0` by every command that resolves an alignment (split children inherit parent confidence unchanged until ref-edited). FE edit modules build the command + dispatch; the reducer owns mutation.

| Op | Command type | Confidence after | FE module | BE handling |
|---|---|---|---|---|
| Trim | `trim` | `1.0` | `utils/edit/trim.ts`, `trim-zoom.ts`, `enter.ts` | full_replace path; `_make_seg` + `_stamp_persisted_classifier_fields` |
| Split | `split` | children inherit parent (→ `1.0` on later ref-edit) | `utils/edit/split.ts`, `split-zoom.ts`, `enter.ts` | full_replace; new child UIDs persisted |
| Merge | `merge` | `1.0` | `utils/edit/merge.ts` | full_replace; consumed UID dropped, kept UID = earlier seg |
| Edit reference | `editReference` (`op_type` `edit_reference` \| `confirm_reference`) | `1.0` | `utils/edit/reference.ts` (`beginRefEdit`/`commitRefEdit`) | patch path (field-level) or full_replace |
| Delete | `delete` | n/a | `utils/edit/delete.ts` | full_replace; seg removed |
| Auto-fill / auto-fix | `autoFixMissingWord` | `1.0` | `utils/edit/auto-fix.ts`, `reference.ts` | full_replace; `op_context_category` defaults `missing_words`, `fix_kind=auto_fix` |
| Ignore | `ignoreIssue` | `1.0` | `utils/edit/ignore.ts` | patch path; appends `category` to `ignored_categories` |
| Set wasl | `setIsWasl` | unchanged | `utils/edit/setIsWasl.ts` | full_replace; `is_wasl` toggled (omitted when `False`); TS-affecting |

Edit-flow support modules in `utils/edit/`:

| File | Role |
|---|---|
| `common.ts` | `exitEditMode`, `finalizeEdit` (attaches forward `patch` to the op) |
| `enter.ts` | `enterEditWithBuffer` — shared entry for trim / split |
| `reference.ts` | `beginRefEdit` / `commitRefEdit` |

The reducer never writes `seg.ignored_categories` except for the explicit `ignoreIssue` command. Editing from a validation accordion card records `op_context_category` (history pill provenance) but does NOT mutate the persisted ignore list — soft-card dismissal is FE session state.

## Command grammar

`SegmentCommand` is a discriminated union on `type`. TS twin: `domain/command.ts`. Python has no command-object twin — the on-wire op carries a `command` envelope validated by name in `save.py::_validate_command_envelopes`; the actual mutation is replayed from the save payload + the op `patch`, not from re-running a Python reducer.

Command variants (TS):

| `type` | Key fields |
|---|---|
| `trim` | `segmentUid`, `delta:{time_start?,time_end?}` |
| `split` | `segmentUid`, `splitMs:number\|number[]`, `newUids?`, `refs?`, `texts?`, `wasls?` (+ legacy `firstRef`/`secondRef`/`secondHalfUid`) |
| `merge` | `fromUid`, `toUid`, `mergedRef?`, `mergedText?`, `direction?` |
| `editReference` | `segmentUid`, `matched_ref`, `matched_text?`, `opType?` |
| `delete` | `segmentUid` |
| `ignoreIssue` | `segmentUid`, `category` |
| `autoFixMissingWord` | `segmentUid`, `matched_ref`, `matched_text?` |
| `setIsWasl` | `segmentUid`, `is_wasl` |

`CommandBase` carries `sourceCategory`/`contextCategory` (history-pill provenance), `fixKind` (`manual`\|`auto_fix`\|`audit`\|`ignore`), and `_mountId` (UI binding; reducer ignores it).

### Reducer — `apply-command.ts`

`applyCommand(state, command, ctx)` is pure: no store writes, no I/O, no input mutation. Returns `CommandResult`:

- `nextState: CommandNextState` — `{byId, affectedChapter, removedSegmentUids?, insertedSegmentUids?, idsByChapter?}`.
- `operation: CommandOperation` — extends wire `EditOp` with `type`, `kind` (`structural`\|`single-index`), `snapshots:{before,after}`, `targetSegmentIndex:{chapter,index}`, `command` envelope.
- `affectedChapters: number[]`.
- `validationDelta?: {resolved, introduced}` — `resolved` populated from `sourceCategory`/`contextCategory` for per-segment registry categories.
- `patch: SegmentPatch` — `{before, after, removedIds, insertedIds, affectedChapterIds}`, the forward diff that drives both client and server undo.

Wire `op_type` mapping (`OP_TYPE_BY_COMMAND`): `trim→trim_segment`, `split→split_segment`, `merge→merge_segments`, `editReference→edit_reference`, `delete→delete_segment`, `ignoreIssue→ignore_issue`, `autoFixMissingWord→auto_fix_missing_word`, `setIsWasl→set_is_wasl`. Structural commands (`trim`/`split`/`merge`/`delete`) force the chapter's save into full_replace mode.

Op recording: dispatcher calls reducer → gets `operation` → `finalizeOp(chapter, op)` pushes it onto `_opLog[chapter]` (`stores/dirty.ts`) and `markDirty(chapter, index, structural)`. `finalizeEdit` (`utils/edit/common.ts`) attaches the forward `patch` to the op so client-side discard can invert it.

### Snapshots — `snapshotSeg` (`stores/dirty.ts`)

Each op carries `targets_before`/`targets_after` (and mirror `snapshots.before/after`) of `SegSnapshot` dicts. Snapshot fields: `segment_uid`, `index_at_save`, `audio_url`, `time_start`, `time_end`, `matched_ref`, `confidence`, optional `wrap_word_ranges`/`entry_ref`/`chapter`/`ignored_categories`/`is_wasl`. Migration #5: `matched_text` and `phonemes_asr` are NOT snapshotted (derivable / retired). Backend `save.py::_attach_classified_issues` enriches each persisted snapshot with `classified_issues: string[]` at write time so the History delta reads it directly.

## Normalized state — `stores/segments.ts`

`SegmentState = {byId: Record<uid, Segment>, idsByChapter: Record<chapter, uid[]>, selectedChapter}`. `byId` is keyed by `segment_uid`; `idsByChapter` preserves render order.

- `segmentsStore` is a `derived` store over `segAllData` + `selectedChapter` (`stores/chapter.ts`) — it rebuilds via `buildSegmentState` whenever upstream fires (load, save republish). No imperative population.
- Pure selectors operate on a `SegmentState` value (not the store): `getChapterSegments`, `getSegByChapterIndex`, `getAdjacentSegments`, `findByUid`. ~50 legacy call sites depend on these.
- `applyNextState(current, slice)` is a pure reducer that folds a `CommandNextState` into a `SegmentState`. Used by client-side patch flows; production write goes through `segAllData.update` which re-derives `segmentsStore`.
- Segments without `segment_uid` are skipped (kept out of `byId`).

Dependent stores: `dirty.ts` (op log + dirty map), `validation.ts`, `filters.ts`, `history.ts`, `playback.ts`.

### Dirty op log — `stores/dirty.ts`

Module-level `Map`s keyed by `number` chapter: `_dirtyMap` (`{indices:Set<number>, structural:boolean}`), `_opLog` (`EditOp[]`). `dirtyTick` writable bumps on every mutation; `isDirtyStore` derives from it. Save reads `getDirtyMap()` + `getChapterOps(ch)`; on success calls `clearSavedOps(ch, ops)` (drops only saved op_ids, recomputes dirty entry) so concurrent edits queued during the fetch survive. `amendSegInOp` overlays fields onto an op's `targets_after`/`snapshots.after`/`patch.after` in sync (post-CV-split wasl picks amend the split op in place rather than emit a `set_is_wasl` row).

## Segment identity

UUID5, deterministic. TS and Py twins MUST produce identical strings for the same `(chapter, original_index, start_ms)` triple.

```
NAMESPACE_INSPECTOR = uuid.UUID("00000000-0000-0000-0000-000000000001")
uid = uuid5(NAMESPACE_INSPECTOR, f"{chapter}:{original_index}:{start_ms}")
```

- Py: `domain/identity.py::derive_uid(chapter, original_index, start_ms)`, `backfill_entry_uids`, `backfill_entries_uids` (chapter parsed from `entry["ref"]`).
- TS: `domain/identity.ts::deriveUid({chapter, originalIndex, startMs})`, `backfillSegmentUids(segs, chapter)`. TS ships a hand-rolled synchronous SHA-1 (`_sha1`) so derivation is callable in store initializers / vitest (no async `crypto.subtle`).

Invariant: changing the namespace, the format string, or the input triple breaks identity continuity across saves. Existing UIDs are never re-derived (`backfill` skips any seg that already has one — MUST-4).

Identity lifecycle:

| Event | UID source |
|---|---|
| First server load, no UID | Backfilled deterministically (`backfill_entries_uids` on read in `adapters/detailed_json.py::load_entries_from_bytes`; FE `backfillSegmentUids`) |
| Subsequent loads | Read from `detailed.json` |
| Split | First child reuses parent UID; later children take `cmd.newUids[]` else `crypto.randomUUID()` |
| Merge | Merged seg keeps the **earlier** seg's UID; later seg's UID dropped (in `removedIds`) |
| Trim / edit-reference / ignore / set-wasl | UID preserved |
| Delete | UID removed (in `patch.removedIds`) |

## Save flow

Client → server, ordered. Mutating routes require `@require_same_origin` → `@require_edit_lock(admin_bypass=True)` (`routes/segments/edit.py`).

1. **Build payload (client)** — `utils/save/execute.ts::executeSave(isAutoSave?)`. Iterates `getDirtyMap()`. Per chapter: `structural` entry → `SavePayloadFull` (`{full_replace:true, segments:[...], operations:chOps}`) from `getChapterSegments(ch)`; else `SavePayloadPatch` (`{segments:[{index, segment_uid, matched_ref, confidence, ignored_categories?, is_wasl?}], operations:chOps}`). Payload omits `matched_text`/`phonemes_asr` (server derives / retired). `op_peaks` attached when in-memory peaks exist (`collectOpPeaks`). Snapshots payload before any network IO so concurrent edits don't mix in. `utils/save/payload.ts::buildPayloadFromCommandResult` builds the same shape from a `CommandResult`. Preview UI built by `utils/save/preview.ts` + `actions.ts`.
2. **POST (client)** — `POST /api/seg/save/<reciter>/<chapter>` (raw `fetch`, `credentials: same-origin`). 401 → sign-in modal; other non-2xx → error toast.
3. **Route (server)** — `edit.py::seg_save` validates body has `segments`, builds `Actor` from `g.current_user` (`_actor_from_g`), calls `save_seg_data(reciter, chapter, updates, actor=)`.
4. **Save (server)** — `services/segments/save.py::save_seg_data`:
   1. `_validate_command_envelopes` — every op with a `type` must carry a `command` whose `type` is in `_ALLOWED_COMMAND_TYPES` and equals `op.type`. (Patch-style ops without `type` pass through.)
   2. `load_detailed(reciter)`; filter `matching` entries for `chapter`; build `(existing_by_time, existing_by_uid)` lookups (`adapters/save_payload.build_seg_lookups`).
   3. Apply: `_apply_full_replace` (rebuilds `segments` via `adapters/save_payload.make_seg`; by_ayah routes each payload seg to its entry by `audio_url`) or `_apply_patch` (field-level by `index`). Both call `_stamp_persisted_classifier_fields` (sets `qalqala_letter` via `compute_qalqala_letter`, `is_boundary_adj` structural-only) so the validate fast-path reads persisted fields instead of recomputing.
   4. `_persist_and_record`: `_validate_op_patches` → `persist_detailed` (atomic `detailed.json` write through `data_dir.write_detailed_doc`, then `rebuild_segments_json`) → build batch (`schema_version`, `batch_id=uuid7()`, `chapter`, `saved_at_utc`, `save_mode`, `operations`, `actor`) with `_ensure_patch_on_ops` + `_attach_classified_issues` → `data_dir.append_edit_history` → `append_peaks_records` (op_peaks) → cache invalidation.
   5. Returns `{"ok": True}` or `({"error": ...}, status)`.
5. **Refresh (client)** — on success: `refreshValidation()` FIRST (ships new `split_group_index`), then deferred `clearSavedOps` per chapter, then `resetHistoryLoader()` so the next History open re-fetches.

`segments.json` rebuild (`adapters/segments_json.build_segments_doc`): verse-aggregated `{verse_ref: [[start_word, end_word, t_from, t_to], ...]}`, cross-verse spans keyed by the compound `matched_ref`. `_meta` preserved from the existing on-bucket doc.

Cache invalidation (`services/storage/cache.py`): `pop_seg_caches_affected_by_segment_edit`, `append_history_batch` (extends cached parsed list in place — no re-parse), `_refresh_split_group_index_on_save` (pops split-group index only when batch has split ops), `pop_seg_auto_split` only when `batch_changes_segment_set`.

### detailed.json segment shape — `qua_shared/schemas/bucket/segment.py`

`DetailedSegment` (`extra="forbid"`): required `time_start`/`time_end`/`matched_ref`; `qalqala_letter`, `is_boundary_adj`, `confidence`, `wrap_word_ranges`, `segment_uid`, optional historical/sample `word_timings`, `ignored_categories`, `ignored` (legacy wildcard), `is_wasl`, `flag` (`SegmentFlag | None`, see [Flagged issues](#flagged-issues)). Writers emit via `model_dump(exclude_none=True)`. A structural save preserves `word_timings` only when the row's bounds and reference are unchanged; a trim or reference edit drops them as stale. Migration #5 dead fields stripped on read with INFO log: `matched_text`, `phonemes_asr`, `has_repeated_words`, plus snapshot-only `audio_url`/`chapter`/`entry_ref`/`index_at_save`/`display_text`. Unknown keys → WARNING + strip (writer-drift signal). `DetailedEntry` strips legacy `audio`. Both extraction (`.local/extraction/segments/outputs.py`) and Inspector save MUST round-trip through these models.

## edit_history.jsonl

Append-only JSONL at the reciter's storage root (`<storage>/<slug>/edit_history.jsonl`, routed by `services/storage/data_dir.py`). Schema: `qua_shared/schemas/bucket/edit_history.py`.

| Record kind | Written by | Distinguishing field |
|---|---|---|
| Batch | `save.py::_persist_and_record` (one per save) | `operations[]`, no `reverts_*` |
| Revert | `undo.py::_append_revert_record` (one per undo) | `reverts_batch_id` (+ `reverts_op_ids` for per-op undo); empty `operations` |

`EditHistoryBatch` fields: `schema_version` (default `1`), `batch_id`, `saved_at_utc`, `actor:{hf_user_id, login_at_time, role}`, `operations`, `chapter` (Inspector save) **or** `chapters` (pipeline multi-chapter), `batch_type` (pipeline only), `reverts_batch_id`/`reverts_op_ids`. `EditOperation`: `op_id`, `kind` (user) **or** `op_type` (pipeline), `fix_kind`, `op_context_category`, `patch` (typed `EditOpPatch`), `targets_before`/`targets_after`. `op_context_category` (the validation category the edit was launched from — read by `build_resolved_by_edit_index`) and `patch` (the forward-change envelope the undo path reverses) are **declared live fields**, not extras. `save_mode` is **no longer persisted** on a batch — it's derived wire-side from the batch's op shape in `history_query.py::_derive_save_mode` (the pure-`forbid` batch model rejects any on-disk `save_mode`; the prod migration stripped it).

Read endpoint: `GET /api/seg/edit-history/<reciter>` (`routes/segments/validation.py::seg_edit_history` → `services/activity/history_query.py::load_edit_history`). The query filters fully-reverted batches, strips per-op-reverted ops, merges batches sharing a `batch_id` (multi-chapter saves), enriches snapshot `audio_url` from the audio manifest, and aggregates summary stats. Clients see only the effective history.

**Integrity.** There is no standalone `validators/validate_edit_history.py` and no CI integrity job — the v1 file-hash chain and genesis record were dropped in v2. Integrity is enforced structurally by the schema: every model is pure `extra="forbid"`, so any unknown/legacy key (`file_hash_after`, `record_type`, `save_mode`, `reciter`, `applied_at_utc`, `snapshots`, `command`, …) raises `ValidationError` rather than being silently stripped. The prod-data migration already rewrote on-disk records to the canonical shape, so live data parses clean. `parse_edit_history_line` still returns `None` for genesis-style / `batch_id`-less lines.

Schema slim-down (Migration #5): snapshots no longer carry `matched_text` (derived via `services/reference/quran_refs.py::dk_text_for_ref` when needed — see `apply_inverse_patch::_hydrate`); per-op timestamps banned; `phonemes_asr`/`has_repeated_words` retired. New records are the slim shape; the prod migration rewrote legacy records to it (the pure-`forbid` model would otherwise reject the old keys).

## Undo

Patch-based, forward-only. Undo never rewinds history — it computes the reverse transformation, applies it to live entries, persists, and appends a **revert** record. `services/segments/undo.py`.

- `apply_reverse_op(entries, op, chapter_set)`: if the op carries a `patch`, route through `_reverse_via_patch` → `domain/command.py::apply_inverse_patch` (drop `insertedIds`, restore `before` snapshots in place, re-insert `removedIds`, re-sort affected chapters by `time_start`). Patches claiming chapters outside the batch scope raise `ValueError`. Ops without a `patch` (legacy) fall back to per-`op_type` branch helpers (`_reverse_trim`/`_reverse_split`/`_reverse_merge`/`_reverse_delete`/`_reverse_ref_edit`/`_reverse_ignore`), each verifying the current seg still matches `targets_after` (raises `ValueError` 409 on conflict).
- `undo_batch(reciter, batch_id, actor=)` — reverses all ops in a batch (skipping already-undone ops), in reverse order. Rejects: not found (404), is-a-revert (400), already fully reverted (400), all ops individually undone (400), conflict (409).
- `undo_ops(reciter, batch_id, op_ids, actor=)` — reverses a subset; rejects already-undone / not-in-batch ops.

Both persist via `persist_detailed`, append a revert record, then evict caches (`pop_seg_caches_affected_by_segment_edit`, `pop_seg_split_group_index`, conditional `pop_seg_auto_split`). Redo is not exposed (re-applying the forward batch would be the mechanism).

Endpoints (`routes/segments/edit.py`):

| Endpoint | Body | Service |
|---|---|---|
| `POST /api/seg/undo-batch/<reciter>` | `{batch_id}` | `undo_batch` |
| `POST /api/seg/undo-ops/<reciter>` | `{batch_id, op_ids}` | `undo_ops` |

Client: `utils/save/undo.ts` — `onBatchUndoClick`, `onOpUndoClick`, `onChainUndoClick` (reverses a split chain batch-by-batch in order), then `_afterUndoSuccess` marks `historyDataStale`, `refreshValidation()` + `reloadSegAll()`. **Client-only discard** of *unsaved* ops: `onPendingOpsDiscard` inverts each op's forward `patch` against `segAllData.segments` via `domain/inverse-patch.ts::applyInversePatchToSegments` (reverse op-log order), drops them from `_opLog`, and recomputes the dirty entry — no server round-trip, history untouched.

## History view

Server: `services/activity/history_query.py`. `load_edit_history` (cached, per-reciter lock) → `{batches, summary}`. `parse_history_for_reciter` reads raw records (cache-aware; save/undo append in place). Derived indices over the same parsed list: `build_split_group_index` (`{root_uid: [descendants]}`, transitive closure of `split_segment` ops, fixpoint capped at `_SPLIT_GROUP_MAX_PASSES=32`) and `build_resolved_by_edit_index` (`{uid: {category}}` for ops whose `op_context_category ∈ RESOLVES_BY_EDIT_CATEGORIES`; lets a card disappear after an edit without writing `ignored_categories`). `HISTORY_NEUTRAL_CATEGORIES = {basmala_amin, muqattaat}` are excluded from per-op classified-issue deltas in `save.py`.

Client store: `stores/history.ts` (`historyData`, `historyDataStale`, edit chains). Utils `utils/history/`: `loader.ts` (lazy per-reciter fetch + `resetHistoryLoader`), `render.ts` (response → store), `chains.ts` (`buildEditChains` — split-chain construction), `items.ts` (`groupRelatedOps`, flatten batches into display items), `actions.ts` (panel lifecycle). Components `components/history/`: `HistoryPanel`, `HistoryBatch`, `HistoryOp`, `HistoryArrows`, `HistoryFilters`, `EditChainRow`.

### Generation tiers (timestamps-staleness)

The edit-history response also carries `generations` — the ascending TS-generation boundary list (`services/segments/history_tiers.py::generation_timeline` over `repo_releases.all_releases_for_track_slug`, merged onto the cached blob in the route so the heavy parse stays cached). The FE partitions batches into **tiers** split by these boundaries (`items.ts::tierOf`): tier 0 = before the first generation ("Initial"), tier N = after generation #N, and the highest tier = the current edits not yet folded into the generated timestamps. `HistoryFilters` renders a **tier filter** (one pill per tier) that surfaces **only when the current tier holds timestamp-affecting edits** (`entryAffectsTimestamps` against `TS_AFFECTING_OP_TYPES` — the lockstep mirror of `qua_shared/segment_edit_ops.py`); with nothing pending the section is hidden. The "Pending regen" pill is the same drift that flags the reciter's `ts` track stale on the Releases tab (`segments_edited` — see [`dataset-and-releases.md`](dataset-and-releases.md)).

## Flagged issues

A manual "needs a second look" annotation any editor can attach to a segment — a required root comment plus an append-only reply thread. **Not a validation category**: it never appears in the filter dropdown or issue types, and produces no history category pill.

- **Persistence.** `DetailedSegment.flag: SegmentFlag | None` (`qua_shared/schemas/bucket/segment.py`). `SegmentFlag = {comment, actor:{hf_user_id, login_at_time, role}, flagged_at_utc, follow_ups:[{comment, actor, at_utc}]}`. Absent on unflagged segs (`exclude_none`). The flag is **backend-owned** — mutated only by `save.py::_apply_flag_ops`; `make_seg` preserves it across a full_replace, and `apply_inverse_patch` preserves the live value across an unrelated undo (it is never carried in `snapshotSeg`, so an FE snapshot can't corrupt it).
- **Command + save.** `flagSegment` command (`domain/command.ts`, op_type `flag_segment`, `op_context_category=null`, single-index patch). `_apply_flag_ops` resolves the target by `segment_uid` and rebuilds the flag through the `SegmentFlag` model with a **server-authoritative** actor + timestamp (the client never supplies identity). Three intents: `set` (create, comment required), `edit` (replace root comment, flagger-only — an **empty comment removes the flag**, the unflag mechanism), `followup` (append a reply, any editor). The FE dispatch is `utils/edit/flag.ts::flagSegment`; it rides the normal op-log + autosave path.
- **Read / redaction.** `GET /api/seg/all` (`routes/segments/data.py`) reduces the persisted flag to the FE shape via `services/segments/flags.py::flag_view`: author identity is shown as **role only** unless the caller holds `segments.see_flagger_identity` (owner + maintainer), and each comment carries a non-identifying `mine` so the flagger can find their own to edit. `count_flagged` powers the admin Review-drawer `flagged_issues_count`.
- **UI.** A non-registry **Flagged Issues** accordion renders at the top of `ValidationPanel.svelte` only when `segAllData.segments` has flagged segs; each row is a `FlaggedCard.svelte` (live `SegmentRow` + comment thread). Every card gets a flag button + inline comment editor + hover tooltip on `SegmentRow.svelte`. History shows the op as **Flag comment** (`EDIT_OP_LABELS`). Guide: the `flagging` accordion guide (last in `REQUIRED_GUIDE_KEYS`).
- **Notification.** A `followup` on a flag whose root author isn't the replier notifies the original flagger via `services.notifications.notify_flag_reply` (collected by `_apply_flag_ops`, emitted by `save_seg_data` after a successful save, best-effort, own durable txn). Surfaces on the flagger's Dashboard "My Notifications" rail — see [notifications.md](notifications.md).

## Auto-split / auto-detect

**Auto-split** (`services/segments/auto_split.py`) is read-only lookup, not an edit op. MFA alignment that produces cursor positions runs **offline** (`qua_shared/auto_split_precompute.py`), persisting `<reciter>/auto_split_v1.json` keyed by `segment_uid`. At runtime `POST /api/seg/auto-split/<reciter>` (`edit.py::seg_auto_split`) → `compute_auto_split` reads the sidecar via `load_auto_split` (O(1), in-memory) and returns `{cursors:int[]|None, refs:str[]|None, kind:"cross_verse"|"repetition"|null, source:"sidecar"|"miss"}`. On a miss the FE flips the button from *Auto Split* back to plain *Split* and falls back to manual single-cursor placement. The returned `cursors`/`refs` feed a `split` command (`cmd.splitMs` array + `cmd.refs`).

### Staged cross-verse split (label-to-save)

A cross-verse card whose seg has a usable sidecar entry (`kind: cross_verse`, `refs.length == cursors.length + 1`, cursors strictly inside the seg — `utils/validation/staged-split.ts::resolveStagedSplit`) does **not** show the single row + Auto Split button. `GenericIssueCard.svelte` renders the N **staged pieces** up front (`buildStagedChildren`: parent sliced at the cursors, refs from the sidecar, piece 0 keeps the parent uid/index, later pieces take uids memoised per parent in `stores/staged-split.ts`) with a `WaslBoundary` picker between each pair. Staged rows (`SegmentRow staged`) look and act like any other row — play seeks to the piece's own start and runs to the parent's end so the cursor walks card to card (`stagedPlayheadWindow` names the piece holding the cursor, and only that row shows as playing), the waveform is drawn from the piece's own range through the `data-hist-*` attributes — except that they register in the playback row registry carrying themselves as the entry's `segOverride` (they share the parent's `(chapter, index)`, so without it `drawActivePlayhead` would resolve the parent's undivided window from the store and the piece would show no cursor — it paints each piece against its own window, only while the playhead is inside it, and erases a piece once on exit; `_pickHandoffMountId` skips these entries so a programmatic edit never claims a staged mount) and every edit action (Adjust / Split / Merge / Delete / Edit Ref / flag / ref click) first calls `onStagedActivate` → the card's `materializeStaged()` (commit with the picks so far; unanswered boundaries commit as WAQF and are marked `pendingWaslConfirm` so their pickers keep asking), waits a tick, then runs against the now-real piece (same uid, same mounted row).

Labelling is the whole edit. Picks are session state (`stagedWaslPicks`, keyed by parent uid); **nothing is dispatched until every boundary is labelled** (or an edit action materialises the split early). The last pick calls `utils/edit/split-commit.ts::commitSplit` with `refs`, `wasls[]`, the memoised `newUids` and `contextCategory = <card category>`, then `finalizeSplit` — one `split_segment` op on the normal op-log + autosave path, no ref-edit chain. Because the op's `command.wasls` is set, `execute.ts::isChapterHalfDirty` treats it as complete (no ref-confirm chain to wait for) so autosave fires. Once committed the real pieces take over via the split-group members and the pickers fall back to their default path (pending-split amend via `amendSegInOp`, or `set_is_wasl` after save). `confirmSplit` (canvas edit mode) shares the same `commitSplit` core and keeps its chain epilogue.

The same staged shape appears when a cross-verse seg surfaces in **another** per-segment category (low_confidence, repetitions, …); after commit those cards show the N pieces with a picker on every join that crosses a verse (`boundary-state.ts::isVerseBoundary`). **Ignore** on a generic card applies to every real member of the group (`realMembers`); while still staged only the parent is real and its `ignored_categories` are inherited by the pieces at commit.

**Boundary chips + filter.** The cross-verse accordion header shows **Unset · Wasl · Waqf** chips, each with a **boundary** count (a three-verse item contributes two). `utils/validation/boundary-state.ts::boundaryStates` reads, in order: committed/in-progress members (`is_wasl` per left member; a uid in `pendingWaslConfirm` is unset) → staged picks → a single unset boundary for an unsplit seg. The chips are a multi-select filter (`stores/boundary-filter.ts`, default Unset only, persisted globally); an item passes when any of its boundaries is selected. The badge counts items that still hold an unset boundary. So that labelled items stay reviewable, the validate response keeps **resolved** cross-verse items: `services/validation/cross_verse_resolved.py` walks effective edit-history batches for `split_segment` ops launched from the card (`op_context_category == cross_verse`) or whose parent ref spanned verses, and emits `SegValCrossVerseItem{resolved: true}` for every root still live and single-verse. Resolved items are excluded from `category_counts`, and the FE mark-ready gate (`utils/validation/blocking-count.ts`) drops them too — only an unlabelled cross-verse blocks. Registry: `IssueDefinition.boundaryFilter` (FE-only).

**Timestamps side.** Generation runs on the timing Space; an unsplit compound ref is read as waṣl on the fly (`expand_cross_verse`, see [`dataset-and-releases.md`](dataset-and-releases.md)) and nothing is persisted — labelling in the editor is what records the reading. `set_is_wasl` is in `TS_AFFECTING_OP_TYPES` (both the Python source and the FE mirror in `utils/history/items.ts`): a relabel after a generation marks the timestamps stale.

**Auto-detect** (`services/segments/auto_detect.py`) is unrelated to segment editing — it is the request-lifecycle reconciler that fires `reciter.alignment_completed` when new `reciters/<slug>/` folders appear (polling loop + `POST /api/admin/reconcile`). It does not touch `detailed.json` segments.


## Multi-riwayah

Every `matched_ref` in `detailed.json` is expressed in the **delivery's own
edition** — `_meta.riwayah` (Inspector slug; absent means Hafs) names which. The
tab renders that edition's script in that edition's font, resolves verse word
counts from its index, and shows its verse-marker glyph.

Because recognition and DP matching always run against Hafs, a non-Hafs seg also
carries the Hafs evidence: `source_ref` (the Hafs word span the matcher matched)
and `projection_support` (`full` / `partial`, where `partial` means the seg cuts
through an N:M relation). Both are `None` on every Hafs seg, so the serialization
is byte-identical to what it always was.

The one resolver is `services/reference/delivery_edition.py`: the catalog row is
the authority, `detailed.json`'s `_meta.riwayah` is evidence, and a disagreement
raises `RiwayahMismatch` rather than picking a side.

Full detail: [`editions.md`](editions.md).
