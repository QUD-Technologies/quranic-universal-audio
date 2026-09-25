# Native align pipeline

One click in **Admin → Requests** takes a delivery from `awaiting_alignment` to
`awaiting_review` on the Spaces that already exist. No Katana, no laptop, no
new engines. Replaces the offline `segments-extraction` runbook for by_surah
deliveries in any supported riwayah, including playlist deliveries: the
aligner detects which surahs each playlist file holds (one, several, a juz', or
part of a long surah) and the run cuts them into chapters. Titles are never read.

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
| Service package | `inspector/services/admin/align_pipeline/` — `runs` (start/retry/cancel/status), `runner` (worker threads), `stage_acquire` · `stage_align` · `stage_split` · `stage_sidecars` · `stage_assemble`, `sources` (manifest → source groups + slots), `partition` (pure cut logic), `resolve` (which file each surah is taken from), `manifest` (writes acquired size/duration/offset + split coverage back to the audio manifest), `adapt` (aligner rows → staged shapes), `aligner_client` (SSE), `staging` (bucket paths), `progress` (in-memory detail + cancel), `params` (knobs + env), `limits` (shared GPU/CPU budget) |
| Intake planner | `inspector/services/admin/intake_plan/` — `enumerate` (+ `drive`), `identity`, `plan`, `mint` |
| Durable row | `align_runs` table — `services/db/migrations/0031_align_runs.sql`, `services/db/repo_align_runs.py` |
| HF jobs | `qua_jobs/acquire_audio.py` (kind `acquire_audio`) and `qua_jobs/split_audio.py` (kind `split_audio`), both shown in the Jobs tab; shared fetch/encode/cut/peaks helpers in `qua_jobs/audio_io.py`; grouping in `qua_shared/audio/sources.py` |
| Build | `inspector/services/segments/promote_build.py` (shared with `scripts/bucket/promote_run.py`) |
| Routes | `inspector/routes/admin/align.py` — `POST /api/admin/reciter/<slug>/align`, `GET …/align/status`, `POST …/align/retry`, `POST …/align/cancel`; `inspector/routes/admin/intake_plan.py` — `GET`/`POST`/`PUT /api/admin/intake/<rid>/plan`, `POST /api/admin/intake/<rid>/align` |
| Capability | `intake.align` (owner + maintainer by default); `intake.align_unlimited` bypasses the shared budget (owner only by default); `intake.ingest` plans + mints an intake (owner only by default; the intake `/align` also needs `intake.align`) |
| Wire | `qua_shared/schemas/wire/align_runs.py` — `AlignRunStatus`, `AlignStartRequest`; `AdminRequestRow.align` overlay; `qua_shared/schemas/wire/intake_plan.py` — `IntakePlan` / `IntakePlanView` / `IntakePlanUpdate` / `IntakeAlignRequest` / `IntakeAlignResponse` |
| FE | `tabs/dashboard/components/admin/AlignProgress.svelte`, the Align block in `RequestsCompartment.svelte`, `tabs/dashboard/components/admin/intake/` (`IntakePlanPanel` + `IntakePlanSummary` / `IntakePlanEntries` / `IntakeIdentityForm` / `IntakeAlignCta`), `lib/api/admin-requests.ts` |
| Aligner side | `qua-aligner-app` — `/api/v1/batches` items by `audio_ref`, `/api/v1/extraction/sidecars`; both gated by `X-Extraction-Secret` |

## Stages

The audio manifest is the only input. It is grouped by physical file
(`qua_shared/audio/sources.py::groups_from_manifest`):

- a chapter's source is its manifest `source_url` when set, else its `url`; a
  group of one is a **single** chapter, a group of several a **combined** file;
- each manifest `sources` entry (`ManifestSource` — a playlist file minted with
  no chapters) is a **detect** group: nobody says what it holds.

