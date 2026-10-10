---
name: inspector-performance
description: Inspector performance across the whole stack — single-worker backend (SQLite / caching / bucket-I/O / hot paths), frontend (bundle / render / wire-shape), and the live-stack runtime probing that produces the evidence (Playwright/Chrome MCP + Flask/Vite log correlation). The cost model, the measurement playbook, and the cautionary patterns.
---

# inspector-performance

Performance skill for the Inspector. Standalone — references split by layer. This replaces the old `performance.md` rule: same intent (a public web app on one worker, where per-request waste compounds across users), more depth, plus the runtime-probing methodology folded in.

## The cost model (internalize this before optimizing anything)

**Single-worker gunicorn `-w 1`, 16 gthreads, 2 vCPU / 18 GB HF Space.** Consequences that drive every decision:

- **Pure-CPU work serializes across all users.** A 1 s CPU spike (a big `orjson` dump, a pydantic catalog rebuild, an un-cached classify) blocks all 16 gthreads. Plan for sub-100 ms on per-request hot paths. The GIL means threads only help **I/O-bound** work (bucket reads, SSL recv release the GIL); CPU work does not parallelize.
- **Bucket reads are the slowest layer.** FUSE mount in prod; `hffs.cat_file` fallback is ~50–500× slower when unmounted. Treat each bucket read as expensive; fan out independent reads with `ThreadPoolExecutor`.
- **SQLite is the source of truth**, one serialized writer (`connection._WRITE_LOCK`), WAL readers concurrent. State transitions block on a synchronous full-DB upload to the bucket before acking (`durable_transaction`).
- **Aggregate is the real number.** A 100 ms regression × N concurrent users × M reads/session compounds. The arithmetic that matters is "× N", not the single-call wall-clock.

## What changed vs the old rule (don't trust stale framing)

- The old `performance.md` pointed at the `inspector-runtime-prober` agent as the probing entry point. That agent is gone — **`performance-expert` owns probing now**, and the methodology lives in `references/probing.md`.
- There is **no audio prefetch worker and no GC sweeper** (both removed). Don't reason about TTL purge or fetch-warming as live costs.

## Hot paths (where the time actually goes)

| Path | Dominant layer | Cold | Warm |
|---|---|---|---|
| Dashboard / public catalog load (the anonymous concurrent path) | **Python CPU** (pydantic catalog rebuild + orjson dump) | ~38 ms, projected ~700 ms @100× deliveries | dict handback (db_seq-keyed byte cache) |
| Segments validate (`/api/seg/validate`) | cold = **bucket I/O** (4 parallel reads); warm = **CPU** classify | 4-read fan-out | cache miss only after save |
| Peaks (`/api/seg/peaks`) | cold = **bucket I/O** (≤114 gzip shards, 8-way fan-out); warm = dict lookup | ~45 ms cold (mounted) | pre-serialized orjson bytes |
| Save (`save_seg_data`) | **bucket I/O** (4–5 serialized writes/reads) | — | — |
| Audio play (`audio-proxy`) | network / OS sendfile, ~0 Python CPU on the mount path | — | — |
| State transition | **bucket I/O** (synchronous full-DB upload, CAS-guarded) | — | — |

Details + file:line in `references/backend-db.md`.

## Reference index

| Reference | When to read | Key files |
|---|---|---|
| `references/backend-db.md` | Compute placement (write vs read), the full cache inventory + invalidation, I/O fan-out + concurrency, save/validate/catalog cost, the two unbounded caches, the bench/drift measurement playbook, local-vs-HF | `services/storage/cache.py`, `services/db/{sync,connection}.py`, `services/segments/save.py`, `services/validation/__init__.py`, `routes/segments/peaks.py`, `scripts/bench_storage.py` |
| `references/frontend.md` | FE critical path, bundle/chunk splitting, the two-layer tab gate, lazy panels, wire-shape, `$effect` hygiene, audio preload audits, the CSS/JS bundle regrets | `frontend/vite.config.ts`, `src/main.ts`, `src/App.svelte`, `tabs/*/stores`, `lib/refs/quran-refs.ts`, `lib/utils/peaks-decode.ts` |
| `references/probing.md` | Driving the live stack: bring-up commands, exact Playwright/Chrome MCP tool calls + `browser_evaluate` recipes, browser↔Flask-log correlation, ranked-summary discipline, local-vs-HF for probing | — |

