# Audio metadata pipeline — generation, cataloging, probing, auditing

How per-chapter audio metadata (`duration_sec` / `bitrate_kbps` / `bitrate_mode`) and the
delivery-level rollups (`total_duration_sec` / `chapter_count` / `sample_rate_hz`) are
**produced, stored, served, audited, and repaired**. Read this before adding a new
source/channel, debugging wrong durations/scrubbers, or re-running a catalog-wide metadata
audit.

Scope boundary: the **in-app** audio subsystem (peaks, proxy/clip routes, AudioPort, VBR
routing) is owned by the `inspector-audio` skill. The catalog **data model** (tables, slug
convention) is [`catalog.md`](catalog.md). This doc is the **operational metadata pipeline**
that feeds both — much of it lives in the gitignored `.local/` extraction repo plus
`scripts/`.

## Where the data lives (and who writes it)

| Datum | Home | Written by |
|---|---|---|
| per-chapter `url` / `size_bytes` / `duration_sec` / `bitrate_kbps` / `bitrate_mode` | bucket sidecar `catalog/audio_manifest/<slug>.json` (`AudioManifestSidecar`) | extraction intake mint; backfill tooling |
| `_meta.checksum` / `_meta.chapter_count` | same sidecar | `sha256` of sorted `key=url;` pairs `[:16]` — see `services/admin/intake.py::_build_manifest` |
| delivery rollups `chapter_count` / `total_duration_sec` / `bitrate_mode` / `bitrate_kbps_nominal` / `sample_rate_hz` / `channels` | SQLite `deliveries` row | intake mint; `repo_catalog` |
| chapter audio bytes / waveform peaks | bucket `reciters/<slug>/audio/<ch>.mp3`, `reciters/<slug>/peaks/<ch>.json.gz` | offline extraction (`.local/extraction`) |

**Sidecar = per-chapter truth; row = uniform rollup.** The two are independent at the storage
layer (a row can carry `total_duration_sec` even when sidecar chapters were never probed), so a
metadata fix usually touches *both*: sidecar files (bucket) **and** the delivery row (DB). →
[`catalog.md`](catalog.md) §5 for the field-by-field row-vs-sidecar split.

## Generation (offline extraction → intake mint)

The authoritative writer path, in `.local/extraction/`:

| Step | File | What |
|---|---|---|
| decoded probe | `remux_bucket_audio.py::probe(bytes)` | walks every MPEG-1 L3 frame → `real_dur_s = frames·1152/sr`, declared kbps, Xing tag, drift% |
| source probe | `.local/audio-scripts/probe_audio_meta.py::classify(url)` | full-download → cbr/vbr via **whole-file linear-seek error** (`max_linear_seek_error_ms`, mirrors `qua_shared/mp3_frames.py`); duration/kbps off the frame grid. (Was: 256 KB head + mutagen header — both mislabelled; see §CBR/VBR detection.) |
| reprobe persisted | `ingest_intake.py::reprobe_{persisted,bucket}_audio` | re-probe the *extracted* mp3s → per-chapter `{duration_sec, bitrate_kbps, bitrate_mode, sample_rate}` shaped for the manifest builder |
| manifest assembly | `intake/manifest_builder.py::build_audio_manifest` | writes the per-chapter sidecar fields |
| delivery rollup | `ingest_intake.py::_rollup_bitrate_mode` / `_sum_duration_sec` | folds per-chapter probes into the row rollup |
| mint (bucket+DB) | `inspector/services/admin/intake.py::_build_manifest` + ingest | atomically writes the sidecar then the delivery row |

**Invariant: record DECODED duration, never bitrate-estimated.** `format.duration` (and a naive
`size·8/bitrate`) lie on VBR. The pipeline reads duration from the decoded frame count (or the
Xing frame-count header), which is why extracted reciters don't emit phantom-tail durations.
Download-only (YouTube / Drive / SoundCloud) sources can't be HTTP-frame-probed. They now arrive
through the **online intake** (Requests-tab plan → mint → align; `align-pipeline.md`): the mint
writes a URL-only manifest, the align pipeline's acquire job produces the canonical 192k CBR encode,
and `services/admin/align_pipeline/manifest.py` fills the per-chapter `size_bytes` /
`duration_sec` / `bitrate_*` (and, after a split, `source_offset_ms`) from those bytes, then
rolls `chapter_count` / `total_duration_sec` / `bitrate_mode` / `sample_rate_hz` up to the row
for fields still unknown. Probed fields are never overwritten.

## Serving (in-app reads — depth in the `inspector-audio` skill)

`inspector/services/audio/audio_meta.py` loads + LRU-caches the sidecar and drives VBR routing
(`is_vbr`), warmup hints, and the `/peaks` URL enumeration. Two fallbacks worth knowing here:

