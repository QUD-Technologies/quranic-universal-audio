# Inspector

The public website for Quranic Universal Audio — browse and play reciters,
inspect word-level timestamps, and edit segment alignments. Flask + a Svelte
single-page app, backed by a Hugging Face storage bucket.

## Developing — three ways to run it

The same code runs in three setups. Pick the lightest one that covers what
you're working on; you can always move up a tier.

| Tier | Run it with | Best for | What you need |
|------|-------------|----------|---------------|
| **0 — Fixtures (offline)** | `seed_fixtures.py` then `python inspector/app.py` | Frontend, UI, quick prototyping — most day-to-day work | **Nothing.** No HF account or token. |
| **1 — Your dev bucket (local)** | local app pointed at your own bucket | Backend, database, state machine, real bucket data — the bulk of backend work | HF account + token + your own bucket (one command) |
| **2 — Your dev Space (deployed)** | a personal HF Space | The final end-to-end check that the *deployed* app behaves like production (real login, gunicorn, the hourly daemons) | HF account + token + your own Space (one command) |

Tier 1 is enough for almost everything. Reach for Tier 2 only when you need to
confirm something that only happens in a deployed Space.

> **Why a separate bucket per person (Tiers 1–2)?** The app's database is a
> single SQLite file that's synced *whole* to its bucket on every write. Two
> people pointing at one bucket would overwrite each other's data. So everyone
> gets their own.

### Workflow

1. **Branch from `main`:** `git switch -c <your-name>/<topic> main`.
2. **Develop in whichever tier fits** (see below).
3. **Open a pull request into `main`** — once reviewed and merged, CI deploys
   to production automatically.

To preview a branch as a live deployment *before* merging, deploy it to your
own Space (Tier 2):

1. **Script (recommended):** `python scripts/deploy/deploy_space.py <your-space-id>`
2. **HF CLI directly:** build the frontend (`cd inspector/frontend && npm run
   build`), then `hf upload <your-space-id> . --repo-type space` from a staged
   copy. The script does the staging for you, so option 1 is simpler.

### Tier 0 — Fixtures (no account, no token)

Downloads a small public sample dataset and runs the app fully offline against
it.

```bash
python scripts/devenv/seed_fixtures.py        # download sample data + configure
cd inspector/frontend && npm install && npm run build
python inspector/app.py                          # → http://localhost:5000
```

For frontend work with hot-reload, run Vite alongside the Flask server:

```bash
cd inspector/frontend && npm run dev             # → http://localhost:5173 (proxies /api)
```

You're signed in as a synthetic **owner** automatically (no login). Use the
in-app role switcher to test other roles. The fixtures bundle a couple of
sample reciters so you can exercise the Dashboard and the Segments editor;
audio streams from the public CDN when available.

### Tier 1 — Your own dev bucket (local, real data)

One command creates a private bucket under your account and seeds it from the
same public sample:

```bash
python scripts/devenv/bootstrap_dev_env.py <name>
```

Then point your local app at it by adding two lines to `.env` at the repo root:

```bash
INSPECTOR_BUCKET_REPO=<your-hf-user>/quranic-inspector-<name>
HF_TOKEN=<your token>
```

Run the app the same way as Tier 0 (`python inspector/app.py`). It now reads
and writes your bucket. To start over, run
`bootstrap_dev_env.py <name> --teardown` and bootstrap again.


### Tier 2 — Your own dev Space (deployed)

When you need to verify deployed behaviour, deploy the same code to a personal
Space:

```bash
python scripts/devenv/bootstrap_dev_env.py <name> --deploy
```

This creates the Space, attaches your bucket as its storage volume, wires its
secrets, points it at your bucket, and pushes the current code. The Space uses
**real Hugging Face login** (OAuth) like production — see [Secrets](#secrets)
for what's set up for you.

> **The only thing you do by hand on Hugging Face is create a token.** No
> clicking in the HF UI to duplicate Spaces or buckets, attach storage, enter
> secrets, or register an OAuth app — `bootstrap_dev_env.py` does all of it via
> the API. (Use a token with write access to your own repos.)

To push code changes after that:

```bash
python scripts/deploy/deploy_space.py <your-hf-user>/quranic-inspector-<name>
```

## What's a bucket?

A **bucket** is Hugging Face's S3-like file storage — a remote folder addressed
as `hf://buckets/<owner>/<name>/`. The Inspector keeps everything that changes
there: the SQLite database (`db/inspector.db`) and per-reciter content.

You mostly don't interact with it directly — the app reads and writes it for
you. When you do need to poke at it:

```bash
hf buckets list                                  # your buckets
hf buckets list <owner>/<name> -R                # list files
hf buckets cp <owner>/<name>/db/inspector.db ./  # download a file
```

For fast local reads the app auto-mounts your bucket as a local folder (via the
`hf-mount` tool) if it's installed; otherwise it reads over the network. Either
way it's automatic — you don't mount anything by hand.

## Secrets

For local Tiers 0–1 the only thing you ever set is your own `HF_TOKEN` (Tier 0
needs nothing). For a Tier-2 Space, `bootstrap_dev_env.py` sets everything for
you:

| Secret / variable | Set by | Purpose |
|---|---|---|
| `HF_TOKEN` | bootstrap | Lets the Space read/write your bucket |
| `INSPECTOR_SESSION_SECRET` | bootstrap (auto-generated) | Signs login cookies |
| `INSPECTOR_BUCKET_REPO` | bootstrap | Points the Space at your bucket |
| `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` | **Hugging Face, automatically** | Login — injected because the Space enables `hf_oauth`; you never register an OAuth app |
| `INSPECTOR_OAUTH_CLIENT_ID` / `INSPECTOR_OAUTH_CLIENT_SECRET` | unset | Override the pair above with an OAuth app you registered. Only needed for a **custom domain**, which HF's auto-provisioned app does not allow-list |

Use a token scoped to your own repos. Get one at
<https://huggingface.co/settings/tokens>.

## Tests

```bash
cd inspector && python -m pytest tests/ -v       # backend
cd inspector/frontend && npm run test            # frontend
cd inspector/frontend && npm run check           # typecheck
```

## Tech stack

- **Backend:** Python 3.11, Flask (Blueprints)
- **Frontend:** Svelte 4/5 + TypeScript + Vite
- **Audio:** Web Audio API (waveform decoding/drawing), ffmpeg (server-side peak extraction)
- **Storage:** SQLite synced to a Hugging Face bucket