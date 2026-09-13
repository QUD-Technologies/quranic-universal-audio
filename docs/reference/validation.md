# Validation

Registry-backed segment validation. Single source of truth for category metadata: `inspector/services/validation/registry.py`, hand-mirrored to `inspector/frontend/src/tabs/segments/domain/registry.ts`. Parity asserted by `inspector/frontend/src/__tests__/registry/parity.test.ts`.

Two orderings exist and are distinct:
- `accordion_order` (registry field) — UI render order in `ValidationPanel.svelte`.
- dict insertion order in `_REGISTRY` — drives `PER_SEGMENT_CATEGORIES` / `_flags_to_categories` / `category_counts` emission. **These two orders are NOT the same** (e.g. `low_confidence_v2` is insertion-pos 6 but `accordion_order=6` while `audio_bleeding`/`boundary_adj`/`repetitions` carry `accordion_order` 7/8/9 yet sit at insertion positions 8/9/7). When adding a category, set BOTH and keep registry.py ↔ registry.ts byte-aligned.

## Categories

Accordion order (registry `accordion_order`). `card_type` = FE card subcomponent dispatch.

| order | kind | severity | scope | detection | where | card_type | key module | visibility |
|---|---|---|---|---|---|---|---|---|
| 1 | `failed` | error | per_segment | empty `matched_ref` | server+client | generic | `classifier.py` / `detail.py` |
| 2 | `missing_verses` | error | per_verse | verse in `word_counts` not covered by any seg key | server | missingVerses | `_structural.py::_check_structural_errors` |
| 3 | `missing_words` | error | per_verse | word-index gap in verse coverage map | server | missingWords | `_missing.py::_build_missing_words` |
| 4 | `structural_errors` | error | per_chapter | `t_from>=t_to`, `w_from<1`, `w_to<w_from`, time overlap | server | error | `_structural.py` |
| 5 | `low_confidence` | warning | per_segment | `confidence < LOW_CONFIDENCE_THRESHOLD` (count); `< LOW_CONFIDENCE_DETAIL_THRESHOLD` (detail list, synthetic `low_confidence_detail`) | server | generic | `classifier.py::classify_flags` |
| 6 | `low_confidence_v2` | warning | per_segment | `segment_uid ∈ probe_failed_uids` (MFA tight-beam probe sidecar, `load_probe_v2`) | server | generic | `classifier.py` + `_build_detail_lists` |
| 7 | `audio_bleeding` | warning | per_segment | by-ayah only: `not seg_belongs_to_entry(matched_ref, entry_ref)` | server | generic | `classifier.py` |
| 8 | `boundary_adj` | warning | per_segment | persisted `is_boundary_adj` (fall-through `compute_is_boundary_adj`) | server | generic | `classifier.py::_check_boundary_adj` |
| 9 | `repetitions` | warning | per_segment | `seg.wrap_word_ranges` truthy (`has_repeated_words` alone does NOT classify) | server | generic | `classifier.py` |
| 10 | `cross_verse` | warning | per_segment | `s_ayah != e_ayah` | server | generic | `classifier.py` |
| 11 | `qalqala` | info | per_segment | persisted `qalqala_letter` non-null (fall-through `compute_qalqala_letter`) | server | generic | `classifier.py` + `segments/qalqala.py` | **owner-only** |
| 12 | `muqattaat` | info | per_segment | `s_word==1 and (surah,s_ayah) ∈ MUQATTAAT_VERSES` | server | generic | `classifier.py` |
| 13 | `basmala_amin` | info | per_segment | per-chapter scan: first seg overlapping `1:1`, last overlapping `1:7`, + missed-Basmala augmentation gated on `len(deleted_basmala_chapters) >= MISSED_BASMALA_FLAG_MIN_DELETED` | server | generic | `detail.py::_build_detail_lists` |
| 14 | `hidden_pause` | info | per_segment | `segment_uid ∈ hidden_pause_v1.json::by_uid` (offline re-segmentation heard a pause inside the segment; `load_hidden_pause`) | server | generic | `classifier.py` + `_build_detail_lists` |
| 15 | `false_split` | info | per_segment | `segment_uid ∈ false_split_v1.json::by_uid` (offline re-segmentation heard continuous speech across the segment's end; `load_false_split`) | server | generic | `classifier.py` + `_build_detail_lists` |
| 16 | `unmarked_wasl` | info | per_segment | `segment_uid ∈ unmarked_wasl_v1.json::by_uid` (every re-segmentation arm read through the verse-to-verse join to the next segment and the delivery never marked `is_wasl`; `load_unmarked_wasl`) | server | generic | `classifier.py` + `_build_detail_lists` |

`hidden_pause` / `false_split` / `unmarked_wasl` are **review-only**: their arrays + `*_meta` blocks are emitted only to viewers holding `segments.view_boundary_review` (maintainer default; owner superuser) — the route caches the full payload per reciter and `strip_boundary_review` redacts per request (counts zeroed, keys absent → the FE hides the accordions). None is in `BLOCKING_COUNT_KEYS` or `REQUIRED_GUIDE_KEYS`. Each item carries the sidecar payload under `boundary` (hidden_pause: `cursors` / `refs` / `score` / `cuts[]` with per-axis `evidence`; false_split and unmarked_wasl share one shape: `next_uid` / `axes` / `gap_ms` / `score` / `word` / `final_class` / `is_wasl` / `evidence`); `score` = agreeing axes × 1000 + min(gap_ms, 999) and all three accordions default-sort by it descending. Auto Split on a `hidden_pause` row reads the cut from `/api/seg/auto-split` — `services/segments/auto_split.py` merges `hidden_pause_v1` entries that carry `refs` (kind `hidden_pause`; an `auto_split_v1` entry for the same uid wins); entries with `refs: null` fall back to plain Split. A `false_split` row resolves with the row's Merge ↓ (auto-suppress on edit, like every per-segment category). An `unmarked_wasl` row opens with the next segment in context (`ACCORDION_CONTEXT` `next_only`) and the WASL/WAQF picker between the two; the fix is `set_is_wasl` on the left segment (or Merge ↓ if it is one utterance) — never a split, so the row is not an Auto Split candidate and `auto_split.py` does not read the sidecar.

`owner_only` (registry field, default `False`) makes a category **invisible to everyone but an owner** — for a non-owner it may as well not exist. `ValidationPanel.svelte` drops the row from `buildBaseDescriptors` (gated on the `isOwner` store), so there is no accordion, no count badge and no filter entry; `REQUIRED_GUIDE_KEYS` is derived as `ALL_GUIDE_KEYS` minus owner-only categories so the guide never gates a first edit or shows in the guides checklist (the guide stays registered and readable from the owner's accordion `?`, and `GUIDE_VIEW_KEYS` in `inspector/constants.py` still accepts the view POST); and the category must never appear in `BLOCKING_COUNT_KEYS` — `mark-ready-copy.test.ts` asserts that. Currently: `qalqala`. This is a display gate, not authz: the validate response still carries the array (unlike the `view_boundary_review` redaction above, which is capability-gated server-side).

Detection runs server-side in the validate pass. The client classifies snapshots/issue records for display only (`utils/validation/classified-issues.ts`), it does not own primary detection. Parent-repo `validators/validate_segments.py` (pipeline, gitignored) references the same `kind` set — accordion + post-pipeline validator must stay aligned.

Suppression: `is_suppressed_for(seg, cat)` = `is_ignored_for` (reads `seg.ignored_categories`, legacy `"_all"` marker, legacy `ignored=True` bool) OR `is_resolved_by_edit` (transient `seg._resolved_by_edit`, injected by the validate route from `build_resolved_by_edit_index`, stripped before return). `failed` is gated only on `is_ignored_for` (resolved-by-edit does not suppress errors).

## Server engine — `inspector/services/validation/`

| File | Role |
|---|---|
| `__init__.py` | Orchestrator `validate_reciter_segments(reciter, include_boundary_review=True)` + `strip_boundary_review`. `ThreadPoolExecutor(7)` fan-out of the 7 independent bucket reads (`load_detailed` / `_load_resolved_idx_cached` / `load_probe_v2` / `load_hidden_pause` / `load_false_split` / `load_unmarked_wasl` / `load_seg_verses`). `canonical=None` throughout (phonemizer out of runtime). Builds `category_counts` + per-category arrays + `split_group_index`. Re-exports registry symbols + classifier API. |
| `registry.py` | `_REGISTRY` dict of frozen `IssueDefinition` rows. Derived tuples: `ALL/PER_SEGMENT/PER_VERSE/PER_CHAPTER/CAN_IGNORE/AUTO_SUPPRESS/PERSISTS_IGNORE/OWNER_ONLY_CATEGORIES`. `filter_persistent_ignores`, `registry_as_dict`. |
| `classifier.py` | Per-segment classification — single source of truth. `classify_flags`/`classify_segment`/`classify_segment_full`/`classify_entry`. `is_ignored_for`/`is_resolved_by_edit`/`is_suppressed_for`. `compute_is_boundary_adj` (raw rule, no suppression) + `_check_boundary_adj` (persisted-field short-circuit + suppression). qalqala persisted-field short-circuit (local import of `compute_qalqala_letter`). |
| `snapshot_classifier.py` | `classify_snapshot(snap)` — routes a loose SegSnapshot dict (history op-log shape) through `classify_segment`; no logic reimplemented. Used by save-flow history enrichment. |
| `detail.py` | `_build_detail_lists` — single entry walk producing every per-category detail array + `verse_segments` coverage map + `sequence_gaps` + `basmala_amin` (per-chapter scan, missed-Basmala augmentation). `_compute_surah_offsets` / `_word_ord` (prefix-sum, O(1) ordinals). Each item carries `classified_issues`. Also identity helpers `resolve_segment_by_uid` / `resolve_segment_for_issue` / `filter_stale_issues`. |
| `_missing.py` | `_build_missing_words` — verse coverage gaps → issue dicts with `auto_fix`/`auto_fix_up`/`auto_fix_down` targeting; merges `sequence_gaps`. |
| `_structural.py` | `_check_structural_errors` — reads `segments.json` (verse-aggregated) via `load_seg_verses`. Returns `(errors, missing_verses, stats)`. |

qalqala helper canonical path: `inspector/services/segments/qalqala.py::compute_qalqala_letter`. `services.qalqala` is an alias resolving to the same module (used by `classifier.py` fall-through and the retired perf report).

### Routes — `inspector/routes/segments/validation.py` (`/api/seg`, bp `seg_val`)

| route | handler | notes |
|---|---|---|
| `GET /api/seg/validate/<reciter>` | `seg_validate` | full-reciter validate. Cached via `cache.get/set_seg_validate_cache`; invalidated on save (`invalidate_seg_caches`). `orjson_cached_response`. **No per-chapter param** (dropped — accordions are global). The cached payload is complete; `hidden_pause` / `false_split` / `unmarked_wasl` are redacted per viewer without `segments.view_boundary_review`. |
| `GET /api/seg/stats/<reciter>` | `seg_stats` | `compute_stats`; cached `cache.get/set_seg_stats_cache`. |
| `GET /api/seg/edit-history/<reciter>` | `seg_edit_history` | `load_edit_history`. |

There is no separate `trigger-validation` route; `/validate` is the single classify entry point and the cache is the dedupe layer.

## Client — `inspector/frontend/src/tabs/segments/`

`utils/validation/`:

| File | Role |
|---|---|
| `classified-issues.ts` | Build/normalize per-segment issue records consumed by the UI from the response. |
| `card-lead-seg.ts` | Lead-segment selection for a card. |
| `conf-class.ts` | `getConfClass()` — CSS class from confidence score. |
| `missing-verse-context.ts` | Surrounding-segment lookup for a missing-verse card. |
| `refresh.ts` | Fetch `/api/seg/validate` → `setValidation`. |
| `resolve-issue.ts` | Mark issue resolved (ignore / fix / split-resolved). |
| `split-group.ts` | Group split-resolved issues into chains (consumes `split_group_index`). |
| `stale.ts` | Stale-state predicates — when classification needs re-run. |

`domain/registry.ts` — TS twin of `registry.py` (camelCase fields). Exports same derived category tuples + `filterPersistentIgnores` + `isCategoryHidden` + `ERROR_CAT_LABELS`.

Store: `stores/validation.ts` — `segValidation` writable (`SegValidateResponse | null`), `splitGroupIndex` derived, `accordionViewActive` derived from `valUiOpenCategory`, `setValidation`/`clearValidation`.

Components: `components/validation/{ValidationPanel,ErrorCard,GenericIssueCard,MissingVersesCard,MissingWordsCard,WaslBoundary,AccordionGuideModal}.svelte`. `ValidationPanel` filters stale items by live uid (`filterStaleIssues`) before render and owns accordion order.

## Registry-pair invariant

`registry.py` ↔ `registry.ts` ↔ parent `validators/validate_segments.py` must align. Adding/reordering a category:

1. Add row to `_REGISTRY` in `inspector/services/validation/registry.py` (set `accordion_order` AND mind dict insertion position).
2. Mirror row in `inspector/frontend/src/tabs/segments/domain/registry.ts` (camelCase). `parity.test.ts` fails otherwise.
3. Add detection hook: per-segment → `classify_flags` in `classifier.py`; cross-segment scan → `detail.py` / `_missing.py` / `_structural.py`.
4. Wire the count into `category_counts` + a response array key in `__init__.py::validate_reciter_segments`.
5. Add accordion position in `ValidationPanel.svelte` and a card subcomponent if `card_type` is new.
6. Update parent `validators/validate_segments.py` if it should also fire post-pipeline.

`IssueDefinition` flags govern persistence/suppression: `can_ignore` (Ignore button), `auto_suppress` (edit writes cat into `ignored_categories` for per-segment scope), `persists_ignore` (survives save serialization — `filter_persistent_ignores` strips the rest before write), `owner_only` (hidden from non-owners entirely — see Categories above).

## Persisted classifier fields

Two fields stamped on every `detailed.json` segment, read at validate time to collapse per-validate CPU to a dict lookup.

| field | type | source-of-truth helper | rule |
|---|---|---|---|
| `qalqala_letter` | `str \| None` | `services/segments/qalqala.py::compute_qalqala_letter` | last Arabic letter of `dk_text_for_ref(matched_ref)` if ∈ `QALQALA_LETTERS` (ق ط ب ج د), else `None` |
| `is_boundary_adj` | `bool` | `services/validation/classifier.py::compute_is_boundary_adj` | raw rule, NO suppression: one-word seg (`s_word==e_word`) outside muqattaʼat / single-word-verse / `STANDALONE_REFS` / `STANDALONE_WORDS` allow-lists |

Stamped by (all call the same source-of-truth helper → byte-equivalent across writers):
- **Save flow** — `services/segments/save.py::_stamp_persisted_classifier_fields(seg, single_word_verses)` on every mutated seg. `is_boundary_adj` computed structural-only (`canonical=None`).
- **Extraction pipeline** — `.local/extraction` outputs (passes `canonical` to capture the phonemic side, where applicable).
- **Backfill** — `scripts/backfills/backfill_qalqala_letter.py`, `scripts/backfills/backfill_boundary_adj.py`.

Read path (`classifier.py`):
- qalqala: `classify_flags` reads `seg["qalqala_letter"]` when the key is present; **legacy fall-through** computes `compute_qalqala_letter(seg)` (local import to avoid a load cycle). Same helper → identical value.
- boundary_adj: `_check_boundary_adj` short-circuits on `is_suppressed_for` first, then returns persisted `seg["is_boundary_adj"]` when present, else falls through to `compute_is_boundary_adj`. Persisted value is the raw rule; suppression layers on top at read time.

Text source: `compute_qalqala_letter` and `compute_is_boundary_adj` derive seg text from `dk_text_for_ref(matched_ref)` (Migration #5 dropped per-seg `matched_text`). The `seg.get("matched_text") or dk_text_for_ref(...)` fall-through is the back-compat path for any pre-Migration-5 seg still carrying `matched_text`.

**Phonemizer is out of the validate runtime.** `compute_is_boundary_adj` accepts `canonical` for back-compat but ignores it (retired in Migration #5 with `phonemes_asr`); the rule is structural-only. `validate_reciter_segments` passes `canonical=None`. The only `quranic_phonemizer` consumer left is the offline `scripts/backfills/backfill_boundary_adj.py`; no `canonical_phonemes.pkl` loads during validate; eager-boot init removed from `app.py`.

## Snapshot lifecycle

Save flow (`services/segments/save.py`) routes history op-log snapshots through `classify_snapshot` (→ `classify_segment`), stamping `classified_issues` on each `targets_before` / `targets_after` and `snapshots.{before,after}` dict (via `_history_visible_categories`, with `probe_failed_uids` so v2 flags appear in the delta). `snapshot_classifier.py` is distinct from the live pass only in that it derives positional inputs from the bare snapshot and skips heavy detail rendering — no separate classification logic.

The before/after category delta on each op record drives the per-batch resolved-issue stats in the edit-history view and the validation-summary line in the save-preview surface.

`failed` resolution: once a ref is committed the seg's confidence becomes 1.0 and the empty-`matched_ref` condition no longer holds, so the category auto-drops on the next validate.

## Drift gates

Any change to a coordinate table, a persisted-field writer, compute placement, caching, or parallelism must prove it did not move existing output. Three gates do that, in ascending cost:

1. **Derivation identity** — `tests/services/test_edition_tables.py::test_hafs_tables_are_projection_identity` and `test_surah_info_matches_edition_index`. The per-edition coordinate tables (`muqattaat_words`, `standalone_refs`, `standalone_words`, `single_word_verses`) are *derived* by projecting the Hafs baseline in `inspector/constants.py` through `qua_domain`; for Hafs that projection is the identity, so the test asserts the derived table equals the frozen literal exactly. Stronger than a snapshot: it proves the derivation for all 77,433 words, not one sampled reciter.
2. **Fixture classifier parity** — `classify_segment` over the committed segment fixtures must produce identical `classified_issues` with the edition accessor forced unavailable. This is what keeps the persisted-field writers (save / extraction / backfill / classifier fall-through) in agreement, since they all call the same source-of-truth helpers.
3. **Nightly bucket validation** — `bucket-validate.yml` + `/healthz?deep=1` (`services/storage/bucket_audit.py::sample_validation`) round-trips a real bucket sample through `qua_shared/schemas`, catching a writer that emits a shape the readers reject.

Backfill scripts use **parallel-then-promote**: write to a staging path (`archive/backfill/<slug>/detailed.json`), compare in memory against the current object, then atomically promote to `reciters/<slug>/detailed.json` only on byte-equivalent match for every field the backfill does not intend to change.

> An earlier version of this document described a `bench/` harness (`snapshot.py` / `drift.py` / `measure.py` + `bench/ground_truth/<slug>.json`) as the drift gate. No such tree exists in this repo or on disk — `git ls-files` matches only `scripts/diagnostics/bench_storage.py` — so it could not gate anything. The three gates above replace it.
