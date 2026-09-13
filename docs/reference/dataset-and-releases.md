# Dataset and releases

The bucket per-reciter folder (`reciters/<slug>/`) is canonical. Two public delivery formats
are adapter-projections of that canonical state: a **GitHub release** (versioned, offline-first)
and an **HF dataset** (parquet, ML-first). A third interactive format (HTTP API) is deferred.

Audio (peaks, proxy, VBR/Xing) lives in the `inspector-audio` skill. Catalog model:
[catalog.md](catalog.md). Lifecycle: [state-machine.md](state-machine.md). TS-tab read path:
[timestamps-job.md](timestamps-job.md). DB substrate + migrations:
[database.md](database.md), [data-migrations.md](data-migrations.md). Firing these
release actions (cut / publish / regen) on a schedule or rule instead of by hand:
[automation.md](automation.md). Watching those jobs (cut / publish / batch / regen),
running + historical, in one place: the **Jobs** tab — [admin-dashboard.md](admin-dashboard.md).

> **As-built note.** Releases ship as **one global GitHub release per version** containing every
> currently-eligible reciter as a `<slug>.zip` asset — NOT a per-reciter release tag. The release
> ledger is three tables (migration 0014), not one. This doc describes what the code does today.

## The three artifacts

| Artifact | Lives at | Who reads it | Why it exists |
|---|---|---|---|
| **Bucket canonical** | `reciters/<slug>/` | Internal (TS tab, the cut/publish jobs, future API) | Zero loss, full reproducibility, single source of truth |
| **GH release** | `gh:releases/v{X.Y.Z}` → `<slug>.zip` assets | Mobile apps, offline kiosks, archives | One version-pinned, fully-offline snapshot of all reciters |
| **HF dataset** | `hetchyy/quranic-universal-ayahs` | ML researchers, training, analysis | Parquet-native, queryable, embedded audio |

Every adapter starts from the same bucket inputs. A native v13 chapter stores every recorded
occasion as connected readings and ordered `parts` (see [shards.md](shards.md) and
[timestamps-job.md](timestamps-job.md)). V3 GitHub tiers use an occurrence-capable ordered-row
envelope but initially emit the single canonical take per verse; HF remains one row per canonical
ayah. Both are pure projections of the same native readings, so the TS-tab read path and the
release/dataset adapters cannot drift at the identity layer. The shared loader runs the strict v13
identity-closure audit before projection; malformed or mixed-version bucket shards block both
adapters. GitHub schema 2 projects producer animation ownership onto exact DigitalKhatt V2; HF
keeps the exact text and word alignment but omits rendering-specific letter paint data.

## Release ledger (SQLite — migration `0014_releases.sql`)

Workflow state (`WIP → REVIEW → READY → released`, see [state-machine.md](state-machine.md)) is
separate from publish status. Publish status lives in three tables, all written by the Inspector
(single writer) inside a `durable_transaction`; the HF Jobs only ever READ the bucket DB.

| Table | Grain | Purpose |
|---|---|---|
| `per_recitation_releases` | one current row per `(track, slug)`, `track ∈ {ts, hf}` | TS-gen + HF-dataset push history per reciter. Partial-unique on `(track, slug) WHERE superseded_at IS NULL`. `stale_since` + `stale_reason` track HF-row staleness. The `ts` row also carries regen provenance — `affected_chapters` (JSON list the regen folded in) + `prior_ts_version` (the superseded gen) — recorded at completion from the edits since the prior gen (`ts_staleness`); the inputs a future granular per-reciter changelog reads. **`hf` ⇒ `ts` invariant:** an HF publish for a reciter with no current `ts` row (timestamps ingested offline, never through an in-app TS job) auto-registers one (`version='offline-ingest'`) in `hf_publish.complete`, so an offline-ingested reciter isn't orphaned from staleness / GH-cut eligibility. Repair pre-existing gaps with `inspector-admin/admin_backfill_ts_release.py` (inserts a ts row at the reciter's publish-time). |
| `gh_releases` | one row per **cut** | Global GH release snapshot: `version`, `produced_at`, `external_uri`, `validation_summary`, `superseded_at`. |
| `gh_release_recitations` | N rows per `gh_releases` | Frozen membership: `slug`, `catalog_snapshot` (JSON), `zip_sha256`, `zip_bytes`, `coverage_ayahs`, `content_hash`, `ts_version`, `change_kind`, `stale_since`, `stale_reason`. Immutable post-cut (except the stale stamps). |

- **Supersede, never delete.** A new cut supersedes the prior `gh_releases` current row; a new
  TS/HF row supersedes the prior `(track, slug)` row. History is retained for audit.
