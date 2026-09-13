# Accordion Guides

Frontend-bundled guide templates for the validation-accordion help modal (`AccordionGuideModal.svelte`). No backend route or bucket file — all source lives in the FE bundle, keyed by validation category.

## Locations

| Path | Role |
|---|---|
| `inspector/frontend/src/tabs/segments/guides/accordion/<category>.guide.ts` | Markdown-like guide source (default-exports a string), one per validation category. Translations are sibling `<category>.<locale>.guide.ts` files (same source-string shape; only prose/headings translated, `::example`/`::component` directives byte-identical) |
| `inspector/frontend/src/tabs/segments/guides/registry.ts` | `getAccordionGuide(category, locale?)` → source string or `null`; frozen `category → source` map for English + a locale-variant map discovered via `import.meta.glob('./accordion/*.*.guide.ts')`. Passing `locale` returns the translated sibling when one exists, else the English base (English fallback per missing translation). Call it reactively (resolve at open / gate a `$derived` on the current locale). |
| `inspector/frontend/src/tabs/segments/guides/parser.ts` | `parseGuideSource(src)` → `GuideBlock[]`; `guideTitleFromBlocks(blocks, fallback)` |
| `inspector/frontend/src/tabs/segments/guides/examples/index.ts` | `guideExamples` frozen `id → GuideExample`; `getGuideExample(id)` |
| `inspector/frontend/src/tabs/segments/guides/types.ts` | `GuideBlock`, `GuideExample`, `GuidePeakRecord` types |
| `inspector/frontend/src/tabs/segments/guides/editing/` | The **editing guide** — a custom-rendered required guide (not category-bound). `EditingGuideContent.svelte` (runes container) + `MockSegCard`/`AnatomyCard`/`ValActionMock` (inert replicas reusing the real global card classes) + `synth-peaks.ts`. Annotation chrome in `styles/editing-guide.css`. |
| `inspector/frontend/src/tabs/segments/components/validation/AccordionGuideModal.svelte` | Modal renderer (legacy Svelte 4); store-driven host lives in `SegmentsTab.svelte`. `GUIDE_COMPONENTS` maps a `::component` name → Svelte body. |
| `inspector/frontend/src/tabs/segments/components/validation/GuidesGateModal.svelte` | First-edit onboarding gate + browsable guide index + optional collapsed keyboard-shortcuts reference (playback + editing only) (Svelte 5 runes) |
| `inspector/frontend/src/tabs/segments/stores/guides.ts` | `guideModal` store + `openGuideModal()`/`closeGuideModal()` — the lifted modal host |

Current categories (each `accordion/<cat>.guide.ts`, registered in `registry.ts`): `failed`, `missing_verses` (prose-only), `missing_words`, `low_confidence`, `low_confidence_v2` (re-exports `low_confidence`), `boundary_adj`, `repetitions`, `cross_verse`, `qalqala`, `muqattaat` (prose-only), `basmala_amin`. Categories without a guide (e.g. `structural_errors`, `audio_bleeding`) hide the `?` button via `hasAccordionGuide()`.

Plus one **non-category** guide: `general_editing` (first in `REQUIRED_GUIDE_KEYS`) — the illustrated **editing guide**. It teaches the editing UI itself (segment anatomy, every edit op, special ops, saving/history) rather than a validation flag, so it is **never** surfaced as a per-accordion `?` (no validation category ever equals `general_editing`) — only in the `GuidesGateModal` list and the top entry point. Its source is just an `# Editing guide` H1 (supplies the title) + a `::component{name="editing-guide"}` directive; the whole body is hand-authored Svelte under `guides/editing/`.

