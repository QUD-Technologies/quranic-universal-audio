# Native align pipeline

One click in **Admin → Requests** takes a delivery from `awaiting_alignment` to
`awaiting_review` on the Spaces that already exist. No Katana, no laptop, no
new engines. Replaces the offline `segments-extraction` runbook for by_surah
deliveries in any supported riwayah.

Where it lives:

| Piece | Path |
|---|---|
| Service package | `inspector/services/admin/align_pipeline/` — `runs` (start/retry/cancel/status), `runner` (worker threads), `stage_acquire` · `stage_align` · `stage_sidecars` · `stage_assemble`, `adapt` (aligner rows → staged shapes), `aligner_client` (SSE), `staging` (bucket paths), `progress` (in-memory detail + cancel), `params` (knobs + env) |
| Durable row | `align_runs` table — `services/db/migrations/0031_align_runs.sql`, `services/db/repo_align_runs.py` |
| Acquire job | `qua_jobs/acquire_audio.py` (kind `acquire_audio`, shows in the Jobs tab) |
| Build | `inspector/services/segments/promote_build.py` (shared with `scripts/bucket/promote_run.py`) |
| Routes | `inspector/routes/admin/align.py` — `POST /api/admin/reciter/<slug>/align`, `GET …/align/status`, `POST …/align/retry`, `POST …/align/cancel` |
| Capability | `intake.align` (owner + maintainer by default) |
| Wire | `qua_shared/schemas/wire/align_runs.py` — `AlignRunStatus`, `AlignStartRequest`; `AdminRequestRow.align` overlay |
| FE | `tabs/dashboard/components/admin/AlignProgress.svelte`, the Align block in `RequestsCompartment.svelte`, `lib/api/admin-requests.ts` |
| Aligner side | `qua-aligner-app` — `/api/v1/batches` items by `audio_ref`, `/api/v1/extraction/sidecars`; both gated by `X-Extraction-Secret` |

## Stages

```
acquire   CPU HF Job qua_jobs/acquire_audio.py (chapters on a thread pool, one per vCPU)
          catalog/audio_manifest/<slug>.json → reciters/<slug>/{audio/<ch>.mp3, peaks/<ch>.json.gz}
          + reciters/<slug>/chapter_sources.json + staging/<slug>/<run>/acquire.json
align     per-chapter loop, aligner Space POST /api/v1/batches (alignment-only) +
          items/<ch>/audio/stream with audio_ref=hf://buckets/<repo>/reciters/<slug>/audio/<ch>.mp3
          → staging/<slug>/<run>/chapters/<ch>.json (raw aligner result)
sidecars  one reciter-wide POST /api/v1/extraction/sidecars (SSE) — the aligner runs
          qua_timing_batch low_confidence against the phoneme MFA Space and builds
          auto_split from interactive word timings returned by the align stage
          → staging/<slug>/<run>/sidecars/{low_confidence_v2,auto_split_v1}.json
          Hafs only for the probe: a non-Hafs delivery gets `low_confidence_v2: null`
          (D12, editions.md) and nothing is staged for it; auto_split_v1 is always staged
assemble  in-process: adapt → promote_build.build_artifacts (peaks from the acquired blobs,
          no ffmpeg) → reciters/<slug>/{detailed,segments,pipeline_meta,chapter_sources,
          coverage_report,edit_history*.jsonl,low_confidence_v2,auto_split_v1}.json
          low_confidence_v2 is required staged on Hafs, absent by contract off Hafs
          detailed.json written last; staging deleted
auto_detect  sees detailed.json → reciter.alignment_completed → awaiting_review
```

Lane choice: the Align CTA carries a **GPU / CPU** toggle (`AlignStartRequest.device`,
persisted in the run's `params_json` and echoed as `AlignRunStatus.device`). `GPU` is
the default and still falls back to CPU on `gpu_quota_exhausted`; `CPU` starts on the
Space's CPU worker pool and never touches the ZeroGPU quota. The live lane (after a
fallback) rides in `detail["device"]`, which the progress card renders.