- **`stale_since` + `stale_reason`.** `repo_releases.stamp_stale(slug, at, reason)` stamps that
  slug's current `hf` row AND its membership in the most-recent `gh_releases` stale, tagging *why*
  (`StaleReason`, severity-ordered — the more severe reason wins when both apply; `stale_since` keeps
  the *first* stale time). Two triggers:
  - **`ts_regen`** — TS regenerated (`timestamps_jobs.complete_timestamps_job` /
    `_regenerate_timestamps_on_released`). Needs a full HF republish / GH cut.
  - **`catalog_edit`** — a public-projection catalog field changed (`services/state/catalog.py::edit_delivery_fields`
    / `edit_reciter_fields`, gated on `PUBLIC_DELIVERY_FIELDS` / `PUBLIC_RECITER_FIELDS`; reciter edits fan
    out to every delivery of the reciter). The complete delivery metadata projection is covered, including
    provenance/source, channel, encoding, coverage, and duration fields. Cheap to fix on HF (catalog refresh
    below); GH reflects it on the next cut. Inspector's catalog snapshot and viewer-specific projections
    update immediately from SQLite after the durable edit.

  A third reason — **`segments_edited`** — lives on the **`ts`** track and is **computed, never
  stamped** (`services/segments/ts_staleness.py::ts_stale_info`, called from the status route). The
  generated timestamps go stale the moment a timestamp-affecting segment edit
  (`qua_shared/segment_edit_ops.py::TS_AFFECTING_OP_TYPES` = trim/split/merge/delete + edit_reference
  + auto_fix_missing_word — annotations like confirm_reference/ignore_issue/flag_segment/set_is_wasl
  are excluded) is saved after the current `ts` row's `produced_at`. Computed (vs stamped) because the
  segments save path is SQLite-free by design and the signal must auto-clear when a regeneration moves
  `produced_at` past every edit. The remediation is a slug-scoped **regenerate timestamps**
  (`reviews.generate_timestamps`), which then cascades to the existing `ts_regen` HF/GH staleness. The
  Releases tab surfaces it as the `behind_edits` ("Behind edits") section; the Segments history
  panel surfaces the same edits as the **tier filter** (see [`segments-editor.md`](segments-editor.md)).

  The reason→remediation mapping is single-sourced in
  [`qua_shared/release_staleness.py`](../../qua_shared/release_staleness.py) (`StaleReason`,
  `SuggestedAction`, `_SUGGESTIONS` keyed by `(reason, track)`). The status route resolves + serializes
  the `SuggestedAction` per stale row so the FE renders the remediation without reimplementing it —
  adding a new staleness measure is a `StaleReason` value + one `_SUGGESTIONS` row. Neither trigger
  auto-unpublishes. `refresh_catalog.complete` calls `repo_releases.clear_catalog_stale_hf()` to clear
  *only* `catalog_edit` HF staleness after a refresh (`ts_regen` rows stay stale — they still need a
  republish; severity ordering guarantees a row that also needs a republish never reads as
  catalog-only).
- **Idempotency.** `complete()` handlers no-op when a row already exists for the version
  (`gh_release_by_version` / `release_by_version`), so a webhook retried after a later cut can't
  replay.

Repository: [services/db/repo_releases.py](../../inspector/services/db/repo_releases.py).

## Eligibility gate

A reciter is GH-release-eligible iff its channel has `gh_release_eligible = 1`, it is not the
`everyayah` owner-only channel, **and** it has a current `per_recitation_releases(track='ts')` row.
This is a pure DB query (no `git ls-files`). EveryAyah is excluded as a second, explicit public
release boundary even if its channel vocabulary row remains GH-eligible for historical compatibility.
The same predicate drives the Releases-tab buckets and the cut job's member discovery.

| Action | Eligibility |
|---|---|
| TS gen | `state == READY` + segments saved + `pipeline_meta.json` present |
| HF publish (per-reciter) | current `ts` row (`release.publish_hf` cap) |
| GH cut (global) | ≥1 eligible reciter (`release.cut_gh` cap, owner-only by default) |

## Cut flow (the HF Job)

`POST /api/admin/cut-release` launches an HF Job running
[qua_jobs/cut_release.py](../../qua_jobs/cut_release.py). The job:

