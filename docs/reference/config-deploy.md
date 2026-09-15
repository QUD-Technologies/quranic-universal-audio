# Config & deploy

Every runtime knob the Inspector reads, plus how the image is built and shipped to the HF Spaces. Deployment tunables that aren't env-driven live in `inspector/config.py` (one constant per line, each commented — read it directly when changing a threshold). This doc covers the **env surface** + **build/deploy/provisioning**.

## Runtime profiles

Same code, profile selected by env presence:

| | **Local dev** (`python inspector/app.py`) | **Deployed** (HF Space, gunicorn) |
|---|---|---|
| Identity | `INSPECTOR_DEV_MODE=1` auto-set when OAuth unconfigured → synthetic per-role user, OAuth bypassed | HF OAuth → signed `inspector_session` cookie |
| DB | `inspector.db` at `INSPECTOR_DB_PATH` (defaults to a tmp path) | same; canonical bucket artefact is `<mount>/db/inspector.db` |
| Bucket | FUSE auto-mount via `hf-mount` (unless opted out) or `hffs` fall-back | NFS mount provided by the Space runtime at `INSPECTOR_BUCKET_MOUNT` |
| Port | `:5000` (Flask dev server) | `:7860` (gunicorn, `EXPOSE 7860`) |
| Workers | Flask dev server | gunicorn-gthread `-w 1 --threads 16` (single-worker invariant) |

`INSPECTOR_BEHIND_PROXY=1` is the deployed-mode signal: it enables `ProxyFix` and skips the local auto-mount (the Space already provides the mount). Set in the image.

It also mounts `utils/wsgi.py::ForceHttpsScheme` on top of `ProxyFix`, pinning `wsgi.url_scheme` to https. `ProxyFix` trusts one hop, which is correct for the direct `*.hf.space` host, but a **custom domain adds a hop** — the nearest `X-Forwarded-Proto` is then HF's internal plaintext leg and werkzeug settles on `http`. Everything scheme-derived breaks together: the OAuth `redirect_uri` goes out as `http://…` (HF answers the authorize call with a 400) and `Secure` is dropped from our cross-site cookies, which a browser then discards because they are `SameSite=None`. Both deployed hostnames are https-only at the edge, so pinning states a fact rather than guessing the hop count — which differs per hostname, hence not simply widening `x_proto`.

A custom domain also needs its **own** OAuth app: HF's auto-provisioned one (`hf_oauth: true`) only allow-lists the Space's own hosts, so the custom-domain `redirect_uri` 400s even over https. Register the app under `/settings/applications/new`, supply `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` as Space secrets, and drop `hf_oauth: true` from the README so HF stops injecting its own pair.

## Spaces

| Env | Space repo | Bucket (`INSPECTOR_BUCKET_REPO`) |
|---|---|---|
| dev | `hetchyy/quranic-inspector-dev` | `hetchyy/quranic-inspector-bucket-dev` |
| prod | `hetchyy/quranic-universal-audio` | `hetchyy/quranic-inspector-bucket` |

The image bakes the **dev** bucket as default (`INSPECTOR_BUCKET_REPO=hetchyy/quranic-inspector-bucket-dev`); the prod Space overrides it via a Space variable. Both ship the Docker SDK and bind 7860.

## Env vars — what the code actually reads

### Storage / bucket / DB