## Method (the non-negotiables)

- **Measure before guessing.** cProfile / `perf_counter` markers / `bench_storage.py`. Numbers > intuition.
- **Cold and warm are different problems.** Cold = post-save / first-load (bucket I/O). Warm = subsequent interaction (CPU). Optimize them separately; never report one as the other.
- **Isolate Python CPU from wall-clock.** CDN/HTTPS/disk variance masks CPU. Profile the service function with caches pre-primed (no bucket read) to see the CPU floor.
- **Idle-system rule.** Take numbers with loadavg < 1; on one worker, background load skews everything.
- **Any change to a perf-sensitive path ships with: a drift check (byte-equivalent vs ground-truth snapshot), before/after numbers, and the idle-system caveat.** Compute-placement / cache / parallelism / persisted-field-writer changes especially.

## Cautionary patterns (the "had we done X first" core)

- **Cache invalidation that isn't keyed on a version is hand-maintained and fragile.** The DB caches self-invalidate on `db_seq`; the bucket-file caches don't, so their invalidation is a choreography in `save.py` that's easy to get subtly wrong (the auto-split "only when uid set changes" gate is exactly this). New cache → entry in `cache.py` + an explicit invalidation hook; never an ad-hoc module global.
- **Two caches are unbounded** (`_PEAKS_CACHE`, `_AUDIO_CACHE_STATUS`) — they grow per (reciter × url) for the process lifetime. Don't add a third; bound new caches (LRU / explicit eviction).
- **Recompute-on-read of a pure-over-stable-input value is waste.** Persist it at write-time behind one source-of-truth helper, drift-gated (qalqala). Asymmetric save-vs-read ownership is a smell.
- **Sequential bucket reads in a hot path** are a code smell — check whether each call truly depends on the prior one; fan out if not.
- **Wire-shape bloat compounds** (~25 B/row × thousands × many users). Strip unused fields aggressively; keep payloads slim (slim int8 peaks, `limit`-capped catalog).
- **Eager work the user may never need** — a tab's `onMount` fetch behind a `hidden=`-only gate, a `preload="metadata"` audio element mounted in a panel, a heavy dep in the main bundle. Defer mount (not just visibility), lazy-load panels, dynamic-import-gate heavy chunks.

## Relationship to `docs/reference/` (don't create drift)

This skill is a **performance lens + practices**, not a second copy of subsystem knowledge. The *what-is* ground truth lives in `docs/reference/` and wins on conflict:
- Cache-module mechanics, the invalidation rule, the route map → `docs/reference/architecture.md`
- SQLite substrate, repos, `sync.py`, db_seq → `docs/reference/database.md`
- Validation engine, persisted classifier fields, the bench/drift harness → `docs/reference/validation.md`
- FE SPA shell, stores, lazy-mount → `docs/reference/frontend.md`

What this skill *owns* (no reference doc does): the **cost model**, the consolidated **cache inventory** (perf lens — bounded/unbounded, hit/miss), the **hot-path cost map**, the **measurement/profiling playbook**, and the **runtime-probing methodology**. When `cache.py` / a hot path / a persisted-field writer changes, update the owning reference doc AND this skill's lens. Don't restate reference mechanics here beyond the one-line anchor needed to make a cost point.

## What this skill deliberately doesn't cover

Audio-subsystem internals (see the `inspector-audio` skill — `performance-expert` delegates audio-domain reasoning to `audio-expert`). Deployment/image build (see `docs/reference/config-deploy.md`). DB schema/migration mechanics beyond their perf characteristics (see `docs/reference/database.md`).
