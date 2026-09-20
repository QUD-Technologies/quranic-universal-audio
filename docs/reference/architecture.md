# Inspector backend architecture — START HERE

The Inspector is a single-worker Flask app serving a Svelte 5 SPA. It manages the lifecycle of Quran-recitation forced-alignment deliveries (catalogue → align → review → release → publish) and lets editors trim/split/merge/re-reference word-level segments. The deployed app is a Docker HF Space; its source of truth is a container-local SQLite DB (`inspector.db`) full-file synced to an HF bucket after every committed write. Per-reciter content (`detailed.json`, `segments.json`, `timestamps/<ch>.json`, peaks, audio) stays JSON/binary on the bucket, read on demand.

## Layering

```
HTTP ─▶ routes/<domain>/*          thin Flask blueprints: parse → service → jsonify
                │                   (primary Flask layer; only services/ exception is auth/auth.py)
                ▼
        services/<domain>/*        business logic, Flask-free, returns plain dicts
                │                   db/ storage/ audio/ auth/ state/ segments/
                │                   validation/ activity/ reference/
                ▼
   domain/  adapters/  utils/      pure model · JSON↔domain conversion · helpers

   services/storage/cache.py       single owner of every mutable cache var
   config.py · constants.py        env-overridable tunables · domain literals
```

Invariants:
- **Routes are the Flask layer.** Services accept params, return dicts, raise typed errors; `app.py` maps those errors to the `{error: str}` JSON envelope. The sole `services/` exception is `services/auth/auth.py` — authlib's `flask_client` OAuth integration (`_CacheStateOAuth`/`_CacheStateIntegration` subclass Flask integration) cannot be abstracted away. The `services-flask-free` CI guard enforces this with `auth.py` as the only allow-listed file.
- **Single worker.** `app.py::_assert_single_worker` refuses to boot under any multi-worker signal. State store, per-slug locks, role cache, OAuth-state cookie, and all in-memory caches assume one process.
- **SQLite is the source of truth** for the 7 former bucket JSON stores + audit. Reads go through `services/db/repo_*`; writes commit in `services/db/connection.py::transaction()` then sync via `services/db/sync.py`.
- **Bucket-first reads** for reciter content go through `services/storage/data_dir.py` + `storage_paths.py`, never raw `Path.read_text()`.
- **`services/__init__.py` re-exports** every submodule at the legacy flat name (`from services.cache import …` still resolves to `services/storage/cache.py`) via `sys.modules` aliasing. New modules drop into a subpackage + one re-export line.

## Services subpackage map