| Var | Default | Read by | Purpose |
|---|---|---|---|
| `INSPECTOR_BACKEND` | `bucket` | `services/storage/hf_bucket.py`, `auto_mount.py` | `bucket` (HF storage) or `filesystem` (tests + the Tier-0 fixtures/offline path seeded by `scripts/devenv/seed_fixtures.py`). |
| `INSPECTOR_BUCKET_REPO` | `…-bucket-dev` | `auto_mount.py`, `hf_bucket.py` | HF bucket repo id. Resolved by `resolve_bucket_repo()`: defaults to the **dev** bucket for every non-deployed process and **refuses prod** unless `INSPECTOR_ALLOW_PROD_BUCKET=1`. |
| `INSPECTOR_ALLOW_PROD_BUCKET` | unset | `hf_bucket.py`, `auto_mount.py` | `1` lets a local/non-deployed process use the prod bucket (otherwise `ProdBucketRefused`). The deployed prod Space is exempt (it runs behind the proxy). |
| `INSPECTOR_DB_SYNC` | unset (`1`) | `app.py`, `services/db/sync.py` | `0` disarms per-commit bucket DB write-back at boot. Boot still pulls the DB; DB commits stay local and never upload. Stops only the **DB** sync — pair with `INSPECTOR_READ_ONLY` to also stop direct content writes. |
| `INSPECTOR_READ_ONLY` | unset | `services/storage/hf_bucket.py` | `1` wraps the storage backend in `ReadOnlyBackend`, which refuses **every** mutating op (`write_*`/`append_jsonl`/`copy`/`move`/`delete`) with `StorageReadOnly`. Covers the writes `INSPECTOR_DB_SYNC` misses — segment save (`detailed.json`/`edit_history.jsonl`), intake manifests, job records — since they all go straight through the backend, not the DB sync. `launch.py --mode prod` sets both, so a local process on the prod bucket cannot mutate it by any path. |
| `INSPECTOR_AUDIO_FROM_BUCKET` | `1` | `services/audio/audio_fetch.py` | `0` makes `read_prefetched_audio_*` short-circuit so `audio_source.resolve` skips the bucket (mount + hffs full-MP3 read) and streams from the **CDN** — faster locally. `launch.py` sets `0` for `dev`/`prod` (override with `--bucket-audio`), `1` for `fixtures` (offline → local audio, no CDN). Deployed leaves it on (mount). |
| `INSPECTOR_PEAKS_FROM_BUCKET` | `1` | `services/audio/audio_fetch.py` | `0` makes `read_prefetched_peaks` short-circuit to `None` so every chapter misses and the FE falls through to per-segment **ffmpeg** compute. `launch.py --ffmpeg-peaks` sets `0`. |
| `INSPECTOR_BUCKET_MOUNT` | unset local; `/data/inspector-bucket` (image) | `app.py`, `health.py`, `hf_bucket.py`, `auto_mount.py` | Mount path. Presence ⇒ "deployed mode" for `/healthz`. When unset locally the backend falls back to per-call `hffs` reads (~50–500× slower). |
| `INSPECTOR_FILESYSTEM_ROOT` | unset | `hf_bucket.py` | Required when `INSPECTOR_BACKEND=filesystem`. `seed_fixtures.py` sets it to `inspector/.fixtures`. |
| `INSPECTOR_AUTO_MOUNT` | `1` | `auto_mount.py` | `0` opts out of local FUSE auto-mount. |
| `INSPECTOR_DB_PATH` | `<tmpdir>/inspector.db` | `services/db/connection.py` | On-disk SQLite path. |
| `INSPECTOR_HF_TOKEN` / `HF_TOKEN` | unset | `hf_bucket.py`, `auto_mount.py` | Token for bucket read/write (needs write scope). `INSPECTOR_HF_TOKEN` wins; `HF_TOKEN` is the fall-back + what `hf-mount` reads. |

### Data (bundled reference)

| Var | Default | Purpose |
|---|---|---|
| `INSPECTOR_DATA_DIR` | `/app/data` (image) / `<repo>/data` (local) | Root for bundled static data (surah_info + linguistic JSONs). |
| `INSPECTOR_QUA_DATA_PATH` | mirrors `INSPECTOR_DATA_DIR` | Base for `qpc_hafs.json`, `digital_khatt_v2_script.json`, `phoneme_sub_costs.json`. |

### Server / proxy / process

| Var | Default | Read by | Purpose |
|---|---|---|---|
| `INSPECTOR_HOST` | `0.0.0.0` | `config.py` | Bind host. |
| `INSPECTOR_BEHIND_PROXY` | unset; `1` (image) | `app.py`, `auth.py`, `auto_mount.py` | Deployed signal — enables `ProxyFix`, skips auto-mount. |
| `INSPECTOR_PUBLIC_URL` | unset | `routes/auth/auth.py` | Override the public https base used to build the OAuth callback. |
| `INSPECTOR_COMMIT_SHA` | `unknown` | `health.py` | Reported by `/healthz` so deploys are identifiable. |
| `GUNICORN_WORKERS` / `WEB_CONCURRENCY` / `GUNICORN_CMD_ARGS` | `1` | `app.py::_assert_single_worker` | Any multi-worker signal aborts boot. The single-worker invariant is load-bearing (single writer, in-process caches, per-slug locks). |
| `FLASK_ENV` | unset | `config.py` / `app.py` | `development` → Flask debug + reloader (local only). |

