# Inspector SQLite substrate (`inspector.db`)

A single container-local `inspector.db` (SQLite, WAL, single-writer) is the source of truth for what used to be 7 bucket-resident JSON stores (state, catalog, access, audit, activity/dismissals, claims, pending requests) + the audit log. Per-reciter content (`reciters/<slug>/`) stays JSON-on-bucket and is **not** in the DB. The DB is full-file synced to the HF bucket (`db/inspector.db`) — pulled at boot, pushed after every committed write under a `db_seq` compare-and-swap guard. The inspector runs single-worker gunicorn-gthread (16 threads); all writes serialize through one writer connection.

Code lives in `inspector/services/db/`. Public API is `inspector/services/db/__init__.py`: `init_db`, `healthcheck`, `current_db_seq`, `current_version`, `transaction`, `get_conn`, `get_writer`, `reset`, `db_path`, `set_db_path_for_test`, `run_migrations`.

DB path resolves from `INSPECTOR_DB_PATH` (else `<tempdir>/inspector.db`). `init_db()` opens the writer, runs pending migrations, chmods `0600` (POSIX; no-op on Windows). `:memory:` is supported (tests).

---

## Concurrency model

`inspector/services/db/connection.py`. Single-worker gunicorn, 16 threads.

| Aspect | Mechanism |
|---|---|
| Writer | ONE shared `sqlite3.Connection` (`_writer`), guarded by re-entrant `_WRITE_LOCK = threading.RLock()`. Autocommit (`isolation_level=None`); `transaction()` manages `BEGIN`/`COMMIT` explicitly. |
| Write txn | `transaction()` CM: top-level acquires `_WRITE_LOCK`, `BEGIN IMMEDIATE` on writer, yields conn, bumps `db_seq`, `COMMIT` (or `ROLLBACK` on any exception, then re-raise). |
| Readers | Thread-local connections (`_readers`), autocommit + `PRAGMA query_only = ON`. WAL lets them run concurrently with the writer. |
| Read-after-write | Readers hold no long-lived txn → every `SELECT` sees the latest committed WAL snapshot. No stale reads after a commit. |
| Re-entrancy | `transaction()` is savepoint-aware. Nested calls (active conn tracked via `_active` ContextVar) enroll in the active connection and use `SAVEPOINT sp_<depth>` (+ `ROLLBACK TO`/`RELEASE`), never a fresh `BEGIN`. Multi-repo handlers are one atomic unit. |
| Connection routing | `get_conn()` returns the active write-txn conn if one is open on this context, else the thread-local reader. Repos call `get_conn()` and work on whichever they get. |
| db_seq bump | `_bump_db_seq()` runs inside the top-level commit only (nested savepoints don't bump). Monotonic; CAS guard for bucket sync. |
| Pragmas | `foreign_keys=ON`, `busy_timeout=5000`, `journal_mode=WAL`, `synchronous=NORMAL`, `temp_store=MEMORY`; readers add `query_only=ON`. |
| Reset | `reset()` closes the writer + bumps `_generation` so each thread's reader lazily reopens. Tests + path switches only. |

**Invariant when adding a write:** every mutation MUST run through `transaction()` (or `sync.durable_transaction()` at a service boundary — see below). Never write on a reader connection (`query_only` will reject it; repos that need the writer call `get_conn()` *inside* an open txn). A handler that touches multiple repos opens ONE outer `transaction()` (or `durable_transaction()`); the inner repo calls nest as savepoints and commit atomically with it. Repo write functions do **not** open their own txn (except `repo_transitions.append`, which uses re-entrant `transaction()` so it nests under the caller).

---

## Table inventory

All from `inspector/services/db/migrations/0001_init.sql`. Datetimes are TEXT (ISO-8601 UTC, `Z` suffix); booleans INTEGER 0/1; JSON columns TEXT (orjson). No CHECK on free-vocabulary columns (`transitions.event`, `requests.kind`, `claims.close_reason`) — new kinds need zero migration; validity enforced in pydantic/app layer.

| Table | Purpose | Owning repo |
|---|---|---|
| `db_meta` | key/value runtime metadata; holds `db_seq` (seeded `'0'`) | `connection.py` (`_bump_db_seq`, `current_db_seq`) |
| `users` | HF identity + cached login + first/last seen | `repo_access` |
| `role_assignments` | maintainer/owner grants (active + soft-revoked); `ux_role_active` partial-unique = one active role per user | `repo_access` |
| `riwayahs` | catalog vocab: riwayah slug/short/name | `repo_catalog` |
| `styles` | catalog vocab: style slug/short/name | `repo_catalog` |
| `sources` | catalog vocab: source + `audio_categories` JSON | `repo_catalog` |
| `channels` | catalog vocab: channel + `host_patterns` JSON | `repo_catalog` |
| `recording_contexts` | catalog vocab: studio/broadcast/prayer/taraweeh/mixed | `repo_catalog` |
| `catalog_meta` | single row id=1: schema_version, generated_at, persisted `derived` JSON | `repo_catalog` |
| `catalog_aliases` | slug/reciter_id rename history | `repo_catalog` |
| `reciters` | reciter identity (name_en/ar, country, notes) | `repo_catalog` |
| `deliveries` | per-(reciter × riwayah × style × source × channel …) audio delivery; FKs into all vocab tables | `repo_catalog` |
| `transitions` | canonical append-only event log (replaces `audit/*.jsonl`); `seq` PK, unique `id`, `content_hash` | `repo_transitions` |
| `delivery_states` | current lifecycle projection per slug (state, visibility, last_save_at, timestamps_job_ids, revision_in_progress) | `repo_state` |
| `claims` | first-class claims (current + history); `ux_claim_open_slug` = one open claim per slug | `repo_claims` |
| `requests` | unified pending + 3 archives (status pending/accepted/returned/discarded); `ux_request_pending_slug` = one pending per non-null slug; evolving fields in `payload` JSON. `kind` ∈ `existing_combo_edit` (slug-based edit) \| `existing_reciter_new_combo` \| `new_reciter` (the latter two are **slugless** intake — `slug=NULL` until accept; `payload` carries `reciter_id`, `source`, `attestations`, cached `probe`; exempt from `ux_request_pending_slug`). | `repo_requests` |
| `request_views` | **unused** — was per-admin "viewed" marks for the retired Requests-tab unviewed badge, `(request_id, hf_user_id)` PK. "New request" awareness moved to the My Notifications rail (`request.received` alert); table left in place (no drop migration). Migration `0004` | — |
| `review_views` | **retained but unused** — was the per-admin marked-ready "viewed" mark for the Reviews-tab dot, `(slug, hf_user_id)` PK. The marked-ready notification was retired with the Releases restructure; `repo_review_views` is deleted and nothing reads the table. The frozen migration `0005` keeps the table; no reader. | — |
| `activity_tombstones` | owner-only global deletes for public-rail cards, keyed on `audit_content_hash`. Per-user dismissals (`activity_dismissals`) were dropped in migration 0006 alongside the retired admin notifications rail. | `repo_activity` |
| `guide_views` | per-user "read" marks for the validation-accordion help guides, `(view_key, hf_user_id)` PK, **write-once** (`INSERT OR IGNORE` — no unread transition). `view_key` is the collapsed guide id (`low_confidence_v2 → low_confidence`); no FK (app-level enum). Drives the FE unread `?` border + first-edit onboarding gate. Surfaced on `/api/me` as `guides_read`. Migration `0010` | `repo_guides` |
| `ts_reports` | Timestamps-tab reports (`audio`/`timing`/`tajweed`/`phonemes`/`silence`/`other`). Grid reports use exact native `(reading_id, target_kind, target_id)` identities and an authoritative `snapshot_json`; verse-level reports use a native verse target. One row per `(slug, verse_key, category, target_key, identity)` where identity is `hf_user_id` XOR `anon_token`; partial unique indexes split signed-in and anonymous identities. `status` is open/resolved, `hidden_at` is the soft-delete marker, and `stale` is set when a regenerated target changes. Migration `0028` | `repo_ts_reports` |
| `ts_bans` | Clients (IPv4 address / IPv6 /64) banned from `/api/ts/*` for pulling in bulk: `banned_until`, `strikes`, `last_strike` (Unix seconds), `reason`. Written once per ban, restored into the in-memory limiter after a restart. Migration `0034` | `repo_ts_bans` |
| `ts_verse_flags` | superseded by `ts_reports` — table retained until its few rows are manually migrated, then dropped in a follow-up migration. Migration `0024` | (no repo) |

Key indexes: `ix_transitions_slug_ts`, `ix_transitions_ts` (DESC), `ix_transitions_event`, `ix_transitions_hash`; `ix_delivery_states_state`; `ix_claim_open_assignee` (partial, open only); `ix_requests_status`, `ix_requests_slug`; `ix_request_views_user`, `ix_review_views_user`, `ix_guide_views_user`; `ix_deliveries_reciter`; `ix_role_user`.

Note: `assignee_*` / `marked_ready` are **not** columns on `delivery_states` — they live on the open `claims` row and are LEFT-JOINed in by `repo_state`.

---

## Repos

`inspector/services/db/repo_*.py`. Each owns its table(s) and assembles the legacy pydantic read model so the service/route/FE wire contract is unchanged. Write functions assume the caller already opened a `transaction()`.

| Repo | Table(s) | Key ops | Read model |
|---|---|---|---|
| `repo_state` | `delivery_states` (+ LEFT JOIN `claims`) | `get_row`, `all_rows`, `exists`, `upsert_state`, `update_state(**writable)` | `ReciterRow` (assignee/marked_ready filled from open claim; only on `under_review`) |
| `repo_catalog` | `riwayahs`/`styles`/`sources`/`channels`/`recording_contexts`/`catalog_meta`/`catalog_aliases`/`reciters`/`deliveries` | `snapshot`, `find_reciter`/`find_delivery`/`find_source`, `edit_reciter`/`edit_delivery`, `add_reciter`/`add_delivery`/`add_source` (raise `Duplicate`), `insert_*`, `load_vocab`, `insert_alias`, `set_meta`, `refresh_derived` | `ReciterCatalog` (full `model_dump(by_alias=True)` byte round-trips parity gate) |
| `repo_access` | `users`, `role_assignments` | `ensure_user`, `get_login`, `resolve_role`, `find_member`, `active_members`, `snapshot`, `grant_role`, `revoke_role` (soft), `update_role`, `has_any_active` | `Member` / `RolesFile` (snapshot = active + revoked) |
| `repo_transitions` | `transitions` | `append` (re-entrant txn), `get`, `get_by_content_hash`, `for_slug`, `since`, `feed` | `AuditRecord` (append) / audit-record-shaped dict (reads) — `ts` is exact stored string so `activity_classification.audit_id` recomputes `content_hash` |
| `repo_claims` | `claims` | `get_open_claim`, `open_claim_for_user` (O(1) one-claim check), `open_claims_for_user`, `open_claim`, `close_claim`, `set_marked_ready`, `reassign` | raw `sqlite3.Row` (consumed by `repo_state`) |
| `repo_requests` | `requests`, `request_views` | `submit`, `resolve` (→ accepted/returned/discarded), `delete_pending`, `get_pending`/`has_pending`/`all_pending`, `count_pending`, `all_archived(kind)`, `get_for_slug(kind, slug)`, `get_by_id`, `admin_list_rows(status)`, `counts_by_status`; views: `mark_viewed`/`is_viewed`/`viewed_ids_for_user`/`count_unviewed_open_for_user` | `PendingRequest` / `ArchivedRequest` (archive kind→status: completed→accepted, returned→returned, discarded→discarded) |
| `repo_activity` | `activity_tombstones` | `delete`/`undelete`/`is_deleted`/`deleted_set` (global tombstones for the public feed; owner-only writes) | `set[str]` keyed on `content_hash` |
| `repo_guides` | `guide_views` | `record_view` (INSERT OR IGNORE — write-once), `read_views` | `list[str]` of read `view_key`s |
| `repo_ts_reports` | `ts_reports` | `create(...) -> (row, created)` / `create_many(...)` (per-identity ON CONFLICT on native `target_key`), `verse_counts(slug)`, `list_for_verse`, `resolve(...)`, `list_open_for_recheck`/`mark_stale`, `delete(...)` | plain dicts (the route assembles the `TsReport` wire model) |

`repo_errors` (`errors.py`): `RepoError`, `Duplicate` (PK / partial-unique violation), `NotFound`. Services map these to their own contracts (e.g. catalog's `InvalidCatalogChange`) instead of leaking `sqlite3.IntegrityError`.

`one-claim-per-non-owner` is enforced in the transition layer (owners exempt by policy), NOT a DB index — `ix_claim_open_assignee` is non-unique. `ux_claim_open_slug` (unique) does enforce one open claim per slug.

---

## Migrations

`inspector/services/db/migrate.py`, scripts in `inspector/services/db/migrations/NNNN_*.sql`.

- Version tracked via `PRAGMA user_version` (NOT a table).
- `run_migrations(conn)` (called by `init_db`): `_discover()` globs `*.sql`, parses the 4-digit prefix (`^(\d{4})_.*\.sql$`), sorts ascending, applies every file with number > `user_version`.
- Each script is wrapped `BEGIN; <sql> PRAGMA user_version = NNNN; COMMIT;` and run via `executescript` — DDL + version bump are atomic. Failure rolls back and **aborts boot** (fail-fast → `/healthz` 503), never half-applies.
- Non-conforming filenames are logged and skipped.

**Adding a migration:** create `NNNN_<desc>.sql` (next 4-digit number) in `migrations/`. Write raw DDL/DML only — do NOT add `BEGIN`/`COMMIT`/`PRAGMA user_version` (the runner wraps it). It applies on next boot. Current head: `0035_quality_status.sql` (adds `delivery_states.quality_status`). Migration 28 is preceded at boot by the strict legacy-target map preparer and followed by a native-schema assertion.

> **Never edit an already-applied migration to change schema.** Existing DBs (every deployed/dev/local one is pulled from the bucket already past `user_version 1`) re-run nothing ≤ their version, so edits to `0001` etc. reach fresh DBs only and silently miss live data. New columns/tables ALWAYS go in a new `NNNN_*.sql` with an additive `ALTER`/`CREATE`. (0007 exists precisely because the `claims.mark_ready_*` columns were first inlined into `0001` and never reached the live `claims` table — the Reviews drawer detail query 500'd with `no such column`.)

---

## Bucket sync

`inspector/services/db/sync.py`. Bucket paths: `db/inspector.db` (the DB), `db/inspector.seq` (CAS sidecar), `db/inspector-<YYYY-MM-DD>.db` (daily snapshots, 30-day retention). Uses bucket primitives directly (`read_bytes_direct`/`write_bytes_direct`), bypassing the mount's debounced flush so an acked write is durable before the response.

**Boot pull** — `pull(dest)`: downloads `db/inspector.db` to the local path (default configured DB path), clears stale `-wal`/`-shm`, chmods `0600`. Returns False (fresh init) if the bucket has no DB. Always trusts the bucket.

**Push after commit** — `durable_transaction()` is the mutating service boundary (wraps `state.transition`, catalog/requests mutations, access grant/revoke/update, activity mutations). It opens `connection.transaction()`, and **only the OUTERMOST** boundary calls `mark_durable()` after the txn commits and the active-conn ContextVar clears (`snapshot()` requires no open txn). Nested `durable_transaction()` is a savepoint and does NOT upload.

- `snapshot()` → standalone DB bytes via SQLite online `backup()` (no WAL sidecar) + the `db_seq` read **from the snapshot itself**, so labelled seq always matches uploaded bytes. Raises if called inside an active write txn.
- `upload()`: snapshot → read remote `inspector.seq` → CAS check → write `inspector.db` then `inspector.seq` (`{seq, nonce, ts}`). Returns the uploaded seq.

**CAS guard** (`db_seq`): the per-process `_NONCE` (12 hex) identifies "our own prior upload" vs "another container raced us during a rolling deploy". Upload **refuses** (`UploadConflict`, → 5xx; local commit stays ahead, later upload reconciles) iff remote `seq >= local snap_seq` AND remote `nonce != _NONCE`. Caveat: bucket I/O has no atomic CAS, so this catches a *stale* racer, not two simultaneous writers — safe under the single-active-writer invariant; it covers the rolling-deploy overlap window.

**Batching** — `deferred_sync()` (ContextVar depth counter): boot-scan (`hydrate_initial_seen`) applies N transitions in a loop; this coalesces them into ONE upload on outermost exit (only if no exception **and the batch actually advanced `db_seq`** — a no-op batch, e.g. a boot scan that finds nothing stuck, skips the upload so it doesn't trip the equal-seq CAS guard against the previous container's nonce and log a spurious `ERR` on every restart). `mark_durable()` is a no-op inside it. `set_sync_enabled(False)` disarms uploads (tests; default armed in prod).

**`current_db_seq()`** (in `connection.py`) reads `db_meta.db_seq` — the monotonic counter the CAS guard compares. Bumped once per committed top-level write txn.

Daily snapshot: `daily_snapshot()` writes `db/inspector-<day>.db` and prunes any older than `_SNAPSHOT_RETENTION_DAYS` (30). `status()` (counters: `nonce`, `last_bucket_upload_ts`, `bucket_lag_seconds`, `last_error`, `queue`) backs `/healthz`.

---

## serde

`inspector/services/db/_serde.py` — row ↔ pydantic helpers.

| Helper | Behavior |
|---|---|
| `to_iso(dt)` / `from_iso(s)` | datetime ↔ ISO-8601 UTC string. `to_iso` emits a **`Z`** suffix (not `+00:00`) to match pydantic v2 `model_dump(mode="json")` byte-for-byte — stored `ts` and wire `ts` must be identical (activity card parity). |
| `json_dumps`/`json_loads` | orjson; `json_loads("")`/`None` → `None`. |
| `now()` | `datetime.now(timezone.utc)`. |
| `new_transition_id()` | `req_<12hex>` (mirrors old audit `request_id`). |
| `content_hash(ts, event, slug, actor_hf, result)` | `sha1(ts\|event\|slug\|actor_hf\|result)[:16]` from the exact stored strings. MUST stay byte-identical to `services.activity.activity_classification.audit_id` so dismissals/tombstones keep matching transitions. |
| `content_hash_for_record(record)` | same hash from a raw audit/transition dict (used by the JSON→SQLite migration on historical records). |

Repos round-trip these pydantic models from `qua_shared/schemas/` (enum values serialized via `.value`; the FE-facing subset re-exported at `qua_shared/schemas/fe_types.py`):

| Schema module | Models | Repo |
|---|---|---|
| `state.py` | `ReciterRow`, `ReciterState`, `Visibility`, `RevisionContext` | `repo_state` |
| `catalog.py` | `ReciterCatalog`, `Vocab`, `Riwayah`, `Style`, `Source`, `Channel`, `RecordingContext`, `ReciterEntry`, `Delivery`, `Alias`, `Derived` | `repo_catalog` |
| `access.py` | `Member`, `Role`, `RolesFile` | `repo_access` |
| `audit.py` | `AuditRecord`, `Actor` | `repo_transitions` |
| `pending_requests.py` | `PendingRequest`, `ArchivedRequest`, `ProposedEdits` | `repo_requests` |
| `activity_state.py` | `ActivityState` (migration read of legacy store) | `repo_activity` |

---

## JSON→SQLite migration

`scripts/migrations/migrate_json_to_sqlite.py` — one-shot, run once per bucket (`--bucket dev|prod`). Reads the 7 legacy stores + audit JSONL via the storage backend, decomposes into the SQLite tables, runs a parity readback, and (unless `--dry-run`) uploads `db/inspector.db`. Refuses to overwrite an existing bucket DB without `--force`. Refuses orphan slugs (state/request slug missing from catalog deliveries) without `--allow-orphans`.

Decomposition (`build()`, all in one `transaction()`; guards that target tables are empty first):

| Source | → Tables |
|---|---|
| `access` (RolesFile) | `users` + `role_assignments` (active + revoked) |
| `catalog` (ReciterCatalog) | vocab + `reciters` + `deliveries` + `catalog_aliases` + persisted `derived`/`generated_at` |
| `state` (ReciterStateFile) | `delivery_states` + a synthesized OPEN `claims` row for each `under_review` row with an assignee (+ `marked_ready` stamp) |
| `requests/pending.json` + 3 archives | `requests` (status pending / accepted / returned / discarded) |
| `audit/<YYYY>-<MM>.jsonl` | `transitions` (ts/id/content_hash preserved verbatim; `slug` NULLed if its delivery no longer exists, to satisfy the FK while preserving the event) |
| `activity/state.json` (legacy `deleted` list only) | `activity_tombstones` (keyed on content_hash). The legacy `dismissals` field is ignored — per-user dismissals were dropped with the admin notifications rail. |

**Parity gate** (`parity_check()`, → `SystemExit` on any issue): SEMANTIC parity (list order normalized — repos read slug-sorted), not raw byte-for-byte file diff. Checks:
- catalog `snapshot().model_dump(mode="json", by_alias=True)` == source dump (after `_norm_catalog` sorts vocab/reciters/deliveries) — the full-fidelity round-trip.
- active roles set match; full `role_assignments` count == source members.
- every in-catalog state row `model_dump` matches.
- pending slug set match; per-status archive row counts match.
- open claims set (slug, assignee) == `under_review` rows with assignee.
- activity tombstones set matches the migrated `deleted` list.
- `transitions` count == audit record count.

`run()` ordering: `read_sources` → `orphan_check` → `build` → `parity_check` → upload. `main()` sets `INSPECTOR_BACKEND=bucket` + `INSPECTOR_BUCKET_REPO`, `init_db()`, then `sync.upload()`.

---

## healthz

`healthcheck()` (`__init__.py`) — cheap, never raises:

```json
{ "open": true, "schema_version": <PRAGMA user_version> }
```

On any error: `{ "open": false, "error": "<first 200 chars>" }`. Sync counters from `sync.status()` (`bucket_lag_seconds`, `last_error`, `nonce`, `last_bucket_upload_ts`, `queue`) supplement the DB health on `/healthz`; a failed boot migration aborts startup → `/healthz` 503.
```
