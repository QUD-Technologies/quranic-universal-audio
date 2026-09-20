# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What

Public website for Quranic Universal Audio. Tabs: **Dashboard** entry view for users and admin controls; search and browse the catalog, **Timestamps** (waveform, anlaysis and visualisation) for published reciters, **Segments** full editor for WIP reciters.

## Runtime profiles

Same code, two profiles selected by env:

| | **Dev** (`python3 inspector/app.py`) | **Deployed** (HF Space, gunicorn) |
|---|---|---|
| Identity | `INSPECTOR_DEV_MODE=1` (auto-set when OAuth unconfigured) → synthetic per-role user, OAuth bypassed | HF OAuth → 1-week signed cookie |
| State + catalog + access + audit + activity + claims + requests | SQLite `inspector.db` (`services/db/`), full-file synced to bucket `db/inspector.db` | same |
| Per-reciter content | bucket `reciters/<slug>/` via `services/storage/data_dir.py` | same |
| Bucket access | auto-mounted via `hf-mount` FUSE at `inspector/.bucket/{dev,prod}/` (gitignored) if the binary is on PATH; otherwise falls back to `hffs.cat_file` (~50-500× slower reads). Opt out with `INSPECTOR_AUTO_MOUNT=0` | NFS mount provided by the HF Space runtime |
| Audio | URL-templated via catalog, proxied through `/api/seg/audio-proxy/` (streams from CDN when bucket has no chapter) | same; bucket audio is populated offline by katana extraction (`.local/extraction/upload_to_bucket.py`) |
| Workers | flask dev server | gunicorn-gthread, **`-w 1`** (single-worker invariant) |

The prod Space is the only thing that uses `hetchyy/quranic-inspector-bucket` (prod) — it sets `INSPECTOR_BUCKET_REPO` explicitly. Every **non-deployed** process (local dev, scripts) defaults to the dev bucket `hetchyy/quranic-inspector-bucket-dev`.

Contributors get their own isolated bucket/Space via `scripts/devenv/bootstrap_dev_env.py`, or run fully offline against fixtures via `scripts/devenv/seed_fixtures.py` (`INSPECTOR_BACKEND=filesystem`). See `inspector/README.md` for the three-tier dev workflow.

Localhost for quick development with Vite and access to bucket, dev space for verifying deployed works same as local on HuggingFace, and prod space for the real deal.

`/healthz` reports `bucket_mounted`, `state_loaded`, `reciters_count`, `oauth_configured` and returns 503 in deployed mode when degraded.
Can access from `HF_TOKEN` in `.env`  

## Commands

**First-time setup (fresh checkout / ephemeral container): run `scripts/devenv/setup.sh`** to install FE (`npm ci`) + BE (`pip`) deps before running tests/build — then use the pinned `npm run *` scripts below, never `npx`/`npm exec` (they can pull a mismatched vitest).