### Background loops

| Var | Default | Purpose |
|---|---|---|
| `INSPECTOR_AUTO_DETECT` | off; `1` (image) | Background loop that scans the bucket for newly-aligned reciters and folds them into state. |
| `INSPECTOR_AUTO_DETECT_INTERVAL_S` | (see `app.py`) | Tick interval for the auto-detect loop. |
| `INSPECTOR_AUTOMATIONS` | off; `1` (image) | Release-automation reconciler daemon (per-automation enable lives in the owner's config blob; the daemon no-ops while all are off). See [automation.md](automation.md). |
| `INSPECTOR_AUTOMATIONS_INTERVAL_S` | `60` | Tick cadence for the automation reconciler. |
| `INSPECTOR_JOB_IMAGE` | `python:3.11-slim` | Docker image every HF Job kind runs in (`services/admin/jobs/base.py`). Stock image; `job_command` installs ffmpeg + the kind's pip deps + the staged `qua_domain` wheel (`/aux/code/wheels/`, glob-guarded) at launch (~40 s). The former prebuilt `hetchyy/quran-ts-job` Space is gone. |
| `INSPECTOR_ALIGN_PIPELINE` | off; `1` on the dev Space | Native align pipeline — enables the Requests-tab Align run and resumes any pending/running `align_runs` worker at boot. See [align-pipeline.md](align-pipeline.md). |
| `INSPECTOR_ALIGNER_URL` | `https://hetchyy-quranic-universal-aligner-dev.hf.space` | Aligner Space the align stage + sidecars call (`/api/v1`). |
| `INSPECTOR_ALIGN_KEEP_STAGING` | off | `1` keeps `staging/<slug>/<run>/` after assemble (debugging). |
| `INSPECTOR_ACQUIRE_JOB_FLAVOR` / `INSPECTOR_ACQUIRE_JOB_TIMEOUT` | `cpu-upgrade` / `6h` | The acquire HF Job's hardware + wall clock. |
| `INSPECTOR_ALIGN_CONCURRENCY` | every pending chapter (what the batch advertises) | Narrows the align stage's fan-out for debugging; there is no cap by default. |
| `ACQUIRE_WORKERS` (job env) | one per vCPU, max 8 | Chapters the acquire job fetches + encodes at once. |
| `INSPECTOR_PUBLIC_BASE_URL` | empty | Public https root the daemon threads into job completion webhooks (no `request.url_root` in a thread). Required for automated GH cuts — the cut job is webhook-only. Set as a Space variable per environment. |
| `INSPECTOR_TS_STALE_RUN_HOURS` | `6` | How long a `running` timestamps run-log record may sit before the single-flight guard treats it as dead (a Space restart mid-run never stamps it terminal). |
| `INSPECTOR_TS_SPACE_URL` | `https://hetchyy-qua-batch-timing-prod.hf.space` | Persistent production timing Space used for timestamp generation. Override only for an isolated environment. |

### Auth / identity / secrets

| Var | Source | Purpose |
|---|---|---|
| `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` / `OPENID_PROVIDER_URL` | HF-injected (`hf_oauth: true`) | OAuth client + OIDC discovery. Auto-managed by HF; never persisted. |
| `OAUTH_SCOPES` | `openid profile` | OAuth scopes requested. |
| `INSPECTOR_SESSION_SECRET` | Space secret (≥32 hex) | Signs the `inspector_session` cookie. `services/auth/secrets_guard.py::get_session_secret` rejects unset/short values. Rotating logs everyone out. |
| `INSPECTOR_DEV_MODE` | `1` auto-set locally when OAuth unconfigured | Bypasses OAuth; mints a synthetic per-role user. |
| `INSPECTOR_DEV_<ROLE>_HF_ID` / `INSPECTOR_DEV_<ROLE>_LOGIN` | unset | Override the synthetic dev identity per role (`OWNER`/`MAINTAINER`/`CONTRIBUTOR`); default `dev-<role>`. Set to a real HF id+login when local dev points at the prod bucket so audit attribution is correct. |
| `INSPECTOR_GITHUB_DISPATCH_TOKEN` | Space secret | Fine-grained PAT (`actions:write` on the project repo). Fires `repository_dispatch` after publish. `secrets_guard.get_dispatch_token` raises if unset or still a `PLACEHOLDER_` value. |
| `INSPECTOR_WEBHOOK_SECRET` | Space secret (optional) | Shared secret for the timestamps-job completion webhook (`POST /api/webhooks/ts-job-complete`). When set, `launch()` threads it (+ the job's callback URL) to the HF job so it auto-publishes the reciter on success; the route validates it with `hmac.compare_digest`. **Unset → webhook disabled (503)** and release falls back to the drawer-poll path. Read by `routes/webhooks/ts_jobs.py` + `services/admin/timestamps_jobs.py::launch`. |
| `INSPECTOR_EXTRACTION_SECRET` | Space secret | Shared secret the aligner Space requires on `audio_ref` batch items and `/api/v1/extraction/sidecars` (`X-Extraction-Secret`); must equal the aligner Space's `EXTRACTION_SECRET`. Unset → Align start returns 503. The aligner bearer is the Inspector's own HF token (`INSPECTOR_HF_TOKEN` / `HF_TOKEN`). |
| `INSPECTOR_WEBHOOK_URL` | set on the **job**, not the Space | The Inspector's public callback URL. The launch route derives it from `request.url_root` (deployed `ProxyFix` → `https://…hf.space/`) and passes it to the job as env — operators don't set it on the Space. |
| `BREVO_API_KEY` | Space secret (optional) | Brevo transactional REST API key for the email-notifications sender (`services/email/`). HF Spaces block outbound SMTP on every port, so delivery is over HTTPS (`POST /v3/smtp/email`), not `smtplib`. **Unset → no email is sent; the sender logs the rendered message instead** (dev exercises the flow without secrets). |
| `INSPECTOR_EMAIL_FROM_ADDRESS` / `GMAIL` | Space secret | From address for outgoing email — MUST be a Brevo-verified sender. `INSPECTOR_EMAIL_FROM_ADDRESS` wins; falls back to `GMAIL` (the already-verified single-sender account). |
| `INSPECTOR_EMAIL_SITE_URL` / `INSPECTOR_EMAIL_GH_RELEASES_URL` / `INSPECTOR_EMAIL_FROM_NAME` | defaults in `config.py` | Email link targets + From display name. Functional links (manage/unsubscribe) use `INSPECTOR_PUBLIC_BASE_URL` (localhost in dev). |

> The error strings in `secrets_guard.py` still reference a `scripts.inspector_v2_seed.setup_space` provisioning module that no longer exists — secrets are now set by hand in the Space secrets panel. Treat that hint as stale.

### Quran Foundation (see `architecture.md` → QF integration)

| Var | Read by | Purpose |
|---|---|---|
| `QF_PREPROD_CLIENT_ID` / `QF_PREPROD_CLIENT_SECRET` | `services/quran_foundation/config.py` | User-API (bookmarks/collections) pre-prod OAuth client. `client_secret_basic` only. |
| `QF_OAUTH_REDIRECT_URI` | same | Registered callback; default `http://localhost:5001/api/qf/callback`. Must match the QF client exactly. |
| `QF_CONTENT_CLIENT_ID` / `QF_CONTENT_CLIENT_SECRET` | same | Content-API server-to-server (`client_credentials`) client. Production endpoints. |

Local QF creds live in `inspector/.env` (gitignored), loaded by `app.py`.

### Misc tunables (env-overridable constants)

| Var | Default | Purpose |
|---|---|---|
| `INSPECTOR_MFA_SPACE_URL` | `https://hetchyy-quran-phoneme-mfa-dev.hf.space` | MFA Space the cross-verse "Auto Split" calls. |
| `MISSED_BASMALA_FLAG_MIN_DELETED` | `10` | Min pipeline-stripped basmalas before flagging non-stripped chapters as "missed Basmala". |
| `INSPECTOR_QUA_DOMAIN_WHEEL_DIR` | `/opt/wheels` | Where the runtime image keeps the `qua_domain` wheel from stage 1b. `stage_job_code` uploads it to `aligner-bucket/code/wheels/` so HF Jobs (non-Hafs publish / cut) install the same edition projection the Space runs; absent = Hafs-only jobs, warned at launch. |
| `INSPECTOR_MULTI_RIWAYAH` | `1` | Kill-switch for the non-Hafs editions (`config.MULTI_RIWAYAH_ENABLED`). `0` makes `services/reference/editions.py` behave as if `qua_domain` were absent — a 60-second rollback via a Space **variable** + restart, where a rebuild is ten minutes. It does NOT render non-Hafs as Hafs; those deliveries fail loudly. |

## Image build — `inspector/Dockerfile`

Three-stage build, context is the **repo root** (so `qua_shared/` ships alongside `inspector/`):

1. **frontend-build** (`node:20-alpine`) — `npm ci` + `npm run build` → `dist/`.
1b. **qua-domain-build** (`python:3.11-alpine`) — sparse blobless fetch of `packages/quran-domain` at the pin in `qua_shared/qua_domain_pin.py`, then `pip wheel` → `/wheels`. Needs `--secret id=QUA_DOMAIN_DEPLOY_KEY` (a read-only SSH deploy key on `Hetchy/qua`, the same arrangement as `CELLS_DEPLOY_KEY`). `required=false`: **without the secret the stage produces an empty `/wheels` and the image still builds**, Hafs-only. The stage's `ARG QUA_COMMIT` default IS the pin on an HF Space (Spaces build with no build args) — `qua_shared/tests/test_qua_domain_pin.py` asserts it matches the Python constant.
2. **ffmpeg-build** (`alpine:3.23`) — compiles a minimal *static* ffmpeg/ffprobe (mp3 decode + `pcm_s16le`/wav for peaks + `libmp3lame`/mp3 muxer for the `segment-clip` route). `libmp3lame 3.100` is built from source `--enable-static` because Alpine ships no static lame archive — without it `clip.py` returns 200/0-bytes and playback hangs. http/https protocols enabled so ffmpeg can decode remote chapters via HTTP Range.
3. **runtime** (`python:3.11-alpine`) — installs `inspector/requirements.txt` **plus the optional `qua_domain` wheel from stage 1b** (11.7 MB of edition data: four scripts, four fonts, the Hafs→target projection; the install is glob-guarded so an absent wheel is a no-op; the wheel is kept at `/opt/wheels` for HF-Job staging), purges pip/setuptools/wheel, **selective COPY** (`app.py/config.py/constants.py`, `adapters/ domain/ routes/ services/ utils/`, `scripts/__init__.py + qua_shared/ + qua_jobs/`, `.github/config/`, `LICENSE`, the 4 bundled data JSONs, and `frontend/dist` from stage 1). `qua_jobs/` + `.github/config/` + `LICENSE` are read by the HF-Job entrypoints (`config_loader`, cut_release asset upload). Runs as non-root uid/gid 1000. `EXPOSE 7860`. **Adding a `COPY` here needs no staging edit** — the deploy stages the whole tracked tree (see Deploy).

`CMD`: `gunicorn -k gthread -w 1 --threads 16 --max-requests 5000 --max-requests-jitter 500 --timeout 60 --graceful-timeout 30 --bind 0.0.0.0:7860 --access-logfile - --error-logfile - --chdir /app/inspector app:app`. `-w 1` is load-bearing (asserted at import). `--threads 16` sizes the I/O-bound pool. `--max-requests` recycles the worker to bound slow leaks. The access log (one line per request → stdout) is de-noised by `app._AccessLogFilter`, attached to the `gunicorn.access` + `werkzeug` loggers: it drops static-asset (`/assets/*`), `/healthz`, and 304 lines while always keeping 4xx/5xx and real API/page hits.

`docker-compose.yml` (repo `inspector/`) is the local reviewer entry (`docker compose up` → `:5000`, bind-mounts `data/`).

## Deploy — `inspector-deploy.yml`

Push to `dev` or `main` (paths under `inspector/**`, `qua_shared/**`, `scripts/deploy/upload_inspector.py`, the bundled data JSONs, `.dockerignore`) → workflow runs `inspector-checks.yml` (reused), then `deploy` runs `scripts/deploy/upload_inspector.py <env>`. Branch→env: `dev`→dev Space, `main`→prod Space; `workflow_dispatch` can force `dev`/`prod`. The Space then rebuilds the Docker image from the uploaded files.

**Fast-path dev dispatch.** A manual `workflow_dispatch` that resolves to the **dev** Space (`env=dev`, or `env=auto` off any non-`main` branch) **skips the `checks` job entirely** for a quick deploy — the dev Space is a verification target, not prod. Every push and any prod-targeting dispatch still runs the full check suite. The `deploy` gate accepts a skipped `checks` (`success` *or* `skipped`), so it never confuses "intentionally skipped" with "failed".

**Staging = the whole git-tracked tree.** `upload_inspector._stage()` copies **every git-tracked file** verbatim (then overlays the root `Dockerfile` from `inspector/Dockerfile` + a frontmatter `README.md`), and `upload_folder(delete_patterns="*")` mirrors it to the Space. `.dockerignore` is the **single source of truth** that prunes the build context — there is no hand-curated allowlist to drift from the Dockerfile's `COPY` set (that drift took prod down once: a `COPY LICENSE` with no matching staging entry → `BUILD_ERROR "/LICENSE: not found"`). Consequence: the Space build context equals the `context: .` that `docker-publish.yml` builds on every push, so its green image build is a faithful "the Space will compile" signal. `dist/` is built inside the image (stage 1), so the deploy does **not** pre-build the frontend.

**Caveat — async build, no CI feedback.** `upload_inspector.py` only uploads + `restart_space(factory_reboot=True)` and returns; the HF docker rebuild is asynchronous. A green `inspector-deploy` run does **not** mean the Space built. After a deploy, poll the runtime stage (`…/api/spaces/<id>/runtime` → `RUNNING_BUILDING → RUNNING_APP_STARTING → RUNNING`; build logs at `…/logs/build`).

**Opt-in boot check (prod-only, default off).** `scripts/deploy/smoke_boot.py` builds the image and boots it offline (filesystem backend + public `seed_fixtures`, no secrets, no bucket), polling `/healthz` for `200 + state_loaded + db.open` (offline it's `200`/`status: degraded` since no bucket is mounted — so it does **not** assert `status == ok`). Two entry points: `scripts/deploy/upload_inspector.py --verify-boot` (gates a local/manual deploy on a passing boot), and a `boot-smoke` CI job that runs **only** on a manual `workflow_dispatch` with `verify_boot=true` targeting prod. The `deploy` job's `if` is `always() && (needs.checks.result=='success' || =='skipped') && needs.boot-smoke.result != 'failure' && != 'cancelled'` so a skipped `boot-smoke` (every normal push) or a skipped `checks` (fast-path dev dispatch) still deploys, while a failing one blocks the deploy. Automatic deploys stay lean (no image build on the deploy path; `docker-publish.yml` builds the identical context in parallel as a non-blocking red signal).

The canonical dev/prod Spaces have **no** auto-provisioning script — their secrets/variables (`INSPECTOR_SESSION_SECRET`, `INSPECTOR_GITHUB_DISPATCH_TOKEN`, QF creds, `INSPECTOR_BUCKET_REPO` override, bucket attachment) are configured by hand in the Space settings panels. OAuth vars are auto-injected by HF via `hf_oauth: true` in the Space README frontmatter.

**Contributor (personal) Spaces** are fully scripted — no manual Space-settings clicks. `scripts/devenv/bootstrap_dev_env.py <name>` provisions a private bucket + Space under the contributor's own account, **attaches the bucket as a Space volume at `/data/inspector-bucket`** (`HfApi.set_space_volumes`), auto-generates `INSPECTOR_SESSION_SECRET`, sets `HF_TOKEN` + the `INSPECTOR_BUCKET_REPO` variable, and (with `--deploy`) pushes code via `scripts/deploy/deploy_space.py <space-id>` (the committed, parameterized cousin of `scripts/deploy/upload_inspector.py`). The only manual prerequisite is an HF token with write scope; OAuth vars are auto-injected via `hf_oauth: true`. See `inspector/README.md` for the three-tier dev workflow.

## Type-check gate (pyright)

The entire Python backend (`inspector/`, `qua_jobs/`, `qua_shared/`) is
type-checked in basic mode as a **blocking** CI gate (`type-check` job in
`inspector-checks.yml`). It runs via `npx -y pyright --level error inspector
qua_jobs qua_shared` so no Python type-checker dependency is added to the image
— node 24 is already present. The `extraPaths` in `pyrightconfig.json` make the
flat-layout imports resolve cleanly across the three packages.

Touching any file under `qua_shared/schemas/` also requires regenerating the
codegen'd FE types and committing the result — see `schema-codegen-check` in
`inspector-checks.yml` and `docs/reference/schemas.md`.

## Secret rotation

- `INSPECTOR_HF_TOKEN` — new token → update Space secret → restart. Revoke the old after verifying.
- `INSPECTOR_SESSION_SECRET` — replace the Space secret → restart. **All in-flight cookies invalidate** (everyone logged out).
- `INSPECTOR_GITHUB_DISPATCH_TOKEN` — mint new PAT → update secret → restart.
- `QUA_DOMAIN_DEPLOY_KEY` — read-only SSH deploy key on `Hetchy/qua` (**deploy key, not a PAT**: scoped to one repo, read-only, and unaffected by `gh auth refresh`). Lives as a GitHub Actions secret on this repo AND as a Space secret on both Spaces (`upload_inspector.py` pushes it on every deploy; it warns and leaves the Space secret alone when absent from the env, so a deploy from a machine without it can't blank a working Space). Rotate: add a new key on `Hetchy/qua` → update the three secrets → redeploy → delete the old key. Consumed **only at build time** — no key ever lands in the running container.

### Optional dependency: `qua-domain`

Installed from a pinned commit of the private `Hetchy/qua` monorepo, not PyPI. One pin (`qua_shared/qua_domain_pin.py`), three consumers: `scripts/devenv/install_qua_domain.py` (local + CI), the Dockerfile stage, and the boot-time provenance in `/healthz`. Bump it like a lockfile entry — edit `QUA_COMMIT` **and** the Dockerfile `ARG`, run the installer, commit.

Absent (fork, no key, or `INSPECTOR_MULTI_RIWAYAH=0`) the Inspector runs Hafs-only: `services/reference/editions.available()` is `False` and every non-Hafs accessor raises `EditionsUnavailable`. There is deliberately **no Hafs fallback** — rendering one edition's coordinates under another's script would let a reviewer save wrong refs.

## Health

`GET /healthz` (`routes/auth/health.py`) returns the resolved config + substrate status: `status`, `mode` (deployed iff `INSPECTOR_BUCKET_MOUNT` set), `bucket_mounted` (checks `<mount>/db/inspector.db` exists), `state_loaded`, `reciters_count`, `oauth_configured`, `auto_detect_loop`, `commit`, and a `db` block (`open`, `schema_version`, `last_bucket_upload_ts`, `bucket_lag_seconds`, `last_error`). Returns **503** in deployed mode when degraded so probes fail loud; always 200 in local mode (no mount to check). `GET /livez` is the always-200 liveness probe.

`editions` block: `{available, riwayat, projection_id, projection_sha256, reference_id}` — the state of the optional `qua_domain` package. Degraded **only** when it is unavailable *and* the catalog holds a delivery in a non-Hafs riwayah (then `unservable_riwayat` lists them); a Hafs-only deployment, or a fork built without the deploy key, stays green.

`GET /healthz?deep=1` adds an **opt-in** `sample_validation` block — a bounded external-bucket drift probe (`services/storage/bucket_audit.py::sample_validation`): it round-trips the DB catalog through `repo_catalog.snapshot()` and audits a small reciter-folder sample (default 3, spread across the sorted slug list) against the `qua_shared/schemas` definitions. Shape: `{ok, catalog_ok, sampled, n_reciters, errors}`. The default probe never walks the bucket (latency unchanged); a deep probe that finds drift flips `status` to degraded (503 in deployed mode) so external-file drift surfaces at health-check time instead of mid-request.