| Subpackage | Role | Key files |
|---|---|---|
| `db/` | SQLite substrate — source of truth for state/catalog/access/claims/requests/activity + transitions (audit). WAL single-writer, full-file bucket sync. | `connection.py` (txn primitives, `transaction()`, `get_writer`, `current_db_seq`), `migrate.py` (`PRAGMA user_version` runner over `migrations/NNNN_*.sql`), `sync.py` (bucket pull/push + CAS guard), `repo_state.py`, `repo_catalog.py`, `repo_access.py`, `repo_claims.py`, `repo_requests.py`, `repo_activity.py`, `repo_transitions.py`, `_serde.py`, `errors.py` |
| `storage/` | Bucket/filesystem backend, per-reciter content IO, the cache registry, local auto-mount, external-file validation. | `cache.py` (all cache vars + invalidation), `data_dir.py` (slug→bytes/dict under `reciters/<slug>/`), `storage_paths.py` (pure on-bucket path strings), `data_loader.py` (cached loaders for segs/verses/probe/reference), `hf_bucket.py` (`BucketBackend`/`FilesystemBackend`), `auto_mount.py` (local hf-mount FUSE), `bucket_audit.py` (Flask-free per-`reciters/<slug>/` artefact validator against `qua_shared/schemas`; backs the per-slug CLI `scripts/bucket/audit_bucket_reciter.py`, the whole-bucket gate `scripts/diagnostics/validate_bucket.py`, and `/healthz?deep=1` via `sample_validation`) |
| `audio/` | Everything bytes-on-disk → `<audio>`: source resolution, fetch, peaks, manifest. | `audio_source.py` (resolve bucket/CDN + VBR), `audio_fetch.py` (bucket audio + peaks reads), `audio_meta.py` (per-chapter sidecar), `peaks.py` (ffmpeg compute), `peaks_slim.py` (canonical schema-v3 packed format), `peaks_history.py` (per-op JSONL) |
| `auth/` | HF OAuth + signed identity cookie, roles, pure authorization predicates. | `auth.py` (OAuth + `inspector_session` cookie + dev-mode), `access.py` (roles facade over DB), `permissions.py` (pure predicates + `normalize_reason`), `predicates.py` (`/api/reciter-task` can-edit predicates), `hf_users.py` (login→hf_user_id proxy), `secrets_guard.py` |
| `state/` | Reciter lifecycle state machine, catalog, requests, audit facade. | `state.py` (`transition(slug, event, actor)`, `_HANDLERS`, typed errors), `catalog.py` (`snapshot()` → `ReciterCatalog`, mutations), `pending_requests.py`, `request_archive.py`, `audit.py` (legacy `append()` facade → `repo_transitions`) |
| `segments/` | Save / undo / read queries for the Segments editor, plus auto-detect + auto-split. | `save.py` (write-through, history append, rebuild segments.json), `undo.py` (batch reversal + snapshot verify), `segments_query.py` (read-only data), `auto_detect.py` (reconciler: `reciters/` dir → `alignment_completed`), `auto_split.py` (precomputed cursor lookup), `qalqala.py` (persisted `qalqala_letter`) |
| `validation/` | Registry-backed segment validation + chapter counts (Flask-free). | `registry.py` (`IssueRegistry`, category sets — keep in lockstep with FE `registry.ts`), `classifier.py`, `snapshot_classifier.py`, `detail.py`, `_missing.py`, `_structural.py`; package `__init__.py` exposes `validate_reciter_segments` |
| `activity/` | Audit-event classification + public activity feed + history queries + stats. The admin notifications rail was retired — admin awareness lives in the Admin dashboard tabs now. | `activity_classification.py` (event → public/hidden), `activity_state.py` (global-tombstone facade), `public_activity.py`, `history_query.py` (edit-history read + split-group/resolved-by-edit indexes), `stats.py`, `search_normalize.py` (mirrors FE `normalizeArabic`) |
| `reference/` | Static Quran reference payloads + timestamps server + public dashboard mapper. | `quran_refs.py` (DK words + verse word counts, one immutable asset), `timestamps.py` (manifest + per-chapter shard server, gzip LRU — shard schema in [shards.md](shards.md)), `public_state.py` (ReciterRow → six-bucket public taxonomy, strips identity) |

## Routes registry

Blueprints register in `routes/__init__.py::register_blueprints`. Subpackage `__init__.py` files are empty; each module defines its own `Blueprint`.