1. Reads the bucket DB read-only → eligible reciters + the prior release's membership.
2. Per reciter: reads every compact native v13 `timestamps/<ch>.json.br` shard and projects the canonical
   verse map (`_load_canonical_verses` → `select_complete_verses`, the earliest completing occasion),
   then drops incomplete verses via `select_complete_verses` (missing a reference word index)
   → builds the three
   tier files (verse/word/letter, top-down), `catalog.json`, a per-recitation `manifest.json`; packs
   a deterministic `<slug>.zip`; computes `content_hash = SHA-256(letter_tier.gz || catalog.json)`.
   `catalog.json` is built from `catalog/audio_manifest/<slug>.json` — each chapter's native
   `source_url` (falling back to `url`) plus `source_offset_ms` → `chapter_offsets_ms` (see
   [Audio policy](#audio-policy-as-built)); legacy sidecar metadata is tolerated here because the GH
   adapter only needs consumer audio URLs. A GH-eligible recitation with no usable audio URLs is fatal.
3. Classifies each reciter `added` / `refresh` / `unchanged` (vs prior `content_hash`) and computes
   the version (below).
4. Builds the dataset-level `manifest.json`, `catalog.json`, and `CHANGELOG.md` (the release body — see
   [Changelog](#changelog-the-release-body)).
5. Creates ONE GH release tag via the GitHub REST API and uploads every asset.
6. POSTs the completion webhook → `/api/webhooks/release-cut-complete` →
   [`services.admin.jobs.cut_release.complete()`](../../inspector/services/admin/jobs/cut_release.py)
   inserts the `gh_releases` row + N `gh_release_recitations` rows, supersedes the prior cut, and
   fires `released({track:'gh', version, recitation_count})`.

Global single-flight: only one cut in flight at a time; a publish landing mid-cut is rejected.

### Versioning

Auto-bump from the prior version (an operator `RELEASE_VERSION` override must still use release
format major 3 or newer):

| Situation | Result |
|---|---|
| First schema-2 release, or prior release below v3 | `v3.0.0` |
| Any `added` reciter after v3 | MINOR bump (`v3.N+1.0`) |
| `refresh` and/or changed static refs only | PATCH bump (`v3.N.P+1`) |
| Nothing changed | error — set `RELEASE_VERSION` to force-cut |
| Later breaking release-format change | raise `RELEASE_FORMAT_MAJOR`; the cut starts at that major |

## GH release structure

One release per version; each reciter is a separate `<slug>.zip` asset so the GitHub page shows
per-file sizes and consumers download only what they need.

```
gh:releases/v{X.Y.Z}/
├── <slug>.zip            # one per reciter (see below)
├── manifest.json         # dataset-level: version, per-reciter sha256/bytes/coverage/change_kind, static_refs
├── CHANGELOG.md          # the rendered release body
├── catalog.json          # dataset-level: reciter rows with audio URLs paired to timestamps
├── shard.py              # consumer helper (per-surah file splitter)
├── check_updates.py      # consumer helper (per-reciter update check)
├── download_audio.py     # consumer helper (fetch source audio @ aligned CBR encode)
├── surah_info.json       # static reference
├── digital_khatt_v2_script.json # exact Hafs presentation text
├── DigitalKhattV2.otf    # matching OFL-1.1 font
└── LICENSE               # CC-BY-4.0
```

Each `<slug>.zip` contains:

```
verse_timestamps.json.gz   # tier 1: ordered occurrence rows + following silence
word_timestamps.json.gz    # tier 2: verse-row prefix + words
letter_timestamps.json.gz  # tier 3: word-row prefix + exact DK text + paint tokens
catalog.json               # this reciter's catalog projection (carries audio chapter_urls)
```

The zip carries no per-reciter `manifest.json`: zip integrity is the release-level `manifest.json`'s
per-zip `sha256`, and the in-zip `catalog.json` already self-identifies the reciter (`slug`). Only
the release-level `manifest.json` exists.

Each tier self-contains the level below; all times are relative to the matching source audio in
`catalog.json`. Rows are ordered occurrences and exactly one row per verse ref is marked canonical.
V3 initially emits only that canonical row; later repeated and partial takes can be appended without
changing the format. The three `.json.gz` layers keep storage, startup speed, and network transfer
cheap: download verse, word, or letter detail independently.
Use `shard.py` when an app prefers local per-surah files. There is no per-reciter `README.md`.

**Letter-tier animation tokens.** A letter occurrence row is exactly its corresponding word row
plus `text` and `tokens`. Public tokens are producer-attributed timed paint units, not a character
alphabet. Each occurrence carries its exact DigitalKhatt V2 text. A token contains its
zero-based `word_occurrence`, resolved start/end, `owns_sound`, and one or more half-open Unicode
scalar paint ranges into that text. The occurrence pointer disambiguates repeated word indices;
the ownership boolean supports silent-omit highlighting. Ordinary combining marks travel with
their attributed unit, while independently sounded marks can have their own range. Display-only
stop signs remain visible in the text but have no timed paint owner. The occurrence envelope is
independent of this token representation: adding noncanonical takes does not change token shape.

There is no letter-vocabulary asset. Consumers do not align QPC tokens against another Quran text,
and the jobs do not strip Unicode marks into a closed alphabet. Both adapters use
`qua_shared.digital_khatt` and `qua_shared.verse_layout`; the release also ships the exact script
JSON and matching font identified by the hash in every tier's `_meta`.

### Audio policy (as-built)

Audio is **not** bundled. `catalog.json` carries `audio.chapter_urls` (the upstream/source URLs the
recitation actually serves) plus codec/bitrate/sample-rate/channels meta; consumers fetch audio
from those URLs and read timestamps relative to those exact files. The fuller
`redistribution_policy` registry (cdn_link / embedded_consent /
internal_only) described in early design is **not implemented** — revisit if embedded-audio
releases become a real requirement.

**Native source + offsets (combined non-CDN sources).** `chapter_urls[ch]` is the **native** source
URL — for a playlist intake (one YouTube/Drive file serving several chapters) the audio manifest
stores a per-chapter *bucket* path in `chapters[ch].url` and the real source in `source_url`; the cut
surfaces `source_url` (`cut_release._audio_sources_from_manifest`, mirroring `publish_hf`'s per-row
resolution) so the release never leaks an internal bucket URL. When one source serves several chapters
(or a single file has a trimmed lead-in), `audio.chapter_offsets_ms[ch]` carries that chapter's start
offset inside its source — the same value the HF dataset persists as `source_offset_ms`. Map a release
tier-file timestamp into the source file with `source_ms = chapter_offsets_ms.get(ch, 0) + tier_ms`.
`chapter_offsets_ms` is **omitted from the JSON when empty** (model_serializer on `ReleaseCatalogAudio`),
so CDN by-surah catalogs stay byte-stable and their `content_hash` doesn't churn.

**`download_audio.py`** (release asset, `qua_jobs/download_audio.py`) fetches a reciter's source audio
re-encoded to the alignment profile (192 kbps CBR / 44.1 kHz / mono by default; configurable). It reads
`catalog.json` and offers `--format chapters` (one offset-0 file per surah — splits a combined file at
the chapter offsets), `--format ayah` (one offset-0 clip per verse, `NNN_VVV.mp3`, cut at the
`verse_timestamps.json.gz` spans auto-located next to `catalog.json` or passed via `--timestamps`), or
`--format original` (the source files as published + a `download_map.json` of chapter→file+offset). Uses
`yt-dlp` + `ffmpeg` for YouTube/Drive, fails loud with install hints when a tool is missing. Staged like
the other helpers (`base.REQUIRED_ENTRYPOINTS`) and uploaded by the cut.

### `manifest.json` (dataset-level)

```json
{
  "schema_version": 2,
  "release_version": "v3.0.0",
  "created_at": "2026-06-03T10:00:00Z",
  "previous_version": null,
  "recitation_count": 9,
  "static_refs": { "surah_info.json": {"sha256": "...", "bytes": 1234}, "digital_khatt_v2_script.json": {"...": "..."}, "DigitalKhattV2.otf": {"...": "..."} },
  "recitations": {
    "<slug>": { "zip": "<slug>.zip", "zip_url": "...", "sha256": "...", "bytes": 123,
                 "coverage_ayahs": 6236, "content_hash": "...", "change_kind": "added",
                 "ts_version": "..." }
  },
  "license": "CC-BY-4.0"
}
```

## Changelog (the release body)

The release body is rendered from
[`docs/templates/release_body.md`](../templates/release_body.md) by
[`render_changelog`](../../qua_shared/release_changelog.py), called by BOTH the cut job (the body
POSTed to GitHub) and the cut-modal preview endpoint. The template uses fixed `{{ placeholders }}`
only; no template dependency. The text stays human-editable, while the renderer owns the generated
asset table, change tables, examples, and links.

Format (display names only — never slugs):

- Title `# {date}`. GitHub already shows the tag above the body.
- First visible section: `## What to download` asset table.
- Short guide sections for audio/timestamp pairing, timestamp levels, recitations, and
  programmatic use.
- `<details>` **Reciter zip schemas**: verse/word/letter timestamp shapes plus a small example.
- `<details>` **Catalog and manifest schemas**: release-level `manifest.json` and `catalog.json`.
- Inline `**License:** CC-BY-4.0` + repository / HF-dataset links.

Coverage shows exact **ayahs** at cut time; the DB-only preview shows **surahs** (chapter_count)
because exact ayah coverage is only computed during the cut. A **Missing** column reports
coverage gaps vs the full 6236-verse mushaf using
[`qua_shared/coverage.py::missing_coverage`](../../qua_shared/coverage.py) — whole un-covered
surahs (`surahs 1-84`) and within-surah verse gaps (`75:18-40`, `7:116, 41:15`) are split into two
concise run-collapsed strings so even a partial recitation (a Juzʾ ʿAmma reciter) stays short; a
complete reciter shows `—`. The same two strings (`coverage.missing_surahs` / `coverage.missing_verses`,
omitted when empty) are written to each `catalog.json`, so the human table and the JSON can't drift.
A data-gated callout above the recitation tables explains that a missing verse is almost always
upstream (source omits it — commonly the Basmala `1:1` — or recites it incompletely) and is excluded
rather than shipped misaligned. The DB-only preview can't compute exact gaps, so its Missing column is
all `—`. Riwayah/style/channel display names
come from the `riwayahs` / `styles` / `channels` vocab tables — `catalog.json` keeps the slug forms
(stable consumer identifiers), the human-facing changelog never shows them.

### Admin routes ([routes/admin/releases.py](../../inspector/routes/admin/releases.py))

| Route | Purpose |
|---|---|
| `GET /api/admin/releases/status` | Per-reciter TS/HF/GH status grid + summary + in-flight jobs (FE buckets). `reviews.view`. |
| `GET /api/admin/release-preview` | DB-only dry-run: change counts, auto-version, rendered changelog. `release.cut_gh`. |
| `POST /api/admin/cut-release` | Launch the global cut. `release.cut_gh`. Body accepts only `version` and `expected_version_at_preview`; echoes the preview version to 409 stale state. |
| `POST /api/admin/publish-hf/<slug>` | Launch per-reciter HF dataset publish (single). `release.publish_hf`. |
| `POST /api/admin/publish-hf-batch` | Launch ONE job publishing a batch of slugs. Body `{slugs: [...]}`. `release.publish_hf`. |
| `POST /api/admin/refresh-hf-catalog` | Launch the global catalog-only refresh (`mushafs` config + card, no audio re-slice). Reuses `release.publish_hf`. Single-flight against publish / batch / refresh. The cheap remediation for `catalog_edit` HF drift; clears those stamps on completion. |
| `POST /api/admin/release-jobs/<job_id>/cancel` | Cancel any in-flight release job (publish / batch / cut / timestamps). Outer gate `reviews.view`; the cancel itself is re-gated per kind. Also stamps the job record terminal. |

Every kind now writes a uniform `JobRecord` to a single bucket store at launch + every terminal point (`services/admin/jobs/records.py`); the admin **Jobs** tab reads that one store (+ live HF jobs) for a unified running/historical view across all kinds. See [admin-dashboard.md](admin-dashboard.md) § Jobs compartment.

### Batch publish (the `hf_publish_batch` kind)

`publish-hf-batch` launches one HF Job running
[qua_jobs/publish_hf_batch.py](../../qua_jobs/publish_hf_batch.py), which loops
[`publish_hf.publish_slug`](../../qua_jobs/publish_hf.py) over each slug (one slug failing never
aborts the batch) and re-renders the dataset catalog/card **once** at the end. The job:

1. writes a durable batch record to `jobs/_global/hf_publish_batch/<job_id>.json`
   (`{completed_at, members:[{slug, status, version, external_uri, error}]}`), then
2. POSTs `/api/webhooks/hf-publish-batch-complete` →
   [`services.admin.jobs.hf_publish_batch.complete()`](../../inspector/services/admin/jobs/hf_publish_batch.py),
   which calls the single-publish `hf_publish.complete()` per **succeeded** member (idempotent
   `per_recitation_releases(track='hf')` insert + `released` event) and leaves failures in the
   record. The 120 s poll fallback reconciles by reading the same record (no webhook payload).

Global single-flight: one batch at a time; a single publish or a cut is rejected while a batch is in
flight, and vice-versa (labels `task=hf_publish_batch`, `reciter=_batch`).

`GET /api/admin/releases/status` reads the newest batch record and derives **failed** slugs (a
failure clears once a current `hf` release lands at/after the batch). The status payload adds
`recitations[].publish_error` (per-row, → the FE "Failed to publish" bucket) and a top-level
`last_batch {job_id, at, published_count, failed_count}` (→ the dismissable summary banner). In-flight
job entries carry `url` for the FE "Open on HF" link; cancel is the generic route above.

Publishing is **select-only**: publishable rows (Publish to HF / Republish to HF / Failed / Published &
current) carry a checkbox; a sticky action bar publishes the selection as one batch (a single selection
= a batch of one). There are no per-row publish buttons. In-flight rows show a minimal status (badge +
elapsed + Open-on-HF + Cancel); the global cut/batch job surfaces the same in the summary card strip.
The Releases tab is the single operator lifecycle home — sections are organized **by action needed**
(Ready to generate / Behind edits / Republish to HF / Publish to HF / Published & current), generation
and regeneration share one in-row expand, and the summary card shows the "what's next" cut preview.
Full UI map: [`admin-dashboard.md`](admin-dashboard.md).

## HF dataset schema

### Per-verse row

| Column | Type | Notes |
|---|---|---|
| `audio` | `Audio` | Verse clip — bytes embedded |
| `surah` | `int32` | 1–114 |
| `ayah` | `int32` | Within surah |
| `duration_ms` | `int32` | first-word-start → last-word-end |
| `text_uthmani` | `string` | Exact DigitalKhatt V2 text for what was recited, including repetitions |
| `segments` | `[[int,int,int,int]]` | `[word_from, word_to, start_ms, end_ms]` |
| `word_timestamps` | `[[int,int,int]]` | `[word_idx, start_ms, end_ms]` |
| `source_url` | `string` | Chapter/verse audio URL (or `bucket://` when embedded) |
| `source_offset_ms` | `int32` | Offset within `source_url` where the verse starts |

- Audio embedded as bytes (verse-trimmed); `word_idx` is 1-based, may repeat / go backward within a
  verse; splitting `text_uthmani` on spaces equals the `word_timestamps` occurrence count.
- Letter-animation ownership and Unicode paint geometry are intentionally absent from the
  ML-oriented HF table. Consumers needing granular rendering use the matching GitHub release's
  `letter_timestamps.json.gz` tier.
- **Subset (config)** = delivery/mushaf slug (e.g. `khalifa_al_tunaiji_tarteel`), **split** =
  `train`. Parquet still lives under the riwayah folder `hafs_an_asim/<slug>-*`; only the config
  label differs (one config per mushaf — the HF viewer caps a config at 30 splits). Readability
  comes from `name_en` / `name_ar`, not the config key.

### Dataset catalog config

HF config `mushafs`, split `all`, is the dataset catalog projection. It is rebuilt by
[`qua_shared.hf_dataset_catalog`](../../qua_shared/hf_dataset_catalog.py) from the Inspector SQLite
`ReciterCatalog` v2 and pushed by the active admin HF publish job after a recitation split lands.

Grain is one published delivery row. Rows include delivery slug, reciter identity,
readable riwayah/style/channel labels, audio metadata, duration, and HF-publish timestamps
(`published_at` = first publish, `updated_at` = last publish/refresh, from
`per_recitation_releases(track='hf')`) — see `CATALOG_COLUMNS`. Admin lifecycle / publish-ledger
fields, the internal release-gating signal `gh_release_eligible`, the niche `variant_label`, and the
upstream `source` label all stay out of the public dataset. The underlying
`Channel.gh_release_eligible` / `Delivery.variant_label` / `Delivery.source` fields remain in the
catalog — they drive GH release-cut eligibility and admin UI, just not the public projection.

This is separate from GitHub release `catalog.json`: the release artifact pairs timestamp tiers with
source audio URLs for offline consumers, while the HF `mushafs` config is a parquet catalog for
dataset discovery and filtering.

### Dataset card (README) — rendered at release time

The dataset card is not uploaded verbatim. [`docs/templates/hf_dataset_card.md`](../templates/hf_dataset_card.md) is a
**template** with `{{configs}}` (frontmatter) and `{{recitations}}` / `{{riwayat}}` / `{{hours}}`
(header badges) placeholders. On each publish, `_sync_dataset_catalog_and_card` enumerates the actual
hub splits once via `hub_published_splits_by_config` (the just-pushed split is already present;
`mushafs`/`segments`/`timestamps` excluded), then `render_dataset_card` builds the `configs:` block
(one `config_name` per riwayah + `mushafs`/`all` last) and fills the badges from the same
`HfDatasetCatalogStats` (`_stats_from_published_splits`) used for the `mushafs` projection. So
frontmatter, badges, and catalog stats can't drift from the published set. The GitHub repo README
badges are a separate, broader metric maintained by `update-badges.yml` CI and are unaffected.

### Audio: preserve, don't normalize

Embedded bytes are stream-copied from the bucket Xing-injected chapter master — source codec /
bitrate / sample-rate / channels preserved; slice frame-snapped (≤26 ms earlier than first word,
word timestamps re-based). Consumers resample at load; filter by codec/SR/channels via the
`mushafs` config columns. For full-chapter app playback, prefer the GitHub release assets. A verse
with an **interior no-match gap** is the one exception to "single contiguous slice": its clip is the
kept runs stitched gaplessly (the no-match audio excised) — see
[Failed-alignment, no-match & deletes](#failed-alignment-no-match--deletes-at-publish).

## Dedup semantics — what projection loses / preserves

Bucket v13 stores every recorded occasion in native readings and parts. The native publishing
projection reduces each verse to its single canonical take; the Inspector itself keeps all parts.

| Lost in projection | Preserved |
|---|---|
| A non-canonical occasion's word/letter timestamps (an interleaved re-recitation of the same verse) | The whole verse `{1..N}` — the canonical occasion reaches full word coverage by construction |
| A leading false-start prefix + trailing post-completion redundancy (an abandoned/redundant re-do within the canonical occasion) | Every recited segment in the bucket shard, time-ordered, addressable via `source_url` + offsets |

The projection picks a single **occasion** (maximal contiguous run, no foreign verse interleaved)
that completes word coverage `{1..N}`; within-pass backward **lookbacks** (a jump back to a
non-first word) inside that occasion are kept verbatim, so the canonical row is never missing a widx.
Among multiple completing occasions the **earliest** (first recited) wins; a **leading false-start**
(a restart at word 1 whose run re-covers the verse) and **trailing** post-completion segments are
trimmed. Consumers wanting alternate takes read the raw bucket shards (every segment present).

### Failed-alignment, no-match & deletes at publish

Three reviewer/pipeline actions converge to "never reached the bucket shard" — native readings
contain only aligned, accepted, **ref-bearing** occurrences:

- **No-match** = the reviewer enters an empty `matched_ref`. `build_mfa_ref` returns `None`, so the
  segment is skipped before MFA (it leaves **no** `_meta.mfa_failures` entry) — it stays in
  `detailed.json` with its time window but contributes no ref/text/words.
- **Failed alignment** = MFA ran and returned non-`ok` (the only thing recorded in
  `_meta.mfa_failures`).
- **Deleted** = removed from `detailed.json` entirely (editor delete / filtered at intake).

Per-verse, `project_segment_shard` keeps the canonical occasion (falling back to the
widest-coverage occasion if none reaches full coverage `{1..N}`). A **publish-time completeness
gate** — `select_complete_verses` (`qua_shared/timestamps_dedup.py`), run by both adapters against
the reference word counts from `surah_info` — then drops any verse whose canonical take is missing a
reference word index. Completeness is by word **index** (`{1..N_ref}` subset of covered indices), mirroring
the editor's missing-words check; verses with no known reference count are kept (fail-open). Two
distinct drop cases:

- **Whole verse gone** (its only segment, or all its segments, missing) → zero shard segments →
  `project_segment_shard` never emits it.
- **Incomplete verse** (any reference word index never recited — interior *or* trailing, e.g. a
  10-word verse recited only through word 8, or kept words 1-3 + 8-10 with 4-7 missing) → the gate
  drops it.

Either way the verse is absent from **both** the HF dataset (no row, no audio — `build_rows`:
`if not tdata: continue`) and the GH tier files (`if not words: continue`); `coverage_ayahs` /
`verse_count` fall by the dropped count and each job logs the dropped refs. The editor's Segments +
TS tabs **still show** these verses (only the published artifacts gate) so reviewers can complete or
correct them — an intentional editor-vs-published divergence.

**Interior no-match audio** is a separate, orthogonal case: a verse that *does* cover every word
index but carries a no-match segment (empty `matched_ref`, no words) inside its
`[verse_start, verse_end]` window — leftover/duplicated audio between recited words. It survives the
gate. The **HF dataset publish excises it**: `build_rows` computes `keep_runs` (`[clip_start,
clip_end]` minus the no-match segment spans, via `_subtract_spans`), and `_slice_chapter` stitches
the kept runs with `mp3_frames.slice_frames_multi` + `_rebase_row_multi` (gapless re-base; logged
per reciter). A contiguous verse (one run) takes the original single-slice path unchanged. The **GH
release** ships timestamps relative to source URLs (no embedded audio), so it leaves the no-match
span in place. Leading/trailing no-match audio is already outside `[clip_start, clip_end]` (the
window spans first→last kept word), so only **interior** spans stitch.

Post-mark-ready a compound cross-verse ref that was never split is read by the TS engine as a waṣl:
`qua_timing_engine.timestamps.items.expand_cross_verse` splits it per verse (per Hafs verse on a
projected delivery, each piece re-projected), aligns the pieces as one continuous item and stamps
`wasl` on every verse but the last — the same treatment an editor-split + `is_wasl` pair gets. So the
shard still holds one verse per occurrence and there are no cross-verse dedup cases; only a span across
two surahs still blocks the run.

## Validation at publish

Each artifact runs a fail-blocking validation pass before it is produced; the summary is persisted
to the `gh_releases.validation_summary` / `per_recitation_releases.validation_summary` row.
Block on the `HARD_FAIL_KINDS` in [dataset_validation.py](../../qua_shared/dataset_validation.py):
`word_bleed_first`, `word_bleed_last`, `duration_arithmetic`, `intra_segment_gap` — `fatal_violations`
aborts the cut on any of these. `coverage_gap` is reported but **non-fatal**, and incomplete verses
are gated out by `select_complete_verses` *before* validation runs, so `coverage_gap` does not fire
for emitted verses. Warn on: audio-slice checksum drift; TS-tab vs dataset-slice parity probe.

## TS-tab vs dataset-row parity

Word timestamps and verse text are identical — both the TS-tab clip and the dataset row reach the
same `project_segment_shard` canonical take over the same raw bucket segments. Audio bytes are a
byte-substring of the bucket chapter (after per-slice Xing if VBR); the dataset slice may start
≤26 ms before the first word (frame snap), with word timestamps re-based. **No intentional drift** —
if they diverge where they should match, it's a bug.

**One intentional exception:** for a complete verse carrying interior no-match audio, the dataset
clip excises that audio (stitched kept runs), while the in-app TS-tab segment-clip route is unchanged
and still plays the contiguous `[verse_start, verse_end]` window including it. So for those (rare)
verses the dataset audio is shorter than the TS-tab clip by the excised span. Word timestamps still
agree once re-based; only the audio bytes differ. (Verses that are *incomplete* — missing a word
index — never reach this path; the completeness gate drops them from the published artifacts while
the editor still shows them.)

## Event classification

Publish actions fire events into
[activity_classification.py](../../inspector/services/activity/activity_classification.py): the
in-app publish milestone `reciter.published` (TS-gen completion → `released` state) is public on
the rail; `released` / dataset-publish events (HF push + GH cut) are hidden operator infrastructure.

## Multi-riwayah

A delivery's tiers follow its shard profile, not its catalog row — the row could
be edited after alignment, and the release must describe what the timings
actually contain.

| Profile | Tiers emitted | `content_hash` over |
|---|---|---|
| native (Hafs) | verse, word, letter | `letter_timestamps.json.gz` + `catalog.json` |
| word (non-Hafs) | verse, word | `word_timestamps.json.gz` + `catalog.json` |

The letter tier is **absent**, not empty, so a consumer can tell "this recitation
has no letter timings" from "this verse happened to have none". The shallower
tiers are exact prefixes of the deeper ones, so hashing the deepest emitted tier
still detects any timing change.

- `manifest.json` — each recitation carries `tiers` and `riwayah`; the new
  top-level `editions` block carries `edition_id`, `words_sha256`,
  `script_asset_sha256`, `font_family`, `projection_sha256`, `words_asset`, `font_asset`
  per non-Hafs edition.
  A tier file's `script_sha256` is the edition's **`words_sha256`** — the digest of
  the index its text came from; `script_asset_sha256` digests the QPC script asset
  and is a different value.
- Tier `_meta.script` names the edition index id instead of `digital_khatt_v2`.
- Verse gating and boundary validation run against the delivery edition's own
  counting profile (`word_counts_for` / `surah_info_for`) — Warsh and Qalun
  renumber 50 of the 114 surahs, so the Hafs map would gate the wrong verses.
- Each non-Hafs edition ships its script + font as release assets, `<riwayah>_words.json.gz`
  (gzipped `[{ref, text}]`, canonical-JSON digest = `words_sha256`) and `<riwayah>.ttf`, read
  byte-for-byte from the `qua_domain` wheel (`cut_release._edition_assets`). Both are digested
  into `static_refs` (an edition bump is a release bump) and named by `editions[<riwayah>]`'s
  `words_asset` / `font_asset`. The CHANGELOG table carries no tier column; `tiers` lives in
  the manifest.
- `RELEASE_FORMAT_MAJOR` -> v4.0.0 is cut only when the first non-Hafs reciter is
  actually publishable, not with the schema work.

Full detail: [`editions.md`](editions.md).