- **`metadata.py` route** falls back to the slim **peaks-header `duration_ms`** when the manifest
  `duration_sec` is null — so the scrubber shows a real length even pre-backfill. Peaks
  `duration_ms` is the ffmpeg-decoded length (dead-tail-free) and is the most authoritative
  duration source when peaks exist.
- **`clip.py`** fail-louds (502) on a deep seek into a dead tail rather than streaming silence.
- **Null `bitrate_mode` is treated as CBR** (`_is_vbr_entry`) — so missing metadata silently
  mis-routes VBR audio. Backfilling mode is not cosmetic.

## VBR caveats (the duration-correctness core)

Duration source priority, most→least trustworthy:

1. **Peaks `duration_ms`** (ffmpeg-decoded) — dead-tail-free; the correct fix for phantom tails.
2. **Xing/Info header frame count** → `frames·spf/sr`. Exact, cheap (first frame only).
3. **CBR + total size** → `size·8/kbps`. Exact *only* for CBR (uniform frame bitrate).
4. **Full decode walk** — the *only* correct path for **headerless VBR**; requires the whole file.

Failure modes:

- **Phantom tail** (e.g. `maher_al_muaiqly_mp3quran` ch76: 449s manifest, ~300s real audio): the
  file carries trailing dead/silent bytes that size-math and frame-walk both count but ffmpeg
  decode stops before. Symptom: scrubber longer than audible audio; deep seeks 502. Fix: take
  the duration from **peaks** (decoded), and flag/curate the file.
- **Headerless VBR**: no Xing, VBR encoding → every cheap method is wrong. Must full-download and
  walk frames. The backfill flags these `needs_full` unless `--allow-full` is set.
- **`mixed` bitrate_mode**: chapters disagree → row `bitrate_kbps_nominal` MUST be null (model
  validator enforces). Per-chapter sidecar is the truth.

## CBR/VBR detection (the routing-correctness core)

The `bitrate_mode` verdict decides playback transport: a `cbr` chapter is seeked
natively by the browser (`<audio>.currentTime`); anything else routes through the
server-side clip endpoint. So the verdict must answer exactly one question —
**can the browser seek this file natively and land accurately?** — and the only
reliable measure of that is the **whole-file linear byte→time seek error**:
walk every audio frame, and for each compute how far a linear `time→byte` map
lands from the frame's true time; the max over all frames is the error. `≤ 200 ms`
⇒ `cbr`, else `vbr`. Canonical impl: `qua_shared/mp3_frames.py::classify_bitrate_mode`
(+ `max_linear_seek_error_ms`); `probe_audio_meta.py` mirrors it inline (it runs
where `qua_shared` may be unimportable). The extraction-side bitrate audit and
the pipeline's `_force_info_if_cbr` use the same metric.

Two shortcuts were tried and **both produced systematic mislabels** — do not
reintroduce them:
- **mutagen header `bitrate_mode`** (Xing/Info/VBRI/LAME): reports the file's
  self-declared marker. Our own extraction stamps `Xing` on CBR audio, so this
  calls genuinely-CBR files VBR; and it says nothing about whether the seek is
  actually linear.
- **head-only / `len(set(bitrates))==1`**: misses VBR whose bitrate variation
  starts past the scanned head (whole tvquran/archive reciters read as CBR and
  stalled on seek), and a single stray odd-bitrate frame flips otherwise-CBR
  audio to a false `vbr`. The TOC's own quantization also swamps any
  Xing-TOC-linearity check.

Because the metric needs every frame, source probing downloads the **full file**
(`probe_audio_meta.classify`, `mp3_probe.probe_source(allow_full=True)`);
`allow_full=False` returns a head duration estimate with `bitrate_mode` left
unset (`needs_full`). A one-shot catalog correction (manifest labels + delivery
rollup + bucket `Xing→Info` tag reconcile) runs from the extraction bitrate
audit; this detection fix stops intake from regenerating the mislabels.

## Probing pitfalls (hit these when adding a source/channel)

- **ID3v2 cover-art tags**: archive.org embeds art in tags up to ~1.5 MB (tvquran ~285 KB), so a
  256 KB range fetch from byte 0 never reaches the first audio frame (`no_frame_in_head`). Read
  the ID3v2 size from the first 10 bytes, then range-fetch *after* the tag. See
  `qua_shared/mp3_probe.py::_fetch`.
- **MPEG version**: archive/tvquran files are often MPEG-2/2.5 (lower sample rates) — a
  MPEG-1-only frame parser scans 0 frames. Use the multi-version parser in `mp3_probe.py`
  (not the MPEG-1-only `remux_bucket_audio.parse_frame_header`).
