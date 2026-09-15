# Release automations

Owner-configurable automations that watch live release state and fire the
existing release jobs on their own — surfaced as the **Automation** card atop the
admin Releases tab. The engine is a single opt-in reconciler daemon; it reacts to
*state*, not events, so it is idempotent + restart-safe.

## The six automations

Plus a non-acting watchdog, below.

| id | What it does | Trigger | Owner settings |
|---|---|---|---|
| `auto_gen_ts` | Launch the timestamps job for a marked-ready recitation that clears the gates | A `ready_to_generate` row (marked-ready, no TS) | gate-by-comments, gate-by-flags |
| `stale_ts_regen` | Regenerate timestamps once segment edits settle | A `behind_edits` row past the guard | guard-minutes, scope (full \| affected) |
| `stale_metadata` | Refresh the HF catalog once a metadata edit settles | An HF row stale for `catalog_edit` past the guard | guard-minutes |
| `auto_release_inactive` | Force-release a reviewer's claim after prolonged inactivity (back to the awaiting-review pool; reviewer notified) | An open, not-yet-marked-ready under-review claim whose reviewer has been idle ≥ N days | inactive-days |
| `hf_publish` | Batch-publish every fresh + stale HF candidate | Scheduled: every N days at HH:MM (owner tz) | interval-days, time-of-day, timezone |
| `gh_cut` | Cut a global GH release when there are changes | Scheduled: every N days at HH:MM (owner tz) | interval-days, time-of-day, timezone, next-version override |

All default **disabled**. The engine reuses the manual job entrypoints verbatim
(`timestamps_jobs.launch`, `cut_release.launch`, `hf_publish_batch.launch`,
`refresh_catalog.launch`) — automation is a *decider*, not new job code. Automated
launches therefore record into the same uniform `JobRecord` store as manual ones,
so every automated run is visible in the admin **Jobs** tab ([admin-dashboard.md](admin-dashboard.md) § Jobs compartment).
`refresh_catalog.launch`) — and the manual `claim.force_released` transition for
`auto_release_inactive`. Automation is a *decider*, not new job code.

## Shard-integrity watchdog

A seventh evaluator, `shard_integrity`
(`services/admin/automation/integrity.py`), that is **not** one of the six: it
takes no action, launches no job, and has **no config toggle**. It sweeps every
delivery once every 24 h for chapters whose timestamps shard has vanished and
notifies the data-integrity recipients ([notifications.md](notifications.md)
§ Owner data-integrity alerts).

- **Why always-on.** There is nothing to tune and no job cost, so an opt-in flag
  would only mean the safety net is off exactly when it matters. The one knob is
  the `notifications.receive_integrity_alerts` capability.
- **Why not the GH cut.** The cut runs on demand, covers only release-eligible
  deliveries, and surfaces a lost chapter as an ordinary "missing coverage" line
  — so a gap can sit unnoticed until someone reads a changelog.
- **Why daily.** The sweep lists two directories per delivery; at the 60 s tick
  that would be thousands of pointless listings an hour. The cadence anchor is
  the `automation_state` row, like the scheduled automations.
- **What it detects** (`services/storage/shard_integrity.py`, Flask-free):
  `orphan_temp` — the hidden `.{chapter}.json.br.{rand}` left by a *killed*
  `write_validated_shard` whose non-atomic bucket rename already removed the
  destination; the orphan holds the complete payload, so the repair is to
  re-validate its bytes and publish them. `missing_shard` — audio present, no
  shard, no orphan; that chapter needs a single-chapter re-align.
- **False-positive guards.** A delivery with zero shards was never timestamped
  (and is absent from every release), so it is not a finding; an orphan beside a
  healthy shard is litter from a later-redone write, not a gap; and the sweep
  uses `list_dir_strict`, never `list_dir` — the latter turns a bucket API error
  into an empty list, which here would read as "every shard is gone". A delivery
  whose listing fails is reported as *unreadable* in the run detail, never as
  findings.

## Engine

| Piece | File | Role |
|---|---|---|
| daemon | `services/admin/automation/engine.py` | `tick()` runs all evaluators; `start_background_loop()` (the ~60 s loop) |
| evaluators | `services/admin/automation/evaluators.py` | one function per automation; `run_all(now)` isolates each in try/except |
| schedule | `services/admin/automation/schedule.py` | `is_due` / `next_fire_at` — `zoneinfo` interval math, catch-up-once, DST-safe |
| config | `services/admin/automation/config.py` | `load_config` (db_seq-cached) / `save_config` (durable + audit) |
| actor | `services/admin/automation/actor.py` | `SYSTEM_AUTOMATION` audit actor on every automated launch |

Wired in `app.py::_boot_substrate()` behind `INSPECTOR_AUTOMATIONS=1` (prod
Dockerfile sets it; off in dev). One daemon thread in the single gunicorn worker
— no change to the single-worker invariant.

## Persistence

Migration `0020_automation.sql`, repo `services/db/repo_automation.py`:

- `automation_config` — single-row JSON blob (`id=1`) holding the
  `AutomationConfig` (`qua_shared/schemas/config/automation.py`). db_seq-keyed cache in
  `services/storage/cache.py`; any committed config write invalidates it.
- `automation_state` — per-automation `last_run_at` / `last_status` /
  `last_detail`. Written **only when an automation acts** — an idle tick writes
  nothing (no bucket upload). `next_run_at` is **not** stored; the route computes
  it live so idle ticks stay write-free. The one exception is the
  `shard_integrity` watchdog, which records every sweep (clean ones included, as
  `skipped`) because that row is also its cadence anchor — once a day, not once a
  tick.

## Shared TS-generation defaults

