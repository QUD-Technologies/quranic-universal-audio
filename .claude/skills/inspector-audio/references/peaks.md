# Waveform peaks

Single canonical on-bucket format (slim int8 packed gzip, schema v3) baked **offline on Katana** + parallel-read route + bounded LRU response cache. Per-segment ffmpeg decode is the only fallback. No runtime re-bake path, and **nothing deletes peaks at runtime** — the bucket is read-only at runtime (the GC sweeper was removed). Peaks persist indefinitely.

## Two-tier shape

| Tier | Source | Wire shape | When it fires |
|---|---|---|---|
| **1. Chapter overview** | `<wip\|published>/<slug>/peaks/<ch>.json.gz` (Katana-baked) | int8 envelope `{schema_version:3, q:'int8', n, peaks_b64, bps, duration_ms}` per URL — sent **verbatim**, no dequant | On accordion open: `ValidationPanel.svelte::_prefetchAccordionPeaks` fires one batch `GET /api/seg/peaks?chapters=…`. FE inflates b64 → `Int8Array` once and caches per URL. |
| **2. Per-segment ffmpeg** | Live ffmpeg decode (`-ss/-t`) against the cheapest input | nested float `PeakBucket[]` at HD 30 bps | `POST /api/seg/segment-peaks` on a card whose chapter overview peaks aren't loaded FE-side (rare once Tier 1 ran). |

No middle tier — every card slices its own range out of the typed array client-side via `peaks-view.ts` + the drawer. There is no "slice chapter peaks server-side and emit a sub-range".

## Compute paths

### Full file — `compute_audio_peaks(audio_source)`

`services/audio/peaks.py:74`. Decodes entire file to mono 16-bit PCM at `PEAKS_FFMPEG_SAMPLE_RATE = 8000 Hz`, buckets into min/max at `PEAKS_BUCKETS_PER_SEC = 30` (HD density). `ffmpeg -i <src> -f s16le -ac 1 -ar 8000 -v quiet -`. Returns `{schema_version, duration_ms, peaks: [[mn,mx],…]}` or `None`. **HD intermediate** — Katana hands it to `pack_slim` for storage; never persisted as-is post-v3.

### Slim packer — `peaks_slim.py::pack_slim(hd_doc, target_bps=10)`

1. `_decimate` 30 bps → `PEAKS_SLIM_BPS = 10` bps (min-of-mins / max-of-maxes over a 3-element window; non-divisible tail folded into one final bucket).
2. Quantize to int8 (`round(peak * 127)`, clip ±127). Inverse is `v / 127`.
3. JSON-wrap `{schema_version:3, duration_ms, q:"int8", bps:10, n, peaks_b64}`.
4. gzip level 6, `mtime=0` → byte-identical output for same input (stable CDN/browser cache key across no-change re-bakes).

~36 ms compute, ~21 KiB output for an avg chapter; ~14× wire vs the dropped v2 float-JSON. 10 bps oversamples a ≤1100 px overview canvas by ~100×; per-segment zoom uses Tier 2's 30 bps.

### Per-segment — `compute_segment_peaks(url, start_ms, end_ms, reciter, chapter)`

`peaks.py:153`. `_ffmpeg_decode_segment` picks the cheapest input: `src.path` (bucket mount) → local file; `src.data` (local-dev no-mount) → stdin; else the URL itself (ffmpeg HTTP-Range, network-enabled build). ffmpeg `-ss` is frame-aware → VBR- and CBR-correct on every input. Returns `{schema_version, start_ms, end_ms, duration_ms, peaks}` at 30 bps. Bucket count `max(MIN_SEG_PEAK_BUCKETS, int(dur_sec * 30))`. No caching.

> **History:** before HTTPS in the image, remote-only sources took a Python `_range_decode_segment` (urllib Range + ID3v2 synchsafe-int decode + `bytes_per_sec * t` arithmetic) — CBR-only, mis-estimated VBR by seconds late in the file. Replaced when the Dockerfile flipped to `--enable-openssl` + `http,https,tcp,tls` (~+8.5 MB). Reintroducing a Python byte-range decoder is the wrong direction.

## On-disk format (`<ch>.json.gz`, schema v3)

```json
{"schema_version":3,"duration_ms":6888280,"q":"int8","bps":10,"n":68883,"peaks_b64":"…(n*2 base64 signed bytes)"}
```

`n*2` interleaved int8s `[mn₀,mx₀,mn₁,mx₁,…]` each in `[-127,127]`.

Path helpers (`services/storage/storage_paths.py`):

| Helper | Path |
|---|---|
| `prefetched_peaks_path(slug, ch)` | `reciters/<slug>/peaks/<ch>.json.gz` (runtime reads) |
| `prefetched_peaks_legacy_path(slug, ch)` | `…/<ch>.json` (backfill/rollback only) |
| `prefetched_peaks_backup_path(slug, ch)` | `…/<ch>.json.bak` (rollback target — not read at runtime) |

Peaks live under the single `reciters/<slug>/peaks/` prefix regardless of lifecycle state — there is no separate published-peaks location.

## Generation — single writer

**Align pipeline HF jobs only.** `qua_jobs/acquire_audio.py` (single-chapter sources) and `qua_jobs/split_audio.py` (cut chapters) write `<ch>.json.gz` next to the chapter MP3. **There is no in-Flask fallback writer.** Peaks missing on the bucket fall through to Tier 2 ffmpeg in `routes/segments/peaks.py` — no runtime re-bake.

> Stale-comment caveat: `peaks.py` / `peaks_slim.py` docstrings still mention an `audio_fetch.fetch_and_persist_chapter` fallback that "re-bakes on AWAITING_REVIEW". That function was removed (see `prefetch.md`); ignore the comments.

## Readers — `unpack_slim_envelope` vs `unpack_slim`

Both in `peaks_slim.py`. Same validation (schema_version==3, `q=="int8"`, payload-length check), different output:

| Reader | Output | Consumer |
|---|---|---|
| `unpack_slim_envelope(blob)` | envelope **verbatim** (`q,n,peaks_b64` kept; no dequant, no `.tolist()`) | **Runtime route** via `audio_fetch.read_prefetched_peaks` → `seg_peaks`. Browser inflates b64 → `Int8Array`. Saves ~5 ms + ~3 MB float-list alloc/request on a husary-ch2-sized chapter. |
| `unpack_slim(blob)` | dequantized `{…, peaks: list[list[float]]}` | **Offline only** — extraction's history-JSONL writer that still emits float-list records. |

`read_prefetched_peaks(slug, url)` (`audio_fetch.py:70-99`): URL→chapter via `chapter_for_url`, `backend.read_bytes(prefetched_peaks_path)`, `unpack_slim_envelope`. Returns `None` for unknown URL / missing file / pre-v3 / corrupt blob — caller treats as a cache miss and falls through to Tier 2.

## Routes — `routes/segments/peaks.py`

### `GET /api/seg/peaks/<reciter>?chapters=<csv>&h=<hash>`

`seg_peaks`. Iterates the **manifest sidecar** (`audio_meta.chapter_urls`) for chapter→URL, not `entry.audio` (post-migration entries have `audio == ""`). Resolution order:

1. **Route LRU response-bytes cache** (`cache._PEAKS_RESPONSE_CACHE`, max 50, keyed `(reciter, sorted_chapter_tuple)`) — returns cached `orjson.dumps(body)` bytes, skips orjson + jsonify. flask-compress still re-gzips per request.
2. **In-process per-URL cache** (`cache._PEAKS_CACHE[reciter][url]`).
3. **Bucket fan-out** — `ThreadPoolExecutor(max_workers=8)` runs `read_prefetched_peaks` for misses (FUSE I/O releases GIL). `cache.set_peaks_for_url` takes `get_peaks_lock()` internally — the route MUST NOT wrap it in an outer `with lock:` (non-reentrant Lock → deadlock; `test_peaks_no_lock_deadlock_on_misses`).

Body `{"peaks": {<url>: <envelope>}, "complete": true}`. `complete` is wire forward-compat, **always true** — no background-compute path. Missing chapter files drop out silently.