- **archive.org stale node URLs**: manifests store per-item node hostnames
  (`ia801506.us.archive.org/<n>/items/<item>/…`) that go stale as items migrate → timeouts /
  connection-refused. The **canonical** `https://archive.org/download/<item>/<file>` form
  302-redirects to a live node and is durable; the proxy (`requests.get`, `allow_redirects=True`)
  follows it. Rewrite stored URLs to canonical to fix playback *and* re-enable probing
  (`mp3_probe.canonical_archive_url`). Changing a URL means recomputing `_meta.checksum`.
- **archive.org throttling**: bursts of range requests return timeouts that *look* like dead
  URLs. Use modest concurrency + a retry, and treat archive staleness as **per-item** (probe the
  node once per reciter, classify chapters via the fast canonical form) — see
  `scripts/diagnostics/audio_url_audit.py`.
- **HF bucket API rate limit**: 2500 requests / 5 min on `/api/buckets/*`. Bulk reads (manifests,
  peaks, audio bytes) must go through the **mount**, not the API, or throttle ≤7 req/s.

## Bucket + mount mechanics

Reads go through `services/storage/data_dir.py` / `storage_paths.py`, never raw `Path.read_text`.
Two backends:

- **hf-mount FUSE** (`services/storage/auto_mount.py`) — `reciters/<slug>/…` reads are
  filesystem reads (Xet content, *not* API calls → no rate limit). Prod is refused to local
  processes unless `INSPECTOR_ALLOW_PROD_BUCKET=1`; **FUSE cannot mount over WSL `/mnt/c`
  (drvfs)** — mount to a native Linux path (e.g. `~/qua-prod-mount`).
- **hffs `cat_file`** fallback — 50-500× slower per read, counts against the API rate limit.

Bucket CLIs live in `scripts/bucket/` (default dev; `--bucket prod` + `--yes-prod` to mutate).
Sidecar writes are plain bucket file writes + a **prod Space restart** to propagate (the running
Space caches catalog/manifests). The DB is the source of truth synced full-file with db_seq CAS
(→ [`database.md`](database.md)); edit prod deliveries only via
`.claude/skills/inspector-admin/scripts/admin_db.py exec "<SQL>" --write --prod --yes-prod`
(wraps `durable_transaction` + sync-back), **not** by editing the bucket DB copy under a live
Space (it gets clobbered).

## Auditing + backfilling (the operational tooling)

`scripts/diagnostics/` + `scripts/backfills/` — all read bucket via `--mount` and default to
dry-run:

| Tool | What |
|---|---|
| `diagnostics/audio_metadata_sweep.py` | scan all manifests for null metadata; `--peaks` flags **duration drift** (manifest vs decoded peaks → phantom tails) |
| `diagnostics/audio_url_audit.py` | classify every source url **ok / fixable (canonical) / dead**; per-reciter valid-after-fix; cross-channel duplicate/keep-remove signal |
| `qua_shared/mp3_probe.py` | pure-python multi-version mp3 source prober: cbr/vbr via whole-file linear-seek metric (`qua_shared/mp3_frames`), decoded duration off the frame grid, ID3v2 skip, archive canonicalization. `allow_full=True` downloads the file; `allow_full=False` → head duration only, mode `needs_full` |
| `backfills/backfill_audio_manifest_meta.py` | fill null meta from bucket-decode / peaks / source-probe; `--fix-drift` corrects phantom tails from peaks; `--rewrite-urls` repairs stale archive urls; `--remove-dead <audit.json>` drops dead chapters; emits delivery-row rollup `UPDATE`s via `--sql-out` |

### Runbook — onboard a new source/channel, or re-audit the catalog

1. **Sweep** `audio_metadata_sweep.py --bucket prod --mount … --peaks` → null + drift inventory.
2. **URL audit** `audio_url_audit.py --archive-only --json` (+ a `--quick` all-reciter pass for
   alternate health) → ok/fixable/dead + per-reciter valid-after-fix + duplicate alternates.
3. **Backfill (dry-run)** `backfill_audio_manifest_meta.py --all-null <sweep> [--slug …]
   --fix-drift --rewrite-urls --allow-full --remove-dead <audit> --sql-out rollups.sql
   --mount …` → corrected sidecars in `/tmp/manifest_backfill/`, schema-validated.
4. **Back up** the affected manifests (the bucket has **no versioning** — deletions are
   permanent) before applying.
5. **Apply**: re-run step 3 with `--apply --yes-prod` (writes sidecars), then run `rollups.sql`
   via `admin_db.py … --write`, then **restart the prod Space**.
6. **Verify**: re-sweep; spot-check `/api/audio/surahs` durations and VBR routing.

VBR-source reciters need `--allow-full` (full downloads) for the headerless ones; expect a few
genuinely-dead (404) chapters that no probe recovers — inventory them rather than guessing a
duration. Latest run's evidence + inventories: `.local/audio_audit/` (`url_audit_prod.json`,
`null_metadata_sweep_prod.json`, manifest backups).
