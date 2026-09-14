# scripts/

Every operational CLI for the repo, grouped by **function**. These are *invoked*
(by a maintainer or CI), never imported by the running app, and are pruned from
the deployed image by `.dockerignore`. Code that's *imported* at runtime is a
package — `qua_shared/` (library + schemas) or `qua_jobs/` (HF-Job entrypoints) —
not a script.

## Where does a NEW script go?

```
Imported by app/job runtime?                 → it's a package: qua_shared/ or qua_jobs/
Run ONLY inside a GitHub Action AND meaningless        → .github/scripts/
  standalone (reads $GITHUB_* / posts PR comments)?      (currently none)
Otherwise it's an operational CLI                      → scripts/<function>/ :
  deploy/       ship the app / build + boot the image
  devenv/       set up a contributor / fixtures
  codegen/      regenerate committed artifacts (FE types, rule catalogue, badges)
  bucket/       read/write the HF bucket (raw CLI + reciter round-trip)
  backfills/    re-runnable data catch-up / one-off data fixes
  diagnostics/  measure / assert (no mutation)
  migrations/   FROZEN one-shot schema moves (see migrations/README.md)
```

A file goes in `migrations/` **only if** re-running it on current state is a
no-op-or-error by design. Idempotent `backfill_*`/`purge_*`/`convert_*` stay in
`backfills/`.

## Catalogue

### `deploy/`
- `upload_inspector.py` — stage every git-tracked file → deploy the canonical dev/prod Space (CI + manual)
- `deploy_space.py` — deploy to any Space (contributor cousin)
- `smoke_boot.py` — build the image, boot it on fixtures, assert `/healthz` (CI boot gate)

### `devenv/`
- `setup.sh` — first-time local setup: install FE (`npm ci`) + BE (`pip`) deps so tests/build/lint run (`frontend`/`backend` to scope). Idempotent.
- `setup_worktree.py` — fast bootstrap for a linked git worktree: mirror `.env` + `node_modules` (root + `inspector/frontend`) from the main checkout instead of a full `npm ci`; falls back to `setup.sh frontend` if main has none. Used by the `wt` skill. Idempotent.
- `launch.py` — run the app any mode (dev bucket / offline fixtures / live dev|prod Space), any worktree, conflict-free: free ports, per-worktree SQLite isolation, readiness wait, `list`/`down`/`doctor`, and a Dashboard+Timestamps smoke. The `/launch` skill wraps it; `launch_smoke.mjs` is its headless-chromium check.
- `bootstrap_dev_env.py` — provision a contributor's personal bucket + Space
- `seed_fixtures.py` — download the public fixtures → `.fixtures` (offline tier-0)
- `make_fixtures_dataset.py` — maintainer: (re)build the PII-free public fixtures dataset
- `install_qua_domain.py` — install the pinned `qua-domain` edition package from `Hetchy/qua` via the `QUA_DOMAIN_DEPLOY_KEY` SSH key; exits 0 having installed nothing when no credential exists (Hafs-only runtime)
- `make_synthetic_delivery.py` — write a synthetic non-Hafs delivery (segs + optional word-profile shard, built from `qua_domain` itself) into a bucket, for multi-riwayah verification

### `codegen/`
- `regen_fe_types.py` — regenerate the FE TypeScript types from `qua_shared/schemas/` (CI-checked)
- `regen_tajweed_rules.py` — regenerate the 45-rule native catalogue from quranic-phonemizer 2.15.3
- `regen_waqf_render.mjs` — regenerate the per-QPC-face waqf-mark render calibration (`lib/utils/waqf-render-table.ts`) by measuring each mark's painted ink in headless Chromium; run FROM `inspector/frontend`
- `update_readme_badges.py` — regenerate the root README stats badges from the prod bucket (daily cron)

### `bucket/`
- `bucket_ls / stat / cat / put / rm / cp / sync / diff.py` — raw HF-bucket CLI toolkit (`--bucket dev|prod|aligner`, `--yes-prod` to mutate prod)
- `bucket_reciters.py` — one-row-per-reciter summary table
- `_bootstrap.py` — shared helpers (token, bucket ids, prod-write guard)
- `download_bucket_reciter.py` → `audit_bucket_reciter.py` → `upload_bucket_reciter.py` — reciter-folder round-trip (download → validate → upload)

### `backfills/`
- `backfill_*` (5), `convert_peaks_v2_to_v3`, `derive_pipeline_meta`, `rollback_peaks_slim` — re-runnable artefact backfills / conversions
- `purge_pad_migration`, `purge_stale_wraps` — strip stale records from `detailed.json` / `edit_history`
- `unignore_category` — bulk-revert `ignore_issue` ops (re-runnable data-fix)
- `patch_release_audio_sources` — rewrite a shipped GH release's `catalog.json` to native source URLs + `chapter_offsets_ms` (re-runnable; no-op once the `cut_release` fix shipped)

### `diagnostics/`
- `bench_storage.py` — benchmark backend read/write hot paths
- `check_eligibility_parity.py` — assert DB-backed eligibility == legacy git-tracked set

### `migrations/`
Frozen one-shot schema moves — see [`../docs/reference/data-migrations.md`](../docs/reference/data-migrations.md).
`migrate_timestamps_v13.py` is the local-only v12→v13 timestamp restamp/audit command; it writes a
parallel destination tree and has no bucket upload capability.