Cache-Control: `?h=` present + non-empty → `public, max-age=31536000, immutable`; `?h=` absent + non-empty → `public, max-age=86400`; empty result → `no-store` (don't lock client into an empty response). Backend ignores `h`'s value — FE computes it over the audio URLs so a delivery flip invalidates.

### `POST /api/seg/segment-peaks/<reciter>`

`seg_segment_peaks`. Tier 2. Body `{segments:[{url,start_ms,end_ms,chapter,pad_ms}]}`. Per seg, `compute_segment_peaks` with `pad_ms` widening the window symmetrically. Returns `{peaks:{"<url>:<start>:<end>[:<pad>]": {peaks,start_ms,end_ms,duration_ms}}}`, nested `PeakBucket[]` floats @30 bps. No caching. FE only calls it when `getWaveformPeaks(url)?.peaks?.length` is empty.

### `GET / POST /api/seg/history-peaks/<reciter>`

Persisted per-op peaks for the History panel. Canonical slim records `{op_id, url, start_ms, end_ms, bps, peaks_b64}` (same int8-b64 shape as Tier 1, 10 bps) in `reciters/<slug>/edit_history_peaks.jsonl` — **permanent** (a root-level file, never GC'd), so anonymous viewers of a released reciter get instant history waveforms.

**Writers (all canonical b64):**
- **Save-time (authoritative)** — `services/segments/save.py::save_seg_data` → `services/audio/op_peaks.py::build_op_records` slices the baked 10 bps chapter peaks (`read_prefetched_peaks` → int8 slice → re-b64) per op range. No ffmpeg; best-effort (never blocks the save).
- **On-play write-back (straggler net)** — a row with no record ffmpeg-computes on play at 10 bps (`preview.ts::_ensurePeaks` → `fetchSegmentPeaks(..., bps=10)`) and POSTs the packed slice. The POST is `require_same_origin` only (no edit-lock): it persists derived, idempotent peaks for *existing* ops, so any same-origin viewer incl. anonymous fills the cache. Guards: op_id-must-exist (`history_query.edit_history_op_ids`), dedup-by-op_id, record/`peaks_b64` size bounds.
- **Offline** — `audio_persist.write_edit_history_peaks` + `backfill_pipeline_peaks.py`.

**Dedup-by-op_id** in `peaks_history.append_peaks_records` skips already-persisted ops (no bucket write) → one record per op, idempotent under autosave re-saves + concurrent anonymous plays (op_ids are immutable). **GET** serves records verbatim (no float inflation); FE decodes `peaks_b64` → `Int8Array` (`utils.ts::indexHistoryPeaksRecords`) into the covering-range cache. Response bytes cached per reciter (`cache._seg_history_peaks_response`, invalidated on append); wire `no-store`. The `bps` param on `compute_segment_peaks`/`/segment-peaks` defaults to HD 30 (Segments-zoom + Timestamps); History passes 10.

## Cache + invalidation policy

| Cache | Lifetime | Evicted by |
|---|---|---|
| `_PEAKS_RESPONSE_CACHE` (LRU 50, response bytes) | Process | Own LRU cap + `pop_reciter_peaks_response_cache(reciter)` (manual rebuild only). **NOT evicted on save/undo** — peaks are tied to immutable audio bytes, not segment edits. `invalidate_seg_caches` (fires every autosave) deliberately leaves the peaks LRU alone. |
| `_PEAKS_CACHE` (per-URL envelope dicts) | Process | Manual `pop_peaks_cache(reciter)`; no auto-eviction. Tiny per entry. |
| FE `waveform-cache` (per-URL `AudioPeaks` Map) | Tab | `clearWaveformCache()` / `resetWaveformState()` on reciter change. `Int8Array` for Tier 1 **and history** (~140 KB/ch — history records are decoded to int8 on receive via `indexHistoryPeaksRecords`); `PeakBucket[]` only for the Tier-2 ffmpeg fallback (~3 MB/ch — the remaining int8-drawer-proposal regret). |

The old disk cache (`CACHE_DIR/<reciter>/peaks/<sha256>.json`) was removed (commit `4a51e411`) — bucket is canonical, runtime never re-persists computed peaks anywhere.

## FE consumers

- **Shared decoder** `lib/utils/peaks-decode.ts::b64ToInt8(b64, n)` — the single signed-reinterpret implementation. Both `_fetchPeaks` (Segments) and `ensureChapterPeaks` (TS, `lib/utils/peaks-fetch.ts`) decode through it so the byte interpretation can't drift across tabs. The route emits int8 **verbatim** (no `?shape=i8` param — a `peaks-view.ts` comment to the contrary is stale).
- `tabs/segments/utils/waveform/utils.ts::_fetchPeaks` — batched request on accordion open; per URL, `b64ToInt8` → `Int8Array(n*2)`, stash via `setWaveformPeaks(url, {peaks, duration_ms})`.
- `lib/utils/peaks-view.ts` — **shape adapter** exposing `length / min(i) / max(i)` over either an `Int8Array` (Tier 1 + **history**) or a nested `PeakBucket[]` (Tier 2 ffmpeg fallback only). Drawer (`waveform-draw.ts`, `tabs/segments/utils/waveform/draw-seg.ts::_slicePeaks`) branches on shape **once at view construction**; per-pixel reads are shape-free. (The save-time op-peaks slicer is the **backend** `services/audio/op_peaks.py` — there is no FE `op-peaks.ts`.)
- Per-segment slicing is client-side: `_slicePeaks` does a zero-copy `subarray(i0*2, i1*2)` and resamples to canvas buckets — no server roundtrip for in-cache segments.

## Backfill + audit (operate on the on-disk envelope — stable since v3)

- `scripts/backfills/backfill_peaks_slim.py` — walks `reciters/*/peaks/` for legacy `.json`, packs via `pack_slim`, writes `.json.gz`, renames original to `.json.bak`. Idempotent (skips existing `.json.gz`).
- `scripts/backfills/rollback_peaks_slim.py` — symmetric reverse (then revert `PEAKS_SCHEMA_VERSION` to 2 manually).
- `scripts/bucket/audit_bucket_reciter.py::_audit_peaks_slim` — validates every `<ch>.json.gz` against the envelope (`schema_version==3`, `peaks_b64`/`bps`/`duration_ms` present, gzip + JSON parse clean). Part of the pre-release audit.

## Performance

- Warm repeat-open accordion ~50 ms (response-bytes cache + per-URL cache → orjson skip + jsonify skip + zero bucket reads).
- Cold: ~3 ms/file FUSE × 8 workers ≈ ~45 ms for a 114-chapter request. `hffs.cat_file` fallback (~50–500× slower) dominates cold opens — bucket-mount in prod avoids it.
- int8 envelope route is the FE win: ~33× faster `JSON.parse`, ~25× less heap (`Int8Array(2N)` vs `Array<[number,number]>`) — bench in `docs/proposals/peaks-int8-drawer.md`.

## Common peaks issues

- **Stale peaks**: `?h=` hash didn't change after edit — FE hash missed the boundary mutation. Inspect peaks request URL.
- **`/peaks` slow on first hit**: bucket short-circuit didn't fire — URL not in sidecar (`chapter_for_url` → None). Manifest missing/stale; re-run `scripts/audio/probe_audio_meta.py`.
- **One URL returns null**: `unpack_slim_envelope` → `None` (corrupt, pre-v3, or extraction never shipped it). No re-bake fires — backfill or re-extract. Check `peaks.py` route log.
- **Per-seg POST fires on a prewarmed reciter**: chapter peaks not yet in `getWaveformPeaks(url)`, or slim 10 bps slice too coarse for a short zoomed seg (10 bps × 1 s = 10 buckets). Expected.
- **Empty response + `no-store`**: no `.json.gz` for the requested chapters. Backfill not run or extraction skipped them — `scripts/backfill_peaks_slim.py --dry-run`.

## Module index

| Module | Role |
|---|---|
| `services/audio/peaks_slim.py` | `pack_slim`, `unpack_slim` (float-list, offline history), `unpack_slim_envelope` (verbatim, runtime route) |
| `services/audio/audio_fetch.py` | `read_prefetched_peaks` (envelope reader), audio read primitives |
| `services/audio/peaks.py` | `compute_audio_peaks` (HD), `compute_segment_peaks` (Tier 2 ffmpeg), `is_current_schema` |
| `routes/segments/peaks.py` | `/peaks` (Tier 1), `/segment-peaks` (Tier 2), `/history-peaks` |
| `services/storage/cache.py` | `_PEAKS_CACHE`, `_PEAKS_RESPONSE_CACHE` (LRU 50), `get_peaks_lock`, `pop_reciter_peaks_response_cache` |
| `services/storage/storage_paths.py` | `prefetched_peaks_path` + legacy/backup variants |
| `services/audio/peaks_history.py` | `edit_history_peaks.jsonl` per-op records (canonical `peaks_b64`); `append_peaks_records` dedup-by-op_id |
| `services/audio/op_peaks.py` | save-time record builder — slices baked chapter peaks per op range |
| `frontend/src/lib/utils/peaks-view.ts` | Shape adapter (`Int8Array` vs `PeakBucket[]`) |
| `frontend/src/lib/utils/peaks-decode.ts` | Shared `b64ToInt8` decoder (cross-tab single source of truth) |
| `frontend/src/lib/utils/peaks-fetch.ts` | Cross-tab/TS peaks fetch (`ensureChapterPeaks`, `pickChapterPeaks`, `fetchSegmentPeaks`) |
| `frontend/src/lib/utils/waveform-cache.ts` | Per-URL FE cache |
| `frontend/src/lib/utils/waveform-draw.ts` | Pure drawer |
| `frontend/src/tabs/segments/utils/waveform/utils.ts` | `_fetchPeaks` (b64 → `Int8Array`) |
| `frontend/src/tabs/segments/components/validation/ValidationPanel.svelte` | `_prefetchAccordionPeaks` (Tier 1 batch on accordion open) |
| `scripts/{backfill,rollback}_peaks_slim.py` | One-shot bucket migration |
| `scripts/audit_bucket_reciter.py::_audit_peaks_slim` | Per-reciter integrity check |
| `.local/extraction/segments/audio_persist.py` | Katana-side sole writer (Tier 1 + history JSONL) |