| URL prefix | Blueprint | Module | Purpose |
|---|---|---|---|
| `/healthz`, `/readyz` | `health` | `routes/auth/health.py` | Liveness/readiness; reports SQLite + bucket-mount status (503 when degraded) |
| `/api` (`/api/auth/*`) | `auth` | `routes/auth/auth.py` | HF OAuth login/callback/logout; sets `inspector_session` identity cookie |
| `/api/dev` | `dev` | `routes/auth/dev.py` | Dev-only: flip `inspector_dev_role` cookie (404 when dev mode off) |
| `/api` | `claims` | `routes/claims/claims.py` | Claim/release/mark/unmark + `/api/reciter-task/<slug>` (via `state.transition`) |
| `/api` | `requests` | `routes/claims/requests.py` | Request flow — user submit + admin review/reject/undiscard |
| `/api/public` | `public` | `routes/public/public.py` | Public read-only dashboard data (identity-redacted) |
| `/api/static` | `static_data` | `routes/public/static.py` | Static projections (`catalog.json`, `quran-refs.json`, …) for the FE |
| `/api/admin/access` | `access_admin` | `routes/admin/access.py` | Role grant/revoke/update (mirrors `services/access.py`) |
| `/api/admin` | `admin_actions` | `routes/admin/actions.py` | Admin override transitions (force-release, reassign, force-set-state, send-back) |
| `/api/public/activity` | `public_activity_admin` | `routes/admin/activity.py` | Owner-only DELETE — global tombstone for a public-feed card |
| `/api/admin` | `admin_reconcile` | `routes/admin/reconcile.py` | Manual trigger for the auto-detect reconciler |
| `/api/ts` | `ts` | `routes/timestamps/timestamps.py` | Timestamps tab: `/manifest`, `/shard/<reciter>/<chapter>`, `/config` |
| `/api/seg` | `seg_data` | `routes/segments/data.py` | Segments tab read-only data (`/all`, refs, …) |
| `/api/seg` | `seg_edit` | `routes/segments/edit.py` | Segments save/undo (`@require_same_origin` → `@require_edit_lock`) |
| `/api/seg` | `seg_val` | `routes/segments/validation.py` | Validate, stats, edit-history |
| `/api/seg` | `peaks` | `routes/segments/peaks.py` | `/peaks`, `/segment-peaks`, `/history-peaks` |
| `/api/seg` | `audio_proxy` | `routes/audio/proxy.py` | `/audio-proxy/<reciter>` — bucket-resident audio streamed by `routes/audio/range_file.py` (1 MB reads; NOT `send_file`, whose Range path reads the NFS mount 8 KB at a time → ~150 KB/s); CDN **stream-through** fallback (same-origin 200/206 + ACAO, not a 302) |
| `/api/audio` | `audio_meta` | `routes/audio/metadata.py` | Audio tab metadata |
| `/api/seg` | `segment_clip` | `routes/audio/clip.py` | `/segment-clip` — ffmpeg MP3 window clip (VBR-safe seek) |
| `/` + static | (Flask static) | `app.py` | SPA shell `frontend/dist/index.html`; `/api/surah-info` cross-tab route |

`_admin_helpers.py` (package root, not a blueprint) is shared by `admin/` + `claims/` routes.

## domain / adapters / utils

`inspector/domain/` — pure model, no Flask, no IO:

| File | Role |
|---|---|
| `segment.py` | Frozen `Segment` dataclass — one aligned segment as stored in `detailed.json` |
| `command.py` | `SegmentPatch` — change set produced by a command; undo applies its inverse |
| `identity.py` | Deterministic `segment_uid` (uuid5 over `NAMESPACE_INSPECTOR`); must match the TS impl |

`inspector/adapters/` — JSON ↔ domain conversion:

| File | Role |
|---|---|
| `detailed_json.py` | `detailed.json` entries list ↔ segment dicts with backfilled UIDs (`load_entries`) |
| `segments_json.py` | Rebuild verse-aggregated `segments.json` from detailed entries |
| `save_payload.py` | Incoming save payload → canonical segment dicts (`make_seg`) |

`inspector/utils/` — pure utilities + cross-cutting decorators:

| File | Role |
|---|---|
| `decorators.py` | `require_same_origin` (CSRF), `require_edit_lock` (signed-in + active claim + editable, `admin_bypass`) |
| `json_response.py` | `orjson_response` — fast JSON for big-payload routes |
| `arabic_text.py` | Strip Quranic decoration, last-letter extraction |
| `references.py` | Reference-string parsing + segment classification helpers |
| `repetitions.py` | Repetition-segment helpers over `wrap_word_ranges` |
| `formatting.py` | Display formatting |
| `io.py` | Atomic writes + safe filenames |
| `uuid7.py` | UUIDv7 (time-ordered) generator |

## app.py boot sequence

Runs at module import (so `python3 inspector/app.py` and `gunicorn inspector.app:app` follow the same path):

