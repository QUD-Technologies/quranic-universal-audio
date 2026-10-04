# Backend / DB / caching / I/O performance

The single-worker substrate: where CPU and bucket I/O actually go, every cache and its invalidation, the I/O fan-outs, and how to measure. All paths under `inspector/` unless noted.

## Hot-path cost map

**Cold dashboard / public catalog load** — the only high-frequency *concurrent* path.
`GET /api/static/catalog.json` (`routes/public/static.py`) and `/api/public/...` (`routes/public/public.py`) funnel through `catalog_service.snapshot()` (`services/state/catalog.py:65`) → `repo_catalog.snapshot()`, a full `ReciterCatalog` pydantic rebuild from SQLite. **~38 ms today; projected ~300 ms @10×, ~700 ms @100× deliveries** (documented `cache.py:676-682`). Single-threaded → multiplies across concurrent anonymous visitors on the one worker. Warm: three db_seq-keyed layers collapse it to a dict handback — `_catalog_snapshot` (model), `_catalog_json_bytes` (serialized bytes), `_public_reciters` (materialized list). **Dominant layer: Python CPU. Bucket I/O is zero** (catalog is in SQLite, pulled at boot). *Guidance: anything new on the anonymous path must be db_seq-cached or it compounds.*

**Segments validate** (`services/validation/__init__.py:98`). Cold = 4 parallel bucket reads (`detailed.json`, resolved-by-edit index from `edit_history.jsonl`, `low_confidence_v2.json`, `segments.json`) over `ThreadPoolExecutor(max_workers=4)` (`__init__.py:108`), then pure-CPU classification per segment. Cached `_seg_validate_result`. **Cold: bucket I/O (biggest read is `detailed.json`). Warm: CPU classify, miss only after save.** The phonemizer is fully removed from this runtime (`__init__.py:32-37`) — that was the big win of the persisted-classifier-fields pattern.

**Peaks** (`routes/segments/peaks.py`). Up to 114 gzip-shard reads over `ThreadPoolExecutor(max_workers=8)` (`_PEAKS_READ_WORKERS`); warm served from `_PEAKS_RESPONSE_CACHE` as **pre-serialized orjson bytes** (skips re-encode). That bytes cache exists because re-`jsonify`-ing husary ch2 (119k peak tuples) costs **~1.5–2 s of single-worker CPU** (`cache.py:380-385`).

**Save** (`services/segments/save.py::save_seg_data`, ~line 497) — the most expensive write, fully sequential bucket I/O:
1. `load_detailed` (cached or 1 read), validate envelopes (cheap CPU).
2. `_stamp_persisted_classifier_fields` per mutated seg (`save.py:292`) — qalqala + boundary_adj at write time.
3. `persist_detailed` → `write_detailed_doc` + `rebuild_segments_json` — **2 writes** (the rebuild first *reads* segments.json to preserve `_meta`, `save.py:250`).
4. `append_edit_history` → `append_jsonl` — **1 write**. On a mount this is an in-place append; **unmounted it's whole-file read-modify-write**.
5. Per-op peaks bake: `op_peaks.build_op_records` (reads chapter peaks) + `append_peaks_records` (writes `edit_history_peaks.jsonl`) — best-effort, never fails the save.
6. Surgical cache invalidation (`save.py:466-470`).
Save writes per-reciter **bucket files**, not SQLite — no DB sync fires on a segment save. **Dominant layer: bucket I/O (4–5 serialized round-trips).**

**State transition** (`services/state/state.py` via `durable_transaction`) — SQLite write + **synchronous full-DB upload** (`db/sync.py::upload`, SQLite `backup()` + CAS-guarded write of `inspector.db` + `inspector.seq`). The one write path that blocks on bucket I/O before acking 200. `deferred_sync()` coalesces N commits into one upload (boot-scan storm fix); only the outermost boundary uploads (nested = SAVEPOINTs).

**Audio play** (`routes/audio/proxy.py`) — bucket-resident → `send_file` on the mount → OS sendfile, Range/304, zero slurp. CDN fall-through streams 64 KB chunks. Near-zero Python CPU on the mount path.