Combined and detect groups get a **source slot** `201 + i` (`SLOT_BASE = 201`,
`MAX_SLOT = 999`, so at most 799 slot files per run). Slots sit above any
chapter number, so `audio/<slot>.mp3` never collides and the aligner's bucket-ref
pattern (1–3 digits) admits it unchanged. The grouping is frozen per run in
`staging/<slug>/<run>/groups.json`; the acquire job reads that file too, so slot
numbers agree everywhere even after split rewrites the manifest. A chapter
already cut on an earlier run (its `url` is its own bucket mp3) is grouped as a
plain single, so a realign never needs the original file again.

```
acquire   CPU HF Job qua_jobs/acquire_audio.py (sources on a thread pool, one per vCPU)
          single   → reciters/<slug>/{audio/<ch>.mp3, peaks/<ch>.json.gz}
          combined / detect → reciters/<slug>/audio/<slot>.mp3 (encoded once, no peaks)
          + staging/<slug>/<run>/acquire.json; the runner then writes the real
          size / duration back into the manifest (manifest.record_acquired)
align     per-file loop, aligner Space POST /api/v1/batches (alignment-only) +
          items/<n>/audio/stream with audio_ref=hf://buckets/<repo>/reciters/<slug>/audio/<n>.mp3
          → staging/<slug>/<run>/chapters/<ch>.json (single) or sources/<slot>.json (slot)
  split   still inside the `align` DB stage (stage_split): cut every slot file's rows
          by detected surah (partition.cut_file) → resolve which file each surah comes
          from (resolve.py) → split_plan.json {chapters: {ch: [[slot,start,end], …]}}
          → CPU HF Job qua_jobs/split_audio.py (kind split_audio) encodes each chapter's
          pieces end to end → audio/<ch>.mp3 + peaks, deletes the slots once every cut
          succeeded → rows rebased onto each cut, staged as chapters/<ch>.json; outcome →
          split_outcome.json + manifest (chapters written, sources cleared).
          From here a cut chapter is indistinguishable from a single one.
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
          coverage_report lists split drops as missing; mislabelled files, suspect cuts and
          files with no recitation as unresolved
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

## Split (surah detection, resolution, mislabel guard)

`partition.py` is pure. `cut_file` partitions one slot file's rows by the surah
of `ref_from`: special rows (Isti'adha / Basmala) attach **forward** to the surah
they introduce; unmatched rows attach to the surah before them. Each surah's
window runs from its first row's start to its last row's end, padded by
`TRIM_PAD_MS = 300`, clamped to the file, and never overlapping a neighbour (a
collision is cut at the silence midpoint). Windows are computed over every
surah in the file, so one later taken from another file still bounds its
neighbours.

`resolve.py` (pure) then decides where each surah comes from:

1. a single-chapter file keeps its chapter;
2. a combined file keeps the chapters the manifest planned for it, when found;
3. every other surah goes to the file with the most matched recitation
   (`matched_ms`). A further file adding new ayahs of that surah (at least
   `PIECE_MIN_NEW_SHARE = 0.8` of its ayahs uncovered) is **stitched** on as
   another piece, in ayah order — a long surah uploaded in parts. A file that
   only repeats covered ayahs (a re-upload) is **ignored** for that surah.

Coverage is tolerant, not fatal:

- a planned chapter no file holds is **dropped**; a surah nobody planned is
  **adopted** (every playlist chapter is adopted);
- a single-chapter file whose matched speech is at least `MISMATCH_SHARE = 0.6`
  another surah is **mismatched** (the `mohammed_burhaji_yt` mis-index) and
  dropped *before* the cut (its mp3 deleted), so another file can provide it;
- a file with no recitation found is reported (`empty_sources`);
- an unplanned surah whose pieces cover under `MIN_SURAH_COVERAGE = 0.5` of its
  ayahs is a **fragment**, not a chapter: a CD intro montage or trailer holds
  short excerpts of many surahs (seen live: the Afasy Juz ʿAmma CD's intro held
  excerpts of 78, 79, 80, 82, 92, 93). Fragments are listed in
  `unresolved_files`;
- when the aligner fails to place a surah (a short one right before the next,
  e.g. al-Ikhlāṣ + al-Falaq), its audio sits unmatched inside the neighbour's
  cut. The neighbour is kept — its unmatched segment fails validation until a
  reviewer fixes it — and flagged **suspect** when it holds ≥
  `SUSPECT_MIN_MS` = 3 s of unmatched recitation next to a surah missing from
  inside the delivery's span.

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

   Entries are listed once per URL; unavailable videos start excluded. No title
   is read for chapters. `identity.py` proposes the reciter id (the request's
   existing reciter, or the English name slugified for a new one), the channel
   (catalog `host_patterns`), the source (the generic source per host, created on
   mint when absent, else a new `<uploader>_youtube` source) and the slug per
   `catalog.md` §3 (`_v2`… on collision). Status ends `ready` or `failed`; an
   `enumerating` plan older than 15 min reads `failed`.
2. **Review** (`GET` / `PUT …/plan`). The owner toggles files in or out
   (`PlanEntryEdit.include` — leave out an intro, a du'a, a talk) and edits the
   identity. The view carries a live check. Errors: nothing included, identity
   problems (slug taken or malformed, unknown reciter, missing channel/source), a
   YouTube source without `INSPECTOR_YTDLP_COOKIES`, and for `links` chapters
   outside 1–114 or given to two links. Warnings: excluded files, very long
   files, and for `links` missing chapters.
3. **Align** (`POST …/intake/<rid>/align`). `mint.mint_and_align` builds the
   `intake.ingest()` body (delivery, reciter, vocab additions, audio manifest),
   flips the request to `accepted`, then calls `align_runs.start`. Playlist files
   become the manifest's `sources` (no chapters; `chapter_count` 0 until the
   split fills it); `links` keep their chapters, a URL shared by several getting
   unique bucket chapter `url`s with the original as `source_url`. The mint
   always lands first; a start the pipeline refuses (budget, config) is
   reported, and the new slug row's Align button retries it.

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
everything else through yt-dlp. YouTube **listing** mostly works from Hugging
Face but its connections get dropped (`SSL: UNEXPECTED_EOF_WHILE_READING`), so
`enumerate._extract` retries network failures (4 attempts, linear back-off) and
passes the cookies / proxy secrets below when set. **Downloads** are refused ("Sign in to confirm you're not a bot") without a
signed-in session. A run with any yt-dlp source installs `yt-dlp[default] deno`
in the job and passes the Space secrets `INSPECTOR_YTDLP_COOKIES` (a Netscape
cookies.txt export) and `INSPECTOR_YTDLP_PROXY` (optional) as the job secrets
`YTDLP_COOKIES` / `YTDLP_PROXY`. HF Jobs egress from AWS (AS14618): without a
session YouTube refuses every player client (`tv`, `tv_simply`, `android_vr`,
`ios`, `mweb`, `web_safari`, `web_embedded`, probed 2026-09). The job repairs a
cookies file whose tabs became spaces (`audio_io.normalize_cookies`), keeps at
most `YTDLP_WORKERS = 2` yt-dlp fetches in flight with `--sleep-requests 1`,
and after the first bot-check refusal (`BotCheckError`) fails the remaining
yt-dlp sources without a request. The error keeps yt-dlp's cookie warnings:
"no longer valid" means YouTube rotated the session after export — export
from a private window and close it without signing out. `INSPECTOR_GOOGLE_API_KEY` (optional) overrides
the scraped Drive listing key. The app itself needs `yt-dlp` (in
`inspector/requirements.txt`) for listing only.

## Not built yet

by_ayah deliveries, Katana runbook removal, prod rollout
(`INSPECTOR_ALIGN_PIPELINE` stays unset on prod).