1. Insert repo root on `sys.path` (resolves `qua_shared.*`).
2. `_load_dotenv_for_local_dev()` — hydrate env from `<repo>/.env` then `inspector/.env` (process env wins).
3. Auto-enable `INSPECTOR_DEV_MODE=1` when not behind proxy and not under pytest.
4. `auto_mount()` — local hf-mount FUSE of the bucket (silent degrade to API path).
5. `_configure_logging()` — plain single-line formatter; silence httpx/urllib3/hf noise.
6. `_assert_single_worker()` — abort on any `-w >1` / `WEB_CONCURRENCY` / `GUNICORN_*` signal.
7. Build `Flask(static_folder=frontend/dist)`; wrap with `Compress` (gzip/brotli).
8. `ProxyFix` if `INSPECTOR_BEHIND_PROXY=1`; set session cookie SameSite/Secure accordingly.
9. `app.secret_key = get_session_secret()` (warns, doesn't crash, if missing).
10. `auth_service.init_oauth(app)` — register HF OAuth provider with Authlib.
11. `register_blueprints(app)` — all route blueprints.
12. `_boot_substrate()` (skipped under pytest): `sync.pull()` bucket DB → local → `init_db()` (open writer + run migrations) → `auto_detect.hydrate_initial_seen()` boot-scan inside `deferred_sync()` (one coalesced upload). Then the opt-in auto-detect loop (`INSPECTOR_AUTO_DETECT=1`). Deployed-mode init failure leaves app importable with `db_open:false`; local failure raises.
13. Register error handlers (`HTTPException`, `UnknownReciter`, `InvalidTransition`, `NotAuthorizedForTransition`, catch-all) → `{error: …}` envelope.
14. Register `/` (SPA shell) + `/api/surah-info`.
15. `if __name__ == "__main__"`: parse `--port`, run dev server (debug/reloader only under `FLASK_ENV=development`).

No eager warms of reciter data — timestamps + per-reciter caches are lazy on first request.

## Caching

All mutable cache vars live in `services/storage/cache.py` (`services.cache`); no `global` for caches elsewhere. Classes: `_SingletonCache` (one nullable value), `_KeyedCache` (LRU dict, ceiling `_KEYED_CACHE_LRU_MAX=20`).

- **Eager (singletons, immutable post-boot):** word counts, single-word verses, QPC/DK data, surah-info-lite — filled on first use, never invalidated.
- **Lazy per-reciter (LRU-bounded):** segments, seg meta/verses, probe-v2, auto-split, pipeline-meta, history batches, split-group index, validate/stats results, audio manifest.
- **db_seq-keyed:** `public_reciters` and `catalog_snapshot` cache on the monotonic `current_db_seq()` — any committed DB write bumps the seq and transparently invalidates them (no per-mutation hook).
- **Peaks:** `_PEAKS_RESPONSE_CACHE` (global LRU, 50 entries, serialized JSON bytes), thread-safe with explicit locks.

Invalidation hook: `invalidate_seg_caches(reciter)` runs on every save/undo. The surgical variant `pop_seg_caches_affected_by_segment_edit` preserves append-in-place caches (history batches, split-group index) and immutable ones (pipeline-meta). The chapter-peaks LRU (`_PEAKS_RESPONSE_CACHE`) is **deliberately NOT** evicted on save — peaks track immutable audio bytes, not segment edits; it sheds only via its own 50-entry LRU and an explicit `pop_reciter_peaks_response_cache` wherever a future path rewrites bucket peaks. Add a new cache here with its invalidation tied to the mutation that dirties it.

## Reference index — which sibling doc per task

| Doc | When to read |
|---|---|
| `database.md` | Working on the SQLite substrate — schema, migrations, repos, WAL/sync, CAS |
| `state-machine.md` | Lifecycle states, events, `transition()` handlers, admin overrides |
| `auth-permissions.md` | OAuth, identity cookie, roles, predicates, edit-lock/CSRF gating |
| `catalog.md` | Reciter taxonomy, `ReciterCatalog`, audio URL templates, accept flow |
| `segments-editor.md` | Save/undo flow, segment domain model, adapters, auto-split |
| `validation.md` | Validation registry, classifier categories, accordion lockstep with FE |
| `frontend.md` | Svelte 5 SPA — stores, tabs (dashboard/timestamps/segments), editGate |
| `accordion-guides.md` | Per-category guide copy for the validation accordion |
| `config-deploy.md` | Env vars, runtime profiles (dev/dev-Space/prod), HF Space deploy |
| `data-migrations.md` | Schema/data migration procedures, backfill scripts, drift checks |
