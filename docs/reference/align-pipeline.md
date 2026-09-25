# Native align pipeline

One click in **Admin → Requests** takes a delivery from `awaiting_alignment` to
`awaiting_review` on the Spaces that already exist. No Katana, no laptop, no
new engines. Replaces the offline `segments-extraction` runbook for by_surah
deliveries in any supported riwayah, including playlist deliveries whose files
hold several chapters each (combined files, split after aligning).

Two entry points share the same run:

- **Slug row**: a catalogued `awaiting_alignment` delivery with an audio
  manifest. The Align CTA calls `POST /api/admin/reciter/<slug>/align`.
- **Intake row**: a slugless `existing_reciter_new_combo` / `new_reciter`
  submission (`payload.source` = per-chapter links or one playlist URL: YouTube,
  Google Drive folder/file, SoundCloud set, archive.org item, direct URL) is
  planned → minted → aligned from the same Requests tab. See
  [Online intake](#online-intake-plan--mint--align).

Where it lives:

| Piece | Path |
|---|---|
| Service package | `inspector/services/admin/align_pipeline/` — `runs` (start/retry/cancel/status), `runner` (worker threads), `stage_acquire` · `stage_align` · `stage_split` · `stage_sidecars` · `stage_assemble`, `sources` (manifest → source groups + slots), `partition` (pure split logic), `manifest` (writes acquired size/duration/offset + split coverage back to the audio manifest), `adapt` (aligner rows → staged shapes), `aligner_client` (SSE), `staging` (bucket paths), `progress` (in-memory detail + cancel), `params` (knobs + env), `limits` (shared GPU/CPU budget) |
| Intake planner | `inspector/services/admin/intake_plan/` — `enumerate` (+ `drive`), `match` (+ `surah_names.json`), `identity`, `plan`, `mint` |
| Durable row | `align_runs` table — `services/db/migrations/0031_align_runs.sql`, `services/db/repo_align_runs.py` |
| HF jobs | `qua_jobs/acquire_audio.py` (kind `acquire_audio`) and `qua_jobs/split_audio.py` (kind `split_audio`), both shown in the Jobs tab; shared fetch/encode/cut/peaks helpers in `qua_jobs/audio_io.py`; grouping in `qua_shared/audio/sources.py` |
| Build | `inspector/services/segments/promote_build.py` (shared with `scripts/bucket/promote_run.py`) |
| Routes | `inspector/routes/admin/align.py` — `POST /api/admin/reciter/<slug>/align`, `GET …/align/status`, `POST …/align/retry`, `POST …/align/cancel`; `inspector/routes/admin/intake_plan.py` — `GET`/`POST`/`PUT /api/admin/intake/<rid>/plan`, `POST /api/admin/intake/<rid>/align` |
| Capability | `intake.align` (owner + maintainer by default); `intake.align_unlimited` bypasses the shared budget (owner only by default); `intake.ingest` plans + mints an intake (owner only by default; the intake `/align` also needs `intake.align`) |
| Wire | `qua_shared/schemas/wire/align_runs.py` — `AlignRunStatus`, `AlignStartRequest`; `AdminRequestRow.align` overlay; `qua_shared/schemas/wire/intake_plan.py` — `IntakePlan` / `IntakePlanView` / `IntakePlanUpdate` / `IntakeAlignRequest` / `IntakeAlignResponse` |
| FE | `tabs/dashboard/components/admin/AlignProgress.svelte`, the Align block in `RequestsCompartment.svelte`, `tabs/dashboard/components/admin/intake/` (`IntakePlanPanel` + `IntakePlanSummary` / `IntakePlanEntries` / `IntakeIdentityForm` / `IntakeAlignCta`), `lib/api/admin-requests.ts` |
| Aligner side | `qua-aligner-app` — `/api/v1/batches` items by `audio_ref`, `/api/v1/extraction/sidecars`; both gated by `X-Extraction-Secret` |

## Stages

The audio manifest is the only input. Chapters are grouped by physical file
(`qua_shared/audio/sources.py::groups_from_manifest`): a chapter's source is its
manifest `source_url` when set, else its `url`. A group of one is a **single**
chapter; a group of several is a **combined** file, given a **source slot**
`901 + i` (`SLOT_BASE = 901`, `MAX_SLOT = 999`). Slots sit above any chapter
number, so `audio/<slot>.mp3` never collides and the aligner's bucket-ref pattern
admits it unchanged. The grouping is frozen per run in
`staging/<slug>/<run>/groups.json`, because split rewrites the manifest and could
otherwise renumber slots on resume.

```
acquire   CPU HF Job qua_jobs/acquire_audio.py (sources on a thread pool, one per vCPU)
          single   → reciters/<slug>/{audio/<ch>.mp3, peaks/<ch>.json.gz}
          combined → reciters/<slug>/audio/<slot>.mp3 (encoded once, no peaks)
          + staging/<slug>/<run>/acquire.json; the runner then writes the real
          size / duration back into the manifest (manifest.record_acquired)
align     per-file loop, aligner Space POST /api/v1/batches (alignment-only) +
          items/<n>/audio/stream with audio_ref=hf://buckets/<repo>/reciters/<slug>/audio/<n>.mp3
          → staging/<slug>/<run>/chapters/<ch>.json (single) or sources/<slot>.json (combined)
  split   still inside the `align` DB stage (stage_split): partition each combined
          file's rows by surah → split_plan.json → CPU HF Job qua_jobs/split_audio.py
          (kind split_audio) cuts audio/<slot>.mp3 → audio/<ch>.mp3 + peaks and deletes
          the slot once every cut succeeded → rows rebased onto each cut, staged as
          chapters/<ch>.json; outcome → split_outcome.json + manifest (drops, adoptions,
          offsets). From here a combined chapter is indistinguishable from a single one.
sidecars  one reciter-wide POST /api/v1/extraction/sidecars (SSE) — the aligner runs
          qua_timing_batch low_confidence against the phoneme MFA Space and builds
          auto_split from interactive word timings returned by the align stage
          → staging/<slug>/<run>/sidecars/{low_confidence_v2,auto_split_v1}.json
          Hafs only for the probe: a non-Hafs delivery gets `low_confidence_v2: null`
          (D12, editions.md) and nothing is staged for it; auto_split_v1 is always staged
assemble  in-process: adapt → promote_build.build_artifacts (peaks from the acquired blobs,
          no ffmpeg) → reciters/<slug>/{detailed,segments,pipeline_meta,chapter_sources,
          coverage_report,edit_history*.jsonl,low_confidence_v2,auto_split_v1}.json
          chapter_sources carries each chapter's offset inside its source file;
          coverage_report lists split drops as missing, mislabelled files as unresolved
          low_confidence_v2 is required staged on Hafs, absent by contract off Hafs
          detailed.json written last; staging deleted
auto_detect  sees detailed.json → reciter.alignment_completed → awaiting_review
```

Lane choice: the Align CTA carries a **GPU / CPU** toggle (`AlignStartRequest.device`,
persisted in the run's `params_json` and echoed as `AlignRunStatus.device`). `GPU` is
the default and still falls back to CPU on `gpu_quota_exhausted`; `CPU` starts on the
Space's CPU worker pool and never touches the ZeroGPU quota. The live lane (after a
fallback) rides in `detail["device"]`, which the progress card renders.

## Shared budget (`limits.py`)

Every aligner call (and the acquire HF Job) rides the Inspector's own HF token,
so every GPU run spends the **owner's** ZeroGPU quota whoever clicks Align. All
maintainers therefore share one budget, checked under the write lock in
`runs.start` / `runs.retry` and refused with **429**:

- **GPU** — `GPU_RUNS_PER_WINDOW = 2` starts per rolling 24 h (a slot frees 24 h
  after the oldest counted start). Retrying a GPU run resumes the same start and
  is free.
- **CPU** — unlimited per day, but `CPU_CONCURRENT = 1` counted CPU run
  pending/running at once (a failed run holds no slot; its retry needs one).
- **Override** — holders of `intake.align_unlimited` (owner only by default; the
  owner can grant it to maintainers in the Permissions tab) bypass both. Their
  runs are stamped `quota_exempt` in `params_json` and never count. An owner
  bearer is always exempt.

Counting reads `params_json.device` (the STARTING lane): a GPU run that fell back
to CPU still counts as GPU, not as the CPU slot. The Requests payload carries
`align_quota` (`AlignQuota`: used/limit/next-free per lane + the caller's
`exempt`) on the open facet for `intake.align` holders; the Align CTA shows the
counters and disables a spent lane.

Parameters (`params.py`): model `Large` (the same `hetchyy/r7` checkpoint as the
Katana extraction), `pad_left_ms=100`, `pad_right_ms=100`, `min_silence_floor_ms=50`,
matcher/thresholds = whatever the Space runs, `include_merge_groups=true`,
`include_auto_split_timings=true`, `discard_session=true`, no full word-timestamp
pass, no aligner-side split. Only cross-verse and repetition rows are timed. Those interactive
word timings are returned with the alignment result and reused by Auto Split; times
are segment-relative while segment bounds remain relative to the persisted mp3 (no trim).
For each section boundary, the cursor is the midpoint between the preceding
section's last word end and the following section's first word start, then shifted
onto the chapter timeline by the segment start.

## Split (combined files + mislabel guard)

`partition.py` is pure. A combined file's rows are partitioned by the surah of
`ref_from`. Special rows (Isti'adha / Basmala) attach **forward** to the surah
they introduce; unmatched rows attach to the surah before them. Each chapter's
window runs from its first row's start to its last row's end, padded by
`TRIM_PAD_MS = 300`, clamped to the file, and never overlapping a neighbour (a
collision is cut at the silence midpoint). Coverage is tolerant, not fatal:

- a planned chapter the file does not hold is **dropped** from the delivery;
- an unplanned surah the file does hold is **adopted**;
- a single-chapter file whose matched speech is at least `MISMATCH_SHARE = 0.6`
  another surah is **mismatched** (mislabelled in the plan, as in the
  `mohammed_burhaji_yt` mis-index) and dropped;
- when the aligner misses a planned chapter inside a combined file (a short
  surah right before the next, e.g. al-Ikhlāṣ + al-Falaq), its audio usually
  sits unmatched inside the neighbour's cut. The neighbour is kept — its
  unmatched segment fails validation until a reviewer fixes it — and flagged
  **suspect** (≥ `SUSPECT_MIN_MS` = 3 s unmatched) in `unresolved_files`.

The aligner Space's `bucket_fetch` drops its `HfFileSystem` listing cache before
every fetch: split writes new chapter mp3s into an `audio/` folder the Space has
already listed, and a stale listing reads as `audio_fetch_failed` in sidecars.

Every decision lands in `split_outcome.json`, the manifest, and
`coverage_report.json`. Split is idempotent: an existing outcome skips it, and
`split_audio.py` re-measures already-cut chapters instead of re-cutting them.

## Concurrency

Both long stages fan out, because neither is CPU-bound end to end:

- **acquire** runs one source per vCPU (`ACQUIRE_WORKERS` overrides, 8 max); the split job
  likewise cuts one chapter per vCPU (`SPLIT_WORKERS`). Every per-file step releases the GIL — the fetch waits on a socket, ffprobe/ffmpeg/peaks
  are subprocesses — so the pool overlaps CDN latency with encode work instead of
  leaving a `cpu-upgrade` flavor idle on one serial chain.
- **align** keeps a rolling pool of 16 file HTTP streams (chapters or source slots). When one finishes,
  the next chapter starts. This overlaps bucket reads and request setup without
  sending all 114 chapters into Hugging Face's ZeroGPU scheduler at once. The
  aligner admits at most 16 GPU launches globally and one CPU job by default,
  rotating newly free slots between caller identities; single requests and batch
  items are peers. `INSPECTOR_ALIGN_CONCURRENCY` overrides only this transport
  pool for debugging. The stage widens urllib3's connection pool to match.

## Run lifecycle

- **Single-flight per slug**: a `pending | running | failed` row blocks a second
  start (partial unique index). `failed` waits for **Retry** (attempt+1, same run,
  resumes from the failed stage) or **Cancel** (row → `canceled`, staging kept;
  the in-flight acquire or split HF job is cancelled too).
- **DB writes only on stage transitions** (every `durable_transaction` pushes
  the whole DB to the bucket). Per-chapter progress = the staged files +
  `progress` (in-memory); `AlignRunStatus.detail` carries the live chapter /
  aligner stage / HF job status / sidecar beam.
- **Resume**: `app.py` calls `runner.resume_active()` at boot under
  `INSPECTOR_ALIGN_PIPELINE=1`; every stage skips what is already staged
  (acquire skips persisted chapters and slots, align skips staged chapters and
  sources, split skips once `split_outcome.json` exists, sidecars skip when both
  docs exist).
- **Aligner resilience**: transport errors retry the chapter (3×, 30 s);
  `batch_not_found` recreates the batch (the Space keys batches by caller IP
  hash, in memory); `gpu_quota_exhausted` flips the rest of the run to the CPU
  lane; the Space writes `: keepalive` SSE comments every 15 s so long silent
  stages survive the proxy. Sidecars 409 (one run at a time on the Space) waits
  and retries for up to 6 h.
- **Assemble guard**: state ∈ {catalogued, awaiting_alignment} and no
  `detailed.json` — a delivery that got content any other way is never
  overwritten.

## What `adapt` reproduces from the Katana post-process

The aligner's public rows carry seconds, `ref_from`/`ref_to`, `kind`/`special_type`
and (when asked) `merge_group_id`/`merge_members`. `adapt.adapt_chapter` mirrors
`qua_sdk.pipelines.align_postprocess` exactly:

1. every auto-merged row → one `waqf_sakt` event (both halves at pre-strip
   indices `k`, `k+1`; merged row at `k`, confidence 1.0);
2. every `kind == "special"` row → one `delete_segment` event at its pre-strip
   index (`Isti'adha+Basmala` audited as `Basmala`); the row is dropped;
3. the surviving waqf row gets `final_index = k − removed_before(k)`.

Seconds → integer ms (`round(s*1000)`), confidence → 2 dp, `ref_from == ref_to`
→ single ref else `a-b`, unmatched rows keep `matched_ref=""`. The resulting
`ChapterCandidate` + events feed both the sidecars call and `promote_build`,
so the sidecars index exactly the rows that get published.

## Scope and what is refused at start

- by_surah deliveries with an audio manifest (a unique `url` per chapter,
  optionally a shared `source_url` marking a combined file). by_ayah and a
  manifest with no chapters are refused (`400`).
- Any supported riwayah. The aligner is asked for the delivery's edition and
  returns projected rows; `adapt` re-derives each row's Hafs `source_ref` +
  `projection_support` through `services/segments/projection_stamp.py` (the
  same `qua_domain` reverse projection the save path uses), so the aligner
  ships nothing extra.
- Missing `INSPECTOR_EXTRACTION_SECRET` / HF token → `503`.

## Online intake (plan → mint → align)

This replaces the offline `ingest_intake.py` driver, the LLM-reviewed playlist
chapter map (`playlist_map`), and the bearer `POST /api/admin/intake/<rid>/ingest`
route, all removed. The plan lives on the request row as `payload.plan`.

1. **Build** (`POST …/intake/<rid>/plan`). `plan.build` stamps
   `status="enumerating"` and a daemon thread enumerates the source
   (`enumerate.py`):
   - `links` submissions keep their chapters; links sharing one URL become one
     combined entry.
   - Google Drive folders go through `drive.py`. It scrapes the browser API key
     embedded in the folder page (`INSPECTOR_GOOGLE_API_KEY`, when set, is tried
     first) and calls Drive v3 `files.list`, so there is no 50-file cap. It walks
     one level of subfolders and skips non-audio files.
   - Everything else goes through yt-dlp flat extraction. A listing shorter than
     yt-dlp's reported count is refused (truncation guard). YouTube oEmbed is the
     title fallback for a single video the bot check refuses.

   `match.py` then maps each title to chapter(s): anchored on a surah keyword, the
   name beats any number, juz' ranges and two-surah titles are understood, and
   each entry gets a confidence of `exact` / `high` / `low` / `manual` / `none`.
   `identity.py` proposes the channel (catalog `host_patterns`), the source (the
   existing generic source per host, else a new `<uploader>_youtube` source) and
   the slug per `catalog.md` §3 (`_v2`… on collision). Status ends `ready` or
   `failed`; an `enumerating` plan older than 15 min reads `failed`.
2. **Review** (`GET` / `PUT …/plan`). The owner edits entries (edited ones become
   `manual`) and the identity. The view carries a live check. Errors: chapters
   outside 1–114, non-consecutive chapters in one file, nothing mapped, identity
   problems (slug taken or malformed, unknown reciter, missing channel/source), and
   a YouTube source without `INSPECTOR_YTDLP_COOKIES`. Warnings: missing chapters
   (partial delivery), low-confidence matches, skipped entries, very long files.
3. **Align** (`POST …/intake/<rid>/align`). `mint.mint_and_align` builds the
   `intake.ingest()` body (delivery, reciter, vocab additions, audio manifest),
   flips the request to `accepted`, then calls `align_runs.start`. A combined
   entry's chapters each get a unique bucket chapter `url`, with the original file
   kept as `source_url`. The mint always lands first; a start the pipeline refuses
   (budget, config) is reported, and the new slug row's Align button retries it.

## Env (see `config-deploy.md`)

`INSPECTOR_ALIGN_PIPELINE=1` (enable + resume workers), `INSPECTOR_ALIGNER_URL`
(default the dev aligner Space), `INSPECTOR_EXTRACTION_SECRET` (must equal the
aligner Space's `EXTRACTION_SECRET`), `INSPECTOR_ALIGN_KEEP_STAGING=1` (debug),
`INSPECTOR_ACQUIRE_JOB_FLAVOR` / `INSPECTOR_ACQUIRE_JOB_TIMEOUT`
(`cpu-upgrade` / `6h`). Bearer for the aligner = the Inspector's own HF token.
The acquire job runs in the stock `INSPECTOR_JOB_IMAGE` (`python:3.11-slim`) like
every other kind — `services/admin/jobs/base.py::job_command` apt-installs ffmpeg
and pip-installs the kind's deps at launch; there is no prebuilt image Space.
`split_audio` reuses the same launcher, flavor and timeout.

Source fetching (`qua_jobs/audio_io.py`): Drive files download through
`drive.usercontent.google.com` (`confirm=t`), direct media URLs by plain HTTP,
everything else through yt-dlp. YouTube **listing** works from Hugging Face, but
**downloads** are refused ("Sign in to confirm you're not a bot") without a
signed-in session. A run with any yt-dlp source installs `yt-dlp[default] deno`
in the job and passes the Space secrets `INSPECTOR_YTDLP_COOKIES` (a Netscape
cookies.txt export) and `INSPECTOR_YTDLP_PROXY` (optional) as the job secrets
`YTDLP_COOKIES` / `YTDLP_PROXY`. `INSPECTOR_GOOGLE_API_KEY` (optional) overrides
the scraped Drive listing key. The app itself needs `yt-dlp` (in
`inspector/requirements.txt`) for listing only.

## Not built yet

by_ayah deliveries, Katana runbook removal, prod rollout
(`INSPECTOR_ALIGN_PIPELINE` stays unset on prod).