## Cache inventory

All in `services/storage/cache.py` unless noted. `_KeyedCache` = LRU-20 (`_KEYED_CACHE_LRU_MAX`); `_SingletonCache` = unbounded single value (used only for process-life immutables).

| Cache | Kind | Invalidated by |
|---|---|---|
| `_seg`, `_seg_meta`, `_seg_verses`, `_seg_resolved_by_edit`, `_seg_probe_v2`, `_seg_edit_history`, `_seg_history_peaks`, `_seg_validate_result`, `_seg_stats_result` | KeyedCache LRU-20 | popped every save/undo (`pop_seg_caches_affected_by_segment_edit`) |
| `_seg_auto_split` | LRU-20 | only when uid set changes (`batch_changes_segment_set`) |
| `_seg_pipeline_meta` | LRU-20 | never on save (extraction-time immutable); explicit pop only |
| `_seg_history_batches`, `_seg_split_group_index` | LRU-20 | appended/extended in place on save, popped on undo |
| `_seg_history_peaks_response` | LRU-20 (bytes) | save + anon write-back |
| **`_PEAKS_CACHE`** (per reciter,url envelope) | manual dict, lock | **UNBOUNDED — never invalidated; process-restart only** |
| `_PEAKS_RESPONSE_CACHE` (chapter peaks bytes) | OrderedDict LRU-50 (bytes), ~10 MiB ceiling | **NOT evicted on save** — `invalidate_seg_caches` (cache.py:278-296 docstring) deliberately leaves it; peaks track immutable audio bytes, not edits. Sheds only via its own LRU + an explicit `pop_reciter_peaks_response_cache` wherever a future path rewrites bucket peaks. (Distinct from `_seg_history_peaks_response`, which IS popped on save.) |
| `_audio_url`, `_audio_manifest`, `_audio_manifest_url_index` | LRU-20 | `pop_audio_manifest_cache` / `invalidate_audio_manifest_cache` |
| `_public_reciters`, `_catalog_snapshot`, `_catalog_json_bytes`, `_admin_users`, `_capability_matrix` | single tuple keyed on **db_seq** | **self-invalidating** — any committed write bumps db_seq |
| `_admin_requests` | dict keyed (db_seq,status) | self + prunes old db_seq on set |
| `_jobs_in_flight` | single tuple, 5 s TTL | TTL + launch/complete handlers |
| **`_AUDIO_CACHE_STATUS`** | manual dict | **UNBOUNDED** — `pop_audio_cache_status` caller-driven only |
| `_phoneme_sub_pairs`, `_word_counts`, `_qpc`, `_dk`, `_surah_info_lite`, … | SingletonCache | never (immutable post-boot) |
| `bridges_for_verse` | `@functools.lru_cache(4096)` (`services/reference/tajweed.py`) | pure inputs, no invalidation |

**Watch:** `_PEAKS_CACHE` and `_AUDIO_CACHE_STATUS` are the two unbounded caches in an otherwise-bounded module — on a long-lived worker browsing many reciters they grow without ceiling (the LRU-50 response cache masks it functionally, not the RSS leak).

**Stale comment to fix:** `cache.py:391-393` claims `pop_reciter_peaks_response_cache` is "wired into `invalidate_seg_caches`". It is NOT — `invalidate_seg_caches` (`:278-311`) deliberately leaves the peaks LRU alone, and the bake-vs-edit reasoning is correct. The comment is the liar.

**Cleverest pattern — db_seq self-invalidation:** keying a cached value on the monotonic db_seq means *any* committed write invalidates it with zero per-mutation hooks. A benign race just recomputes for one seq. Apply whenever a cached value is a pure function of DB state.

## Compute-placement map

| Field / result | Computed at | Persisted in | Source-of-truth helper |
|---|---|---|---|
| `qalqala_letter` | extraction / save / backfill | `detailed.json` seg | `services/segments/qalqala.py::compute_qalqala_letter` |
| `is_boundary_adj` | extraction (w/ canonical) / save (structural-only) / backfill | `detailed.json` seg | `classifier.py::compute_is_boundary_adj` — **asymmetric by design**: save passes `canonical=None` (no ASR at edit time); read path short-circuits on persisted value (`save.py:292-327`) |
| `classified_issues` (per-op) | save (`_attach_classified_issues`) | `edit_history.jsonl` op snapshots | `snapshot_classifier.classify_snapshot` |
| per-op history peaks | save (`op_peaks.build_op_records`) / backfill / lazy-on-play | `edit_history_peaks.jsonl` | `services/audio/op_peaks.py` — slices baked int8 chapter peaks, no ffmpeg |
| `deleted_basmala_chapters` | extraction / backfill | `pipeline_meta.json` | `qua_shared/pipeline_meta.py::collect_deleted_basmalas` (post-#5: reads sidecar, not re-derived per cold validate) |
| chapter peaks (10 bps int8) | offline extraction | `peaks/<ch>.json.gz` | extraction; served verbatim |
| catalog snapshot / public reciters | read-time, db_seq cached | not persisted | `repo_catalog.snapshot()` |

The bench/drift harness (below) is the only guarantee that save/extraction/backfill/fall-through stay byte-equivalent. **Any change to a persisted-field writer must pass drift.**

## I/O & concurrency

**ThreadPoolExecutor fan-outs (all three):** validate 4 reads (`validation/__init__.py:108`, `max_workers=4`); peaks shards (`routes/segments/peaks.py`, `max_workers=8`); intake probe (`services/admin/intake_probe.py`, `min(_MAX_WORKERS, len(targets))`). Rationale repeated in each: FUSE/SSL recv releases the GIL → threads are correct for I/O-bound reads, and each loader self-caches so threads don't double-fetch.

**Serialization points:** `connection._WRITE_LOCK` (RLock — one serialized SQLite writer; WAL readers concurrent); `durable_transaction` → synchronous `db.sync.upload()` holds the request until the full DB is on the bucket; per-slug `_detailed_locks` (double-checked locking in `data_loader.py` so concurrent cold-reciter readers don't all fetch+parse); `_PEAKS_LOCK` (non-reentrant — the peaks route must NOT wrap `set_peaks_for_url` in an outer lock, `test_peaks_no_lock_deadlock_on_misses`).

**Missing parallelization:** save's peaks-bake (read+write) is independent of the history-append write but runs serially. The 4–5 save writes are mostly dependency-ordered (write detailed before rebuild), so upside is limited — but it's the first place to look if save latency regresses.

## Good patterns (teach these)

- **Cache-module ownership** — `cache.py` owns every mutable cache var; no `global` cache state elsewhere; getter/setter/invalidator + invalidation policy documented *at the definition*. New cache = entry here + a hook in `invalidate_seg_caches` (or a surgical helper).
- **Cache serialized bytes, not the parsed object** (`_catalog_json_bytes`, `_PEAKS_RESPONSE_CACHE`) — skips re-`orjson.dumps` on warm hits; decisive on huge payloads. flask-compress negotiates per request on top.
- **Write-time peaks bake by slicing baked data** (`op_peaks.py`) — int8-slice the already-baked chapter peaks per op range; no ffmpeg, no float roundtrip; best-effort wrapper.
- **Persisted classifier fields** — pure-over-stable rules computed once at write, read as a dict lookup; single helper shared across writers, drift-gated.
- **`durable_transaction` / `deferred_sync`** — "no 200 without a durable bucket copy", but re-entrant (SAVEPOINT) and batchable; CAS guard reads the seq sidecar *from the snapshot itself* so bytes and label can't disagree.
- **Parallel-then-cache fan-out with self-caching loaders** — threads fetch independent reads; each loader caches its own result.

## Antipatterns / regrets / forward guidance

- **"Had we keyed bucket-file caches on a content version first,"** save wouldn't need the surgical pop/append choreography (`save.py:466-470`, `cache.py:314-355`). DB caches got db_seq self-invalidation free; bucket-file caches didn't, so invalidation is hand-maintained and fragile (the auto-split gate is the proof).
- **"Had we bounded `_PEAKS_CACHE` / `_AUDIO_CACHE_STATUS` first,"** they wouldn't be the two unbounded leaks in a bounded module.
- **`append_jsonl` unmounted is whole-file read-modify-write** — fine in prod (mounted), a cost cliff if the mount drops. Always note mount state when reporting save/append numbers.
- **Catalog rebuild compounds per concurrent visitor** (38 ms → 700 ms @100×, single-threaded). The three-layer cache exists precisely for this one anonymous concurrent path.
- **Wire-shape bloat is actively fought** (Migration #5 stripped `matched_text`, `phonemes_asr`, snapshot-only fields). Keep stripping.

## Profiling & measurement playbook (terminal/script)

**Storage bench — the only committed bench in-tree** (`scripts/diagnostics/bench_storage.py`). Cold (fresh `hffs` cache, `invalidate_hffs()` each iter) vs warm bucket read/write latency on real hot-path files, p50/p95/max + payload sizes:
```
INSPECTOR_BUCKET_REPO=QUD-Technologies/quranic-inspector-bucket-dev \
  python3 scripts/diagnostics/bench_storage.py [--mount /path/to/mount] [--skip-writes] [--warm N]
```
Run with **and** without `--mount` to quantify the mount-vs-`hffs.cat_file` gap before trusting any bucket-I/O number. Benches `state/`, catalog, `detailed.json`, `segments.json`, `edit_history.jsonl`, a TS shard, plus `list_dir`/`exists`/write/append.

**Validation drift/bench harness** (gitignored, outside the working tree — `bench/snapshot.py`, `bench/drift.py`, `bench/measure.py` + committed `bench/ground_truth/<slug>.json`; see `docs/reference/validation.md`). The drift gate asserts **byte-equivalent per-category output** vs the ground-truth snapshot across the WIP reciter set. Backfill scripts (`backfill_qalqala_letter.py`, `backfill_boundary_adj.py`, `backfill_deleted_basmala.py`) are themselves deterministic drift checks (stamp → validate against persisted fields → compare → promote only on byte-equal). **Any compute-placement / caching / parallelism / persisted-writer change must pass drift before landing.**

**cProfile / perf_counter** — no committed cProfile harness; `perf_counter` markers exist at `services/reference/tajweed.py` (phonemizer init). For ad-hoc work: wrap the **service** function (not the route — strip Flask/CDN variance) under cProfile, or add `perf_counter` deltas inside `validate_reciter_segments` / `save_seg_data` between phases.

**Discipline:** cold = first read after `invalidate_seg_caches` / process restart (post-save, first-load); warm = repeat (subsequent interactions) — they optimize differently (I/O vs CPU). Isolate Python CPU by pre-priming caches so no bucket read fires (this is how the peaks-bytes-cache decision was made — separating the 1.5–2 s encode CPU from I/O). Take numbers at loadavg < 1. Report before/after + a drift pass for any perf-sensitive path.

## Local-vs-HF

| | Local (`python3 app.py`) | Deployed (HF Space) |
|---|---|---|
| Server | Flask dev server | gunicorn-gthread **`-w 1`**, 16 threads |
| Bucket | dev bucket, `hf-mount` FUSE if on PATH else `hffs.cat_file` (~50–500× slower) | prod bucket, NFS mount |
| Concurrency | single user | concurrent anonymous + reviewers on one worker |
| Hardware | your box (usually faster) | 2 vCPU / 18 GB |

**Transfers in shape, not ms:** CPU-bound measurements (classify, catalog build, orjson encode) — your cores ≠ 2 vCPU. **Does NOT show locally:** single-worker serialization, catalog rebuild compounding across visitors — reason via "× N", not local wall-clock. **Deceptive locally:** unmounted local pays whole-file RMW on `append_jsonl` (will mislead save numbers); `hffs.cat_file` cold reads look slow but aren't a real regression. Always state your mount state.