And one more illustrated non-category guide: `flagging` (**last** in `REQUIRED_GUIDE_KEYS`) — opened from the `?` on the non-registry **Flagged Issues** accordion. Source is an `# Flagging` H1 + a one-line description + `::component{name="flagging-demo"}` (no `::example` blocks); the body is `guides/editing/FlaggingGuideContent.svelte` (reuses `.eg-*` classes + `MockSegCard`'s `'flag'` emphasis mode). See [segments-editor.md → Flagged issues](segments-editor.md#flagged-issues).

## Guide syntax

Line-oriented, parsed by `parser.ts`. Blank line flushes the current paragraph.

| Source | `GuideBlock` | Renders as |
|---|---|---|
| `# Heading` | `{type:'heading', level:1}` | Modal title (first level-1 = `guideTitleFromBlocks`) |
| `## Heading` | `{type:'heading', level:2}` | Section heading |
| `> text` (blockquote) | `{type:'callout', text}` | Tinted **goal callout** (🎯 + "Goal" kicker) — the "By the end…" expectation line. Consecutive `> ` lines join into one; a blank line or any other block ends it. |
| Non-blank lines joined with spaces | `{type:'paragraph', text}` | Body copy |
| `::example{id="..."}` | `{type:'example', id}` | Shared History-card renderer for that example |
| `::component{name="..."}` | `{type:'component', name}` | Custom Svelte body from `AccordionGuideModal`'s `GUIDE_COMPONENTS` map (`editing-guide` → `EditingGuideContent`); unknown name → inline error |
| `::...` not matching a directive | `{type:'missing', message}` | Inline error (unsupported / missing `id`/`name`) |

Convention: every actionable category caps its intro with one `> By the end…` callout stating the expected end-of-review state. Info-only guides (`qalqala`, `muqattaat`) have no goal, so no callout.

Directive regexes: `^::example\{([^}]*)\}$` with `\bid="([^"]+)"`, and `^::component\{([^}]*)\}$` with `\bname="([^"]+)"`. Example:

```md
# Low Confidence
Natural paragraph text.

::example{id="low_conf_reference_correction"}
::example{id="low_conf_trim_timing"}
```

## Example records

`GuideExample` (`types.ts`):

| Field | Type | Notes |
|---|---|---|
| `id` | `string` | Matches the `::example{id}` directive |
| `title` | `string` | Card heading |
| `description?` | `string` | |
| `render` | `'history_op' \| 'edit_chain'` | Single op vs. multi-op chain |
| `chapter` | `number \| null` | |
| `operations` | `EditOp[]` | Real `EditOp` shape (`op_id`, `op_type`, `merge_direction?`, `targets_before`, `targets_after`) |
| `clip_base_ms?` | `number` | The clip's file-start in original ms — playback rebases by this |
| `peaks?` | `GuidePeakRecord[]` | **Canonical history-peaks shape** `{op_id, url, start_ms, end_ms, bps, peaks_b64}` (base64 of n×2 int8s) — the SAME shape `indexHistoryPeaksRecords` consumes. The old inflated `peaks: number[][]` never rendered (the consumer reads `peaks_b64`). |
| `context?` | `GuideContextGroup[]` | `{label, position:'before'\|'after', segments}` — muted read-only neighbour cards (clean occurrence / ±1 context) |

`targets_*` entries carry the slim snapshot shape the renderer reads: `segment_uid`, `time_start`, `time_end`, `matched_ref`, `confidence`, `is_wasl?`, `audio_url` (repointed to the clip). `EditOp` is imported from `lib/types/domain` — examples reuse the production type.

`examples/index.ts` is **auto-generated** by `.local/guide_build/generate.py` (read-only against the prod bucket): per flag it pulls the cross-batch ops from `edit_history.jsonl`, cuts a CBR mp3 clip over HTTP range from the chapter CDN url, computes peaks from the clip (relabelled to original times) → `peaks_b64`, and writes the clip to `frontend/public/guide-audio/<id>.mp3`. Source flags live in `.local/guide_flags/flags.jsonl` (the dev flagging tool).

## Read path

`AccordionGuideModal.svelte` (opened with `category` + `opener` props): `getAccordionGuide(category)` → `parseGuideSource` → render blocks; each `example` block resolves via `getGuideExample(id)`, indexes its peaks (`indexHistoryPeaksRecords`), registers the clip base (`previewCtx.setClipBase(url, clip_base_ms)`), and renders through `HistoryOp` / `EditChainRow` (+ any `context` cards as muted `SegmentRow`s) with a non-persisting preview context (`createPreviewPlaybackContext({persistPeaks:false})`). Audio is the **same-origin clip** `/guide-audio/<id>.mp3`; `preview.ts` rebases the `AudioRange` by `clip_base_ms` so the short clip plays while cards display original timestamps. No bucket/CDN fetch at runtime.

## Read-tracking + edit gate

The guides are **enforced**: each reviewer reads every guide once (globally,
per user) before their first edit unlocks. Three coupled pieces:

**Storage (per-user, SQLite).** `guide_views (view_key, hf_user_id, viewed_at)`
— a write-once junction mirroring the `request_views` per-user view-mark pattern. Opening a
guide `POST`s `/api/guides/viewed {category}`; the route collapses the category
to its stored `view_key` (`low_confidence_v2 → low_confidence`), validates it
against the `GUIDE_VIEW_KEYS` allowlist in `inspector/constants.py`, and
`INSERT OR IGNORE`s. `/api/me` returns the user's `guides_read: string[]`
(empty for anon). → [`database.md`](database.md).

**Registry helpers** (`guides/registry.ts`): `guideViewKey(category)` (alias
collapse), `ALL_GUIDE_KEYS` (every storable key — mirrors `GUIDE_VIEW_KEYS`),
`REQUIRED_GUIDE_KEYS` (the gate set — `ALL_GUIDE_KEYS` minus aliases minus the
guides of **owner-only** validation categories, i.e. rows with
`IssueRegistry[cat].ownerOnly`; currently that drops `qalqala`),
`isGuideRead(guidesRead, category)`, `allGuidesRead(guidesRead)`. The FE-only
`CurrentUser.guides_read` is updated optimistically by `markGuideReadLocally`
after a successful POST.

**Two entry points to the guides, both with the cyan unread signal:**

- **Top button** — a persistent `.seg-guide-entry` button at the top of
  `SegmentsTab.svelte` ("Editing guide & shortcuts") opens `GuidesGateModal` in
  `browse` mode any time via `openGuidesGate('browse')`. Carries `class:unread`
  → cyan border (`var(--accent)`) + dot when signed-in and `!allGuidesRead`.
- **Per-accordion `?`** — `val-guide-btn` (`ValidationPanel.svelte`, first child
  of `<summary>`) opens that category's guide directly via `openGuideModal`.
  `class:unread` → cyan border when signed-in and `!isGuideRead` for that
  category. Both clear reactively when `guides_read` updates.

The old top-of-tab `ShortcutsGuide.svelte` `<details>` was deleted — its
playback + editing rows now live inside `GuidesGateModal` as an optional
collapsed section.

**Gate** — `syncEditingMode(user, task, allGuidesRead)` returns
`{kind:'view', viewReason:'guides_unread'}` before **every** editable kind
(owner, editor, maintainer) when `!allGuidesRead`. The gate applies to **all
editing roles** (contributor/maintainer/owner) — there is **no** dev-mode or
admin exemption (so it's visible and testable locally, and dev is never prod).
`SegmentsTab` threads the live `allGuidesRead($currentUser.guides_read)` into a
reactive `setEditingMode`, so the gate lifts the instant the last guide is read.
The `editGate` action opens `GuidesGateModal` (instead of the usual popover) on
`guides_unread`; **keyboard edit shortcuts** (`E`/`S`/`Enter`) go through the
same check via `gateKeyboardEdit()` in `tabs/segments/utils/keyboard.ts` (they'd
otherwise bypass `editGate`). The modal lists every required guide with
read/unread state and opens each **directly** via the `guideModal` store —
reciter-independent, so it works even when a reciter surfaces no accordion for
that category (the load-bearing edge case). The only consumer of
`editingMode.kind` besides `editGate` is `SegmentsFooter`'s `writeable`, which
hides the save group while gated — desirable. Onboarding is UX only — there is
no save-time enforcement; real authz stays in `require_edit_lock`.

**Adding a guide that re-onboards** existing reviewers: add its key to
`ALL_GUIDE_KEYS` **and** to `GUIDE_VIEW_KEYS` in `inspector/constants.py`
(the only FE↔BE drift point). A guide registered but left out of
`ALL_GUIDE_KEYS` badges-only and never blocks. An **owner-only** category's
guide stays in both lists (an owner can still open it from the accordion `?`
and have the read recorded) but is filtered out of `REQUIRED_GUIDE_KEYS`, so it
never gates a first edit nor appears in the `GuidesGateModal` checklist — see
[`validation.md`](validation.md#categories). No migration/backfill — a
key absent from a user's `guide_views` simply reads as unread.

## Tests

| Path | Covers |
|---|---|
| `guides/__tests__/parser.test.ts` | Block order, paragraph flush, malformed/missing-id directives → `missing` block, title fallback |
| `guides/__tests__/registry.test.ts` | `guideViewKey` alias collapse, `REQUIRED_GUIDE_KEYS`, `isGuideRead`/`allGuidesRead` |
| `components/validation/__tests__/AccordionGuideModal.test.ts` | Store-driven modal open (via `GuideModalHarness`), history examples render without edit controls |
| `lib/stores/__tests__/editing-mode.test.ts` | `guides_unread` gate branch (+ dev/admin exemptions) |
| `tests/routes/test_route_guides.py` (backend) | `POST /api/guides/viewed` auth/allowlist/collapse/idempotency, `/api/me` `guides_read` |

## Where to edit

- New help text for an existing category → edit `accordion/<category>.guide.ts`.
- New category → add `accordion/<new>.guide.ts` and register it in `registry.ts`.
- New referenced example → add to `examples/index.ts`, reference by `id` in the guide source.

## Authoring real examples from past edits

The committed `examples/index.ts` records are currently synthetic (`guide-op-*`,
`https://example.test`). The intended flow is to seed examples from *real* edits
made on past reciters, so the History card the reader sees is authentic.

### Flagging tool (dev-only)

A maintainer browsing the **History** panel can flag any op for guide use. The
flow:

| Surface | Path | Role |
|---|---|---|
| FE button | `tabs/segments/components/history/GuideFlagButton.svelte` | 🚩 button in the **History card header** — rendered by `HistoryBatch.svelte` (op cards) and `EditChainRow.svelte` (split/merge chains), next to Undo. Self-gated on `$currentUser.dev_mode`. Captures category + note + optional example id. NOT in `HistoryOp` (always rendered with `skipLabel=true`, so its label block never shows). |
| FE client | `lib/api/guide-flags.ts` | `flagGuideExample` / `listGuideFlags` / `deleteGuideFlag` |
| Routes | `routes/auth/dev.py` — `POST/GET/DELETE /api/dev/guide-flag(s)` | dev-only; `abort(404)` when dev mode is off (zero prod surface, same pattern as `/api/dev/role`) |
| Store | `services/segments/guide_flags.py` → `config.GUIDE_FLAGS_PATH` | JSONL queue under the gitignored `.local/guide_flags/flags.jsonl` — never the bucket, never git |

A flag record carries enough to resolve everything else: `reciter`, `chapter`,
`batch_id`, `op_ids[]`, `op_type`, `matched_ref`, `category`, `render`, `note`,
`example_id`, plus server-stamped `flag_id` / `flagged_at_utc`. **`op_ids` is the
join key and may span multiple batches** — a flagged chain (split + follow-up
ref-edits/ignores) is captured from the History panel's cross-batch chain, so the
builder looks ops up globally across the reciter's `edit_history.jsonl`, not in
`batch_id` alone.

### Builder — `.local/guide_build/generate.py`

Read-only against the prod bucket (`inspector/.env` already points there). Per flag:
- collect the ops whose `op_id ∈ op_ids` across ALL the reciter's batches → emit
  them all in one `operations[]` so the modal's `buildEditChains` reconstructs the
  chain; snapshots keep authentic absolute times (kept for display).
- compute the clip window over all op snapshots (+ any hand-specified context),
  un-proxy the snapshot `audio_url` to its CDN url, and cut a CBR mp3 (mono,
  44.1 kHz, ~96 kbps) over **HTTP range** (two-stage accurate seek, no full
  download) → `frontend/public/guide-audio/<id>.mp3`. `clip_base_ms` = clip start.
- compute peaks **from the cut clip** at 10 bps and int8-b64 encode (mirrors
  `peaks_slim`'s `×127` quantization) → one covering `peaks_b64` record relabelled
  to original times (guarantees full coverage even when a stored per-op record
  doesn't span the union).
- repoint every snapshot `audio_url` + the peaks `url` to the clip.

**Audio is decoupled from the source reciter on purpose.** Pointing an example at
a live `reciters/<slug>/audio/<ch>.mp3` is fragile: when the bucket lacks that
chapter the preview falls back to a cross-origin CDN url, which played through the
preview port hits the Web-Audio kill-switch ACAO-zeroes problem. The same-origin clip under
`frontend/public/` sidesteps both — durable, instant, no proxy. `preview.ts`
rebases the `AudioRange` by `clip_base_ms` so playback maps into the clip while
the card shows original absolute times.

### Adding an example (recipe)

The whole loop is flag → describe → generate → reference. Steps 3's generator
needs prod-bucket reads, but the **output** (clip + bundled peaks) is fully
static — nothing the deployed Space touches.

1. **Flag the edit.** In dev mode, open the History panel, find the edit, click
   **🚩 guide**, pick the target category + note. It appends a line to
   `.local/guide_flags/flags.jsonl` (`reciter`, `chapter`, `batch_id`,
   `op_ids[]`, `render`, …). (You can also hand-write the line.)

2. **Describe it.** Add one entry to the `CFG` dict in
   `.local/guide_build/generate.py`, keyed by the flag's `flag_id`:

   ```python
   "<flag_id>": dict(
       id="my_example_id",                 # unique; also the clip filename + ::example id
       title="Short label",
       desc="One-line lesson (the why, not the mechanics).",
       # optional — render adjacent context cards (clean occurrence / ±1 neighbour):
       ctx=[dict(label="Clean recitation (kept)", position="before",
                 time_start=..., time_end=..., matched_ref="x:y:a-x:y:b", confidence=1)],
   ),
   ```

3. **Generate.** `python .local/guide_build/generate.py` (prod bucket via
   `inspector/.env`). It resolves the flag's ops across all batches, cuts a CBR
   clip over HTTP range → `frontend/public/guide-audio/<id>.mp3`, computes the
   `peaks_b64`, and rewrites `examples/index.ts`. **Re-runs reuse existing clips**
   (only the new one is fetched); `--force` re-cuts everything.

4. **Reference it.** Add `::example{id="my_example_id"}` to the right
   `accordion/<category>.guide.ts` (under a sub-heading if grouping). For a new
   category, also add `accordion/<new>.guide.ts` + a line in `registry.ts`.

5. **Verify + ship.** `cd inspector/frontend && npm run check` (+ build), then
   commit the new clip, regenerated `index.ts`, and the guide source.

**Invariants:** the `id` must match across the `CFG` entry, the clip filename,
the `examples/index.ts` record, and the `::example` directive. `render` comes
from the flag (`split_segment` → `edit_chain`, else `history_op`). Snapshots keep
original absolute times; only `audio_url`/peaks `url` are repointed to the clip.