`AutomationConfig.ts_generation_defaults` (`TsGenerationDefaults`) is the single
owner-wide source for the timestamps-generation knobs — beam, aligner
model, workers, batch_size, download_workers, padding, method. It lives inside
the same config blob (no separate table) and is the base every generation
surface reads:

- the manual launch form (`routes/admin/reviews.py::_parse_ts_settings`) takes
  every TS tunable from it; the request body carries only the per-launch
  `chapters` scope;
- `eval_auto_gen_ts` / `eval_stale_ts_regen` build their `TsJobSettings` from it
  via `evaluators._ts_settings` (only `chapters` is the automation's own scope);
- both thread `padding` / `method` into the HF job env (`PADDING` / `METHOD`),
  which the job reads with `DEFAULT_PADDING` / `DEFAULT_METHOD` fallbacks.

Edited from the Releases-tab **Timestamps generation** card
(`TsGenerationSettingsSection.svelte`) via
`GET|POST /api/admin/releases/ts-generation-defaults`, same
`release.manage_automation` gate.

## Route + capability

`GET|POST /api/admin/releases/automation` (`routes/admin/releases.py`), gated by
`release.manage_automation` (owner-only by default, in the capability registry).
GET returns `{config, state[], next_version}` where `next_version` is the
auto-computed next GH tag (reuses the summary card's what's-next computation) and
each scheduled `state` row carries a live-computed `next_run_at`. POST validates
+ saves the whole `AutomationConfig` with an audit append.

FE: `tabs/dashboard/components/admin/releases/AutomationSection.svelte` (mounted
owner-only under `ReleasesSummaryCard`); API in `lib/api/admin-releases.ts`.

## Invariants / edge cases

- **State-driven, trigger-agnostic chaining.** *Any* completed TS regen — manual
  or automated — stamps the HF row `republish_hf`-stale, so auto-publish (when
  on) picks it up at its next window and the cut sees it at its window. The
  interval cadence (not instant) is the buffer; pause auto-publish to stop a
  manual regen propagating.
- **Failed jobs are not retried.** `auto_gen_ts` + `stale_ts_regen` skip any slug
  whose most-recent timestamps job ended non-success
  (`timestamps_jobs.latest_terminal_failed_slugs`) — it stays in
  `ready_to_generate` / `behind_edits` for a manual retry, exactly as today.
- **Single-flight.** Every launch already enforces per-slug + global single-flight
  (`running_job_for`); evaluators also pre-skip in-flight slugs and the
  cut/batch/refresh globals.
- **Relaunch watermark (one job per edit-burst).** TS staleness / mark-ready is a
  *computed* signal that clears only when a regen's async completion advances the
  `ts` release `produced_at` (the 120 s Space-run poll or an interactive status
  poll) — which lags the 60 s
  tick. So `auto_gen_ts` + `stale_ts_regen` additionally skip a slug whose newest
  timestamps job (`timestamps_jobs.latest_job_started_by_slug`) started at/after
  the staleness/readiness watermark (`last_edit_at` / `marked_ready_at`): a regen
  covering those edits is already pending, so they wait rather than re-fire. This
  bounds launches to exactly one per edit-burst **even if completion never clears
  staleness** (broken webhook + missed poll) — the slug then stays visibly stale
  for manual attention instead of spawning unbounded jobs. The in-flight + failed
  guards alone do NOT cover the succeeded-but-not-yet-completed window.
- **Debounce.** Stale-TS/metadata fire only once the latest invalidating edit is
  older than `guard_minutes` (via `ts_stale_info.last_edit_at`), so consecutive
  edits coalesce into one regen and we never regenerate mid-edit.
- **First fire bootstraps from the clock, not from `last_run_at`.** A scheduled
  automation (`gh_cut` / `hf_publish`) has no `last_run_at` until it fires, and
  firing is what writes it — so `is_due` must not gate the first run on a stored
  anchor. When `last_run_at` is null it fires once the local clock reaches *today's*
  `HH:MM` (enabled before the window → fires at it; enabled after → catches up on
  the next tick), which seeds `last_run_at` and hands every later fire to the
  interval branch. `next_fire_at` still reports the strictly-future *upcoming*
  occurrence (for the FE's "next run"); `is_due` deliberately does **not** delegate
  to it for the null case, or the schedule could never bootstrap.
- **Empty windows.** GH cut skips when the preview shows no adds/refreshes; batch
  publish skips when there are no candidates (both advance the cadence so they
  don't re-check every tick).
- **GH cut webhook.** The cut job is webhook-only (no poll fallback), so automated
  cuts need `INSPECTOR_PUBLIC_BASE_URL` set (the daemon has no `request.url_root`).
- **Actor.** Automated launches/transitions carry the `SYSTEM_AUTOMATION` actor so
  the audit rail reads "automation" and the actor-on-every-edit invariant holds.
- **Inactivity = last review activity, not just claim age.** `auto_release_inactive`
  measures idleness as the *later* of the claim time and the reviewer's most recent
  segment edit (`saved_at_utc` from the slug's edit history) — so a reviewer who's
  actively editing isn't released even if they claimed long ago. When that signal
  can't be read it fails safe (keeps the claim). It only ever touches open,
  not-yet-marked-ready under-review claims (a marked-ready claim is complete and
  awaiting the pipeline, not idle), and routes through the same
  `claim.force_released` transition as the manual path, so the reviewer gets the
  identical "your review was released" notification.

## Env

| Var | Default | Effect |
|---|---|---|
| `INSPECTOR_AUTOMATIONS` | unset (on in prod Dockerfile) | `=1` starts the reconciler daemon |
| `INSPECTOR_AUTOMATIONS_INTERVAL_S` | `60` | tick cadence |
| `INSPECTOR_PUBLIC_BASE_URL` | empty | public https root for job completion webhooks (required for automated cuts) |