| Task | Command |
|---|---|
| First-time setup (FE + BE deps) | `scripts/devenv/setup.sh` (`frontend` / `backend` to scope) |
| Build frontend | `cd frontend && npm install && npm run build` |
| **Run the app (any mode/worktree, conflict-free)** | `python scripts/devenv/launch.py` (the `/launch` skill) — fully local (your branch's backend + Vite, no Space proxy). Defaults to **dev** (DEV bucket, read-write, audio + analysis, works on Windows via hffs); `--mode prod` for a read-only look at PROD data (write-back disarmed); `--mode fixtures` for fully offline. Free ports, isolated DB, readiness wait, `list`/`down`/`doctor`. Prefer this over hand-running the two below. |
| Run server (dev) | `python3 inspector/app.py` → http://localhost:5000 |
| Frontend HMR | `cd frontend && npm run dev` → http://localhost:5173 (proxies `/api`) |
| Frontend test / typecheck / lint | `npm run test` / `npm run check` / `npm run lint` |
| Backend tests | `cd inspector && python -m pytest tests/ -v` |
| Deploy dev Space | push to `dev` → `inspector-deploy.yml` |

## Repo shape

Top level — code that is neither the app nor the frontend:

| Path | What |
|---|---|
| `qua_shared/` | Shared runtime **library** + Pydantic schemas — imported by the app at runtime AND by HF jobs, shipped in the image, the source of the codegen'd FE types + the authz capability registry. A package, not scripts. |
| `qua_jobs/` | HF-Job **entrypoints** (cut_release, publish_hf, generate_timestamps, shard) — staged to the job bucket, run remotely as `/aux/code/qua_jobs/X.py`. |
| `inspector/` | The Flask app (tree below). |
| `scripts/` | All operational CLIs, grouped by function — `deploy/` `devenv/` `codegen/` `release/` `bucket/` `backfills/` `diagnostics/` `migrations/`. Invoked, never imported; pruned from the image. |
| `.github/` | Workflows + `config/` only. CI invokes the tools under `scripts/`. |

```
inspector/
├── app.py            Flask factory, ProxyFix, single-worker guard, SQLite boot (pull + migrate)
├── config.py         Env-overridable tunables (paths, timeouts, thresholds)
├── constants.py      Domain literals (validation categories, muqattaat, qalqala)
├── routes/           Thin Flask blueprints (subpackages: admin/ audio/ auth/ claims/ public/ segments/ timestamps/)
├── services/         Business logic, Flask-free (except auth/auth.py authlib glue) — subpackages: db/ storage/ audio/ auth/ admin/ state/ segments/ validation/ activity/ reference/ ts_reports/
├── domain/           Pure model — Segment, SegmentCommand, identity
├── adapters/         JSON ↔ domain conversion
├── utils/            Pure utilities + cross-cutting decorators
├── tests/            pytest tree
└── frontend/         TS + Vite + Svelte 5 SPA → dist/ served by Flask
```

Frontend: `lib/` is cross-tab only. Tab work lives under `tabs/{tab}/`, each self-contained.

## Bucket shape

```
quranic-inspector-bucket/
├── db/
│   └── inspector.db             
├── catalog/
│   ├── reciter_catalog.json     # READ-ONLY legacy backup
│   └── audio_manifest/
│       └── <slug>.json          # Per-reciter audio manifests
├── reciters/
│   └── <slug>/                  # ALL per-reciter content
│       ├── detailed.json        # segments breakdown
│       ├── audio/<ch>.mp3       # Chapter audio 
│       ├── peaks/<ch>.json.gz   # Waveform peaks
│       ├── timestamps/<ch>.json.br # Compact native v13 readings + timing sidecars
│       └── ...       
└──           
```

## Design Langauge 

See `PRODUCT.md` and `DESIGN.md` when designing or doing UI work, alongside the `impeccable` skill.

## Where to look

Deep, agent-facing reference docs live in `docs/reference/` (flat). **Read the one matching your task on demand** — don't preload them. Index: [`docs/reference/README.md`](docs/reference/README.md).

| Doc | Open when working on |
|---|---|
| [`architecture.md`](docs/reference/architecture.md) | backend layering, `services/`+`routes/` subpackage map, caching, app.py boot |
| [`database.md`](docs/reference/database.md) | the SQLite substrate — repos, migrations, bucket sync, db_seq CAS |
| [`state-machine.md`](docs/reference/state-machine.md) | lifecycle states, flags, transition matrix, events |
| [`auth-permissions.md`](docs/reference/auth-permissions.md) | OAuth identity, roles, predicates, edit-lock, CSRF, admin endpoints, activity rails |
| [`notifications.md`](docs/reference/notifications.md) | per-user "My Notifications" Dashboard rail — `notifications` table, `services/notifications` emitter, event→target resolver, dismiss/archive, `/api/me/notifications` |
| [`ts-reports.md`](docs/reference/ts-reports.md) | Timestamps reader reporting — `ts_reports` table, word-grouped timing vs per-cell tajweed vs per-gap silence (pause boundary/waṣl/missed), batch create/group-resolve/soft-delete/staleness + silence auto-resolve-on-regen, FE drop-up + in-grid report mode (cell + pause-gap flagging, tajweed spotlight, missed-pause slots, persisted public flags) |
| [`capabilities.md`](docs/reference/capabilities.md) | data-driven capability authz — resolver, override store, Permissions tab, **convention for adding a gate that surfaces in the UI** |
| [`admin-dashboard.md`](docs/reference/admin-dashboard.md) | admin modal — Users compartment, owner-only role picker, `/api/admin/*` |
| [`catalog.md`](docs/reference/catalog.md) | reciter catalog — layers, slug convention, audio manifests, naming guide |
| [`audio-metadata-pipeline.md`](docs/reference/audio-metadata-pipeline.md) | audio metadata generation/probing/auditing/backfill — VBR & phantom-tail caveats, source-probe pitfalls, bucket/mount, the diagnostics+backfills tooling, new-source/channel runbook |
| [`segments-editor.md`](docs/reference/segments-editor.md) | command grammar, normalized state, identity, save flow, edit_history, undo |
| [`editions.md`](docs/reference/editions.md) | multi-riwayah — the four editions, slug vocabularies, the `qua-domain` dependency, coordinate projection, per-surface changes, the word-profile shard |
| [`validation.md`](docs/reference/validation.md) | validation engine, categories, persisted classifier fields, drift gates |
| [`frontend.md`](docs/reference/frontend.md) | Svelte 5 SPA — dashboard/timestamps/segments tabs, lib, stores, charts |
| [`theming.md`](docs/reference/theming.md) | light/dark themes — token contract (`tokens.css` + `theme-light.css`), flash-free init, `themeStore`+toggle, `canvas-theme.ts` resolver, theme-conditional accent-derivation, the no-raw-color rule |
| [`i18n.md`](docs/reference/i18n.md) | frontend i18n (Paraglide JS 2.x) — split-by-area message files + flat prefixed keys, compile/build-order wiring, locale rune + dir/lang plumbing, generated-output exclusions, do/don't-translate boundary, verification; strings-only now, RTL flip is a later workflow |
| [`keyboard-shortcuts.md`](docs/reference/keyboard-shortcuts.md) | Segments user-editable, context-scoped keyboard shortcuts — catalogue, binding store, dispatcher, active-row registry, accordion nav/auto-scroll, footer rebind popover |
| [`accordion-guides.md`](docs/reference/accordion-guides.md) | validation accordion help-modal guide templates |
| [`dataset-and-releases.md`](docs/reference/dataset-and-releases.md) | dataset releasing — bucket-as-canonical + 3 adapter formats (HF, GH release tiers, future API), `releases` table, publish state model, schema |
| [`align-pipeline.md`](docs/reference/align-pipeline.md) | the one-click native align pipeline — Requests-tab Align button, `align_runs` + worker threads, acquire HF Job → aligner Space batches → sidecars → in-process assemble, `adapt` parity with the Katana post-process, resume/retry/cancel, env |
| [`automation.md`](docs/reference/automation.md) | owner-configurable release automations — the opt-in reconciler daemon (auto-gen TS / GH cut / HF batch-publish / stale-TS regen / stale-metadata refresh), config blob + state tables, `release.manage_automation` gate, Releases-tab Automation card |
| [`config-deploy.md`](docs/reference/config-deploy.md) | env vars, secrets, image build, deploy, healthz |
| [`data-migrations.md`](docs/reference/data-migrations.md) | one-shot migration/backfill scripts |
| [`schemas.md`](docs/reference/schemas.md) | the shared `qua_shared/schemas/` Pydantic layer — bucket/wire/config folders + `extra=` policy, FE-types codegen pipeline, wire-model route contract (`ErrorEnvelope`/`OkAck` + response snapshots), tricky-shape cookbook, round-trip tests, external-file validation harness |
| [`api-roadmap.md`](docs/reference/api-roadmap.md) | **roadmap (not built)** — planned public data API: typed pip/npm SDKs over static CDN data + optional HF-Space compute layer; caching/versioning model + build sequence |

**Keep references current.** They are the *what-is* contract: when you change a subsystem, update its reference doc in the same change (code wins on conflict — fix the doc to match). **Add a new reference** when a new surface/subsytem/feature/convention appears that an agent would need to get oriented — and add a one-line row to the table above + `docs/reference/README.md`.

**Do NOT ignore these references** They are meant to help you. Do not jump straight to deep code explorations and waste tokens when the reference likely has the answer and helps provide an initial direction.

## Invariants

Cross-cutting rules to respect before touching anything. Depth is in the reference docs — these are the don't-break-this list.

- **SQLite is the source of truth.** `state`/`catalog`/`access`/`audit`/`activity`/`claims`/`requests` live in one container-local `inspector.db`, synced full-file to the bucket after each commit. Per-reciter content (`detailed`/`segments`/`timestamps`) stays in bucket files. → [`database.md`](docs/reference/database.md)
- **Single-worker.** The SQLite writer, per-slug locks, and role cache all assume one process; boot aborts on any multi-worker signal.
- **All state changes go through `transition()`.** Lifecycle moves are validated + audited there, never mutated ad-hoc. → [`state-machine.md`](docs/reference/state-machine.md)
- **One source for authz = the capability resolver.** Tier authorization is data-driven: every gate routes through `services/auth/capabilities.py::can()` against the registry in `qua_shared/schemas/config/capabilities.py`; an owner toggles capabilities per tier from the **Admin → Permissions** tab. A new gate = register a `Capability` + gate via `can()` / `@require_capability` / `_require_capability`  → [`capabilities.md`](docs/reference/capabilities.md), [`auth-permissions.md`](docs/reference/auth-permissions.md)

## Conventions

- **Clean imports** — services are Flask-free except `services/auth/auth.py` (authlib's `flask_client` OAuth integration is irreducible); routes are thin (parse → service → jsonify). No NEW Flask/authlib import under `services/` without extending the allow-list in the `services-flask-free` CI guard + `docs/reference/architecture.md`.
- **Cache via getter/setter** — `services/storage/cache.py` owns all cache variables; no `global` outside it.
- **Registry-paired validation** — accordion order in `services/validation/registry.py` + `tabs/segments/domain/registry.ts` must stay in lockstep.
- **Capability-gated permissions** — every permission gate routes through `can()` against the `CAPABILITIES` registry (`qua_shared/schemas/config/capabilities.py`); register a `Capability` and it auto-surfaces in the owner Permissions tab (the matrix is fully data-driven — no FE edit). Never add a new gate with hardcoded `is_maintainer()`/`@require_role`. → [`capabilities.md`](docs/reference/capabilities.md)
- **Actor on every edit** — save / undo / state-transition records carry `{hf_user_id, login_at_time, role}`; `login_at_time` is the cookie snapshot, never refetched.
- **Bucket-first reads** — anything touching reciter content or store JSON goes through `services/storage/data_dir.py` / `services/storage/storage_paths.py`, not raw `Path.read_text()`.
- **Svelte 5 for new code, Svelte 4 is legacy** — every new component, store, or `.svelte.ts` module is written with runes (`$state`, `$derived`, `$effect`, `$props`, `$bindable`) and callback-prop events. Pre-existing Svelte 4 files (`export let`, `$:`, `createEventDispatcher`, `on:`-directives, `<slot>`) keep working in legacy mode and are migrated either opportunistically when touched for a feature, or in dedicated batches per `docs/planning/svelte-migration.md`. Do not mix legacy syntax and runes in the same file — a file is fully one or the other. `TimestampsWaveform.svelte` and the canvas/audio-imperative components are deliberately exempt from migration; see the plan doc for the indefinitely-legacy list.
- **Token-driven theming, no raw colours** — new UI consumes `var(--token)`; every token already has a dark AND light value, so a new surface themes for BOTH automatically. A genuinely new colour = add it to `tokens.css :root` (dark) *and* `styles/theme-light.css` (light); a 2D-canvas colour = resolve via `themeColor()` + repaint on `themechange`. Never hardcode a hex/rgba/oklch in component CSS/`<style>`/canvas (dark is the default, so a hardcoded colour silently ships fine in dark and breaks light). → [`theming.md`](docs/reference/theming.md)
- **Localisation** — all strings in the FE are i18n'd via Paraglide JS and should word for Arabic.
- **Shared schemas live in `qua_shared/schemas/`** (Pydantic v2), split into `bucket/` (external bucket-resident JSON), `wire/` (HTTP request/response), and `config/` (SQLite-backed state/config) by where the shape lives. `extra="forbid"` is the default everywhere, including `bucket/` artefacts — post-migration the on-disk data is clean, so an unknown/legacy field raises `ValidationError` (no `strip_and_warn` tolerance). `extra="allow"` is used only for documented forward-compat exceptions (`ts_validation`, `playlist_map`, `ts_shard`'s `_meta`) where a round-trip fixture proves an unknown field rides through unchanged. Both the offline extraction pipeline and the Inspector save flow MUST round-trip the per-reciter artefact shapes (`detailed.json` segs, `edit_history.jsonl` batches/ops, `edit_history_peaks.jsonl` records) through these models — never construct dict literals at the writer site. The slim FE-facing subset is re-exported at `qua_shared/schemas/fe_types.py`. → [`schemas.md`](docs/reference/schemas.md)
- **FE types are codegen'd, never hand-edited** — `inspector/frontend/src/lib/types/generated/schemas.ts` is autogenerated by `scripts/codegen/regen_fe_types.py` (Pydantic → JSON Schema → TypeScript via `pydantic-to-typescript`). After touching anything under `qua_shared/schemas/`, run the script and commit the result. CI's `schema-codegen-check` job runs the same command and fails the build via `git diff --exit-code` if the committed file is out of sync. See `docs/reference/data-migrations.md` (Migration #5) for the rationale (writer/reader drift root cause).
- **The Inspector rule catalogue is generated, presentation policy is not** — `scripts/codegen/regen_tajweed_rules.py` pins quranic-phonemizer 2.15.3 and writes the producer-owned 45-rule names/summaries to `timestamps/data/rules.json`. Inspector grouping, colors, defaults, duration labels, and stacks remain explicit in `tajweed-rules.ts`; neither belongs in the shard schema.
- **Scripts live in one `scripts/` home, grouped by function** — operational CLIs go under `scripts/<function>/` (`deploy/` `devenv/` `codegen/` `bucket/` `backfills/` `diagnostics/`); completed one-shot schema migrations are frozen under `scripts/migrations/`. A script that ONLY makes sense inside a GitHub Actions run (reads `$GITHUB_*`, posts PR comments) → `.github/scripts/` (currently none — `.github/` is workflows + config; CI invokes the `scripts/` tools). Code *imported* at runtime is a package (`qua_shared`/`qua_jobs`), not a script. Full decision rule + catalogue: [`scripts/README.md`](scripts/README.md). There is no `scripts/lib`, `scripts/jobs`, or `inspector/scripts/` — the `scripts-layout-guard` CI job (`inspector-checks.yml`) fails any PR that re-adds those paths or imports `scripts.lib`/`scripts.jobs`.