Parameters (`params.py`): model `Large` (the same `hetchyy/r7` checkpoint as the
Katana extraction), `pad_left_ms=100`, `pad_right_ms=100`, `min_silence_floor_ms=50`,
matcher/thresholds = whatever the Space runs, `include_merge_groups=true`,
`include_auto_split_timings=true`, `discard_session=true`, no full word-timestamp
pass, no split. Only cross-verse and repetition rows are timed. Those interactive
word timings are returned with the alignment result and reused by Auto Split; times
are segment-relative while segment bounds remain relative to the persisted mp3 (no trim).
For each section boundary, the cursor is the midpoint between the preceding
section's last word end and the following section's first word start, then shifted
onto the chapter timeline by the segment start.

## Concurrency

Both long stages fan out, because neither is CPU-bound end to end:

- **acquire** runs one chapter per vCPU (`ACQUIRE_WORKERS` overrides, 8 max). Every
  per-chapter step releases the GIL — the fetch waits on a socket, ffprobe/ffmpeg/peaks
  are subprocesses — so the pool overlaps CDN latency with encode work instead of
  leaving a `cpu-upgrade` flavor idle on one serial chain.
- **align** keeps a rolling pool of 16 chapter HTTP streams. When one finishes,
  the next chapter starts. This overlaps bucket reads and request setup without
  sending all 114 chapters into Hugging Face's ZeroGPU scheduler at once. The
  aligner admits at most 16 GPU launches globally and one CPU job by default,
  rotating newly free slots between caller identities; single requests and batch
  items are peers. `INSPECTOR_ALIGN_CONCURRENCY` overrides only this transport
  pool for debugging. The stage widens urllib3's connection pool to match.

## Run lifecycle

- **Single-flight per slug**: a `pending | running | failed` row blocks a second
  start (partial unique index). `failed` waits for **Retry** (attempt+1, same run,
  resumes from the failed stage) or **Cancel** (row → `canceled`, staging kept).
- **DB writes only on stage transitions** (every `durable_transaction` pushes
  the whole DB to the bucket). Per-chapter progress = the staged files +
  `progress` (in-memory); `AlignRunStatus.detail` carries the live chapter /
  aligner stage / HF job status / sidecar beam.
- **Resume**: `app.py` calls `runner.resume_active()` at boot under
  `INSPECTOR_ALIGN_PIPELINE=1`; every stage skips what is already staged
  (acquire skips persisted chapters, align skips staged chapters, sidecars skip
  when both docs exist).
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

## Scope (v1) and what is refused at start

- by_surah deliveries with an audio manifest (one URL per chapter); combined
  files / playlists / by_ayah are refused (`400`).
- Any supported riwayah. The aligner is asked for the delivery's edition and
  returns projected rows; `adapt` re-derives each row's Hafs `source_ref` +
  `projection_support` through `services/segments/projection_stamp.py` (the
  same `qua_domain` reverse projection the save path uses), so the aligner
  ships nothing extra.
- Missing `INSPECTOR_EXTRACTION_SECRET` / HF token → `503`.

## Env (see `config-deploy.md`)

`INSPECTOR_ALIGN_PIPELINE=1` (enable + resume workers), `INSPECTOR_ALIGNER_URL`
(default the dev aligner Space), `INSPECTOR_EXTRACTION_SECRET` (must equal the
aligner Space's `EXTRACTION_SECRET`), `INSPECTOR_ALIGN_KEEP_STAGING=1` (debug),
`INSPECTOR_ACQUIRE_JOB_FLAVOR` / `INSPECTOR_ACQUIRE_JOB_TIMEOUT`
(`cpu-upgrade` / `6h`). Bearer for the aligner = the Inspector's own HF token.
The acquire job runs in the stock `INSPECTOR_JOB_IMAGE` (`python:3.11-slim`) like
every other kind — `services/admin/jobs/base.py::job_command` apt-installs ffmpeg
and pip-installs the kind's deps at launch; there is no prebuilt image Space.

## Not built yet

Intake rows ("Ingest & Align" from a `links` submission — today ingest first,
then Align on the slug row), playlist / combined-file acquire, Katana runbook
removal, prod rollout (`INSPECTOR_ALIGN_PIPELINE` stays unset on prod).
