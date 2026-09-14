# Admin dashboard

Owner/maintainer control surface launched from the Dashboard activity rail. A wide modal with a top tab strip; **Users**, **Requests**, **Reviews**, **Releases**, **Jobs**, **Announcements**, and **Permissions** ship. The modal reopens to the **last active tab** (localStorage `insp_admin_active_tab`, validated against the live tab set; `openModal()` defaults to it). The **Permissions** tab is **owner-only** (`ownerOnly` → `$isOwner`); the **Announcements** tab is **capability-gated** on `announcements.send` (owner-only by default, but owner-toggleable to maintainers — so it filters on `$canAnnounce`, not the tier). Both vanish from the tab strip when ineligible, and an active-tab guard snaps back to Users on a live demotion.

## Permissions compartment (owner-only)

`PermissionsCompartment.svelte` renders the capability matrix (`GET /api/admin/permissions`) grouped by category; each capability row is an On/Off `Toggle` (`lib/components/Toggle.svelte`) per tier (anonymous / contributor / maintainer), with a static always-on **Owner** indicator. Anonymous cells render a locked **N/A** marker when the capability isn't `anon_eligible`; the `manage_permissions` row is fully locked (owner-only fixed). Toggling is optimistic + revert-on-error (mirrors `UsersCompartment.changeRole`); a modified cell shows a reset (↺) affordance → `POST …/<cap>/<tier>` with `{reset:true}`. API client `lib/api/admin-permissions.ts`; the resolved capability model + endpoints live in [auth-permissions.md](auth-permissions.md) § Capabilities.

Gated on `$isAdmin` (maintainer **or** owner). Read-only data for maintainers; the role picker (Users), request resolution (Requests), and reviewer overrides (Reviews → General-drawer popover) are owner-only or use existing owner-gated routes.

The entry button (`AdminDashboardButton`) is badge-free — it just opens the modal. "New request" awareness moved to the per-user My Notifications rail (the owner-facing `request.received` alert, see [notifications.md](notifications.md) § Owner review alerts); the Requests tab no longer carries an unviewed dot/count, and the marked-ready review notification was retired with the Releases-tab restructure.

## Frontend

`tabs/dashboard/components/admin/`

| File | Role |
|---|---|
| `AdminDashboardButton.svelte` | Entry button above the activity rail; rendered only when `$isAdmin`. Opens the modal via the store. No badge. |
| `AdminDashboardModal.svelte` | `Modal size="wide"` + tab strip (`adminDashboard.activeTab`, persisted to localStorage). Renders `UsersCompartment` / `RequestsCompartment` / `reviews/ReviewsCompartment` / `releases/ReleasesCompartment` / `jobs/JobsCompartment` / `AnnouncementsCompartment` / `PermissionsCompartment`. |
| `AnnouncementsCompartment.svelte` | Compose a global announcement (title + optional body) + a management list of past announcements (active + revoked) with per-row Revoke. Gated on `announcements.send`. API client `lib/api/announcements.ts`; see [notifications.md](notifications.md) § Announcements. |
| `RequestsCompartment.svelte` | Status facets (open / accepted / sent back / discarded + counts) over a review queue. Clicking a pending row expands an inline review (proposed-changes diff over `ProposedEdits` fields, requester note, conflict notice). Expanding a pending request marks it viewed (clears the unviewed dot/pill, decrements the badge). **Owner-only** inline reason + Send back / Discard; maintainers are read-only. Archived rows show a read-only resolution footer. Lazy-fetched on first activation + light `visiblePoll` while open. |
| `reviews/ReviewsCompartment.svelte` | Slimmed read-only oversight surface — two collapsible buckets (**Under review** [claimed, not yet marked ready] / **Available for review**) split FE-side from one `/api/admin/reviews/list` fetch. Available collapsed by default (localStorage-persisted). Filter bar (Arabic + Latin search · riwayah/style/channel facets · `stalled`/`name` sort). Marked-ready / published / staleness moved to the Releases tab. |
| `reviews/ReviewsRow.svelte` | One recitation row — Latin primary + Arabic muted trailing + reviewer chip; age (stale ⚠ when a claim is > 7d old) + a `Segments` deep-link (`selectedReciter` + `setActiveTab('segments')` + `adminDashboard.close()`). Row body click opens the General drawer. No Generate-TS / unread dot (retired with the restructure). |
| `reviews/ReviewsGeneralDrawer.svelte` | Current-claim chip is the popover trigger for owners (subtle hover ring + caret); maintainers see static text. **Mark-ready submission** card (between Current reviewer and Reviewer history, visible only when `current_claim.mark_ready_submission` is set) renders the reviewer's checklist as a 6-row ✓ list plus the two optional comment boxes as quoted blocks — labels come from the same `tabs/segments/copy/mark-ready` module the reviewer saw. **Bypass submissions** (`submission.bypass_used === true`) replace the checklist with an italic bypass line + an "owner bypass" pill. Also holds the reviewer-history table (closed claims), expanded vertical timeline, and a **Flagged issues** count (shown only when `flagged_issues_count > 0`). |
| `reviews/ReviewerActionsPopover.svelte` | **Owner-only** mode-driven popover anchored to the current-claim chip — three views: `menu` (Change reviewer / Remove reviewer), `change` (debounced 300 ms HF login lookup → resolved user card + reason → Reassign; disables when picked login equals current reviewer), `remove` (reason → Force-release). RolePicker-style click-outside + Escape dismissal; success fires `onaction` so the drawer + parent list refetch. |
| `reviews/ExpandedTimeline.svelte` | Vertical chronological timeline with tone variants (transition / admin / job dot styles). Newest-first sort. Reused by the Releases row's Timeline expand. |
| `UsersCompartment.svelte` | Fetches the list once + visitor stats; people/traffic ribbon, search + role-filter toolbar, master table, detail drawer. Owns inline role-change orchestration (optimistic + revert + error banner). |
| `UsersTable.svelte` | Sortable master table. Role cell is a `RolePicker` (`editable={canEditRoles}`); the cell stops click-propagation only when editable so non-owners still open the drawer. |
| `UserDetailDrawer.svelte` | Lazy per-user detail (stats, role/claims/requests/activity timelines, HF profile link). Header pill is a `RolePicker`; reflects the live row role via `roleOverride`. |

### Releases compartment (FE)

`tabs/dashboard/components/admin/releases/` — the single operator lifecycle home. Sections are organized **by action needed** (mutually exclusive, priority order): In progress · Failed to publish · **Ready to generate** (marked-ready, no ts) · **Behind edits** (ts stale) · **Republish to HF** (hf stale) · **Publish to HF** (released, first publish) · Published & current. `bucketOf()` mirrors the route's `_is_bucketable`.

| File | Role |
|---|---|
| `ReleasesCompartment.svelte` | Bulk `/api/admin/releases/status` fetch + FE bucketing + filter bar; owns the single-open in-row expansion state (`expanded = {slug, mode}` — one open across the whole list), the batch-publish selection + action bar, the cut modal, and send-back on Ready-to-generate rows. Also fetches `/api/admin/release-preview` for the header what's-next. |
| `ReleasesRow.svelte` | TS/HF/GH chips + reviewer chip on Ready-to-generate rows. Action cluster: Generate/Regenerate (opens the `ts` expand) · Timeline · Reviewers · Past jobs · Segments redirect; Send back on Ready-to-generate; in-flight controls on running rows. Renders `ReleasesRowExpansion` below when this row is the open one. |
| `ReleasesRowExpansion.svelte` | The one shared, button-switched panel — renders `ReleasesTsSettings` (`ts`), `ExpandedTimeline` (`timeline`), `ReviewerHistoryView` (`reviewers`), or `ReleasesPastJobs` (`jobs`). Timeline + Reviewers share one lazy `fetchAdminReviewDetail`. |
| `ReleasesTsSettings.svelte` | Unified generate/regenerate form — beam 50 (single pass), collapsible Advanced, inline Full-vs-Affected scope chooser. Launches via `generateTimestamps`. |
| `ReleasesPastJobs.svelte` | Job history + live `visiblePoll` log tail; on terminal success triggers a status refetch. |
| `ReviewerHistoryView.svelte` | Read-only current-reviewer + mark-ready submission + closed-claim history + flagged count (no claim mutation). |
| `ReleasesSummaryCard.svelte` | Current GH release + **what's-next** preview ("Adds N, refreshes M (K carried) over vX.Y.Z") + Cut / Refresh-catalog buttons + in-flight strip. |
| `CutReleaseModal.svelte` · `ReleasesActionBar.svelte` | Cut dry-run document; sticky batch-publish bar. |

Full release model: [dataset-and-releases.md](dataset-and-releases.md). TS gen/regen subsystem: [timestamps-job.md](timestamps-job.md).

### Jobs compartment (FE)

`tabs/dashboard/components/admin/jobs/` — the **one** place to see every job kind (`timestamps` · `hf_publish` · `hf_publish_batch` · `cut_release` · `refresh_catalog` · `acquire_audio`), running **and** historical, across all reciters. Replaces the scatter where TS history hid in a per-reciter Past-jobs expand and release/cut/publish jobs only flashed in the Releases in-flight strip.

| File | Role |
|---|---|
| `JobsCompartment.svelte` | `visiblePoll` (~10 s) over `GET /api/admin/jobs`; filter chips (kind / status / reciter+id search); running pinned on top; row click opens the drawer. Gated `reviews.view`. |
| `JobDetailDrawer.svelte` | `GET /api/admin/jobs/detail` → summary + per-kind extras (TS settings + log tail; batch/cut `members` table; `version`/`external_uri`); **Open on HF ↗** (the job page `url`) + **Cancel** when running (reuses `cancelReleaseJob`). |

API client `lib/api/admin-jobs.ts` (`fetchJobs`, `fetchJobDetail`; re-exports `cancelReleaseJob`).

### Shared job-record store (backend) — the single source the Jobs tab reads

`services/admin/jobs/records.py` is the **one writer/reader** of job records. Every kind's server-side `launch()` writes a `running` `JobRecord`; every `complete()` and every failure path (webhook non-success branches, the generic cancel route, the TS backstop) writes the terminal outcome via read-merge-write (launch fields survive). Paths reuse `base.job_record_path` — `reciters/<slug>/jobs/<kind>/<id>.json` per-slug, `jobs/_global/<kind>/<id>.json` global. The DB stays source-of-truth for *releases*; these records are the uniform observability trail (the `JobRecord` schema is a superset of the legacy `TsJobRecord`, which the TS HF-Job container still self-writes unchanged).

`services/admin/jobs/registry.py` aggregates: `records.list_all()` ∪ live `base.list_in_flight_jobs(ALL_KINDS)`, deduped by `job_id` (live `running` wins), newest-first, TTL-cached (`cache.{get,set,invalidate}_all_jobs_cache`). `job_detail()` enriches a running TS job with its live log tail. Endpoints (gate `reviews.view`): `GET /api/admin/jobs`, `GET /api/admin/jobs/detail?kind=&job_id=&slug=`. Cancel reuses `POST /api/admin/release-jobs/<job_id>/cancel`.

Pre-store history (cut/hf_publish that predate the store) is one-shot synthesized into records by `scripts/backfills/backfill_job_records.py` (from `gh_releases` + `per_recitation_releases(track='hf')`), so the tab reads complete history from one store.

State:
- `tabs/dashboard/stores/admin-dashboard.svelte.ts` (`adminDashboard`: `open`, `activeTab` [localStorage-persisted], `openModal/close/setTab`).
- `lib/stores/reviews.svelte.ts` (`reviewsStore`: `selectedSlug`, `openDrawer` [`'general'`], `filters`, `sortBy`, `refreshSeq`; `open(slug)`).
- `lib/stores/releases.svelte.ts` (`releasesStore`: `filters`, `sortBy`, `refreshSeq` + `requestRefresh()`).

Cross-tab building blocks in `lib/components/`: `RolePill` (presentational badge), `RolePicker` (owner-editable wrapper — bare pill when `editable=false`, dropdown of role pills + click-outside/Escape when `true`), `Avatar`, `Timeline`. API clients `lib/api/admin-users.ts`, `lib/api/admin-requests.ts`, `lib/api/admin-reviews.ts`; formatters `lib/utils/admin-format.ts`.

## Backend

Blueprint `admin_users_bp` at `/api/admin` (`routes/admin/users.py`), all `@require_role(Role.MAINTAINER, Role.OWNER)`:

| Route | Purpose |
|---|---|
| `GET /users` | Master list. Cached on `db_seq` (`services/admin/users.py::list_users`, cache in `services/storage/cache.py`). |
| `GET /users/<hf_user_id>` | Per-user detail (lazy, uncached) — `repo_admin_users` aggregations. |
| `GET /visitor-stats` | Today's traffic rollup. Gated on env `INSPECTOR_VISITOR_ANALYTICS`; the compartment swallows failure. |
| `POST /users/<hf_user_id>/role` | **Owner-only** role change (`@require_role(Role.OWNER)` + `@require_same_origin`). |

Aggregation/reads: `services/db/repo_admin_users.py`, `repo_visitors.py`; visitor counting in `services/admin/visitors.py`. Schemas: `qua_shared/schemas/wire/admin_users.py` (codegen'd to FE types).

### Requests compartment

Routes live in `requests_bp` (`routes/claims/requests.py`); reads in `services/admin/requests.py`; schema `qua_shared/schemas/wire/admin_requests.py` (codegen'd).

| Route | Purpose |
|---|---|
| `GET /admin/requests?status=open\|accepted\|returned\|discarded` | Review-queue payload for one facet — catalog-joined (name/riwayah/style), proposed-changes diff over `ProposedEdits` fields, conflict flag, facet counts. Includes **slugless intake rows** (new-combo / new-reciter) alongside slug-based edit requests; intake rows carry `source` + `probe`. Maintainer+; **tier-redacted** (owners get `@login`+hf_id, maintainers role only). Base list cached on `db_seq` in `cache.py` (`get/set_admin_requests_cache`); per-caller overlay applied live. "New request" awareness is the rail's `request.received` alert — no unviewed badge / view-mark route. |

Resolution of **slug-based edit requests** stays on the existing per-slug routes, now **owner-only**: `POST /admin/request/<slug>/reject-{soft,hard}` (`@require_role(Role.OWNER)`; the `reciter.request_rejected_{soft,hard}` transition handlers use `_require_owner`). Acceptance is implicit (the alignment pipeline → `reciter.alignment_completed` applies the proposed edits + resolves the request to `accepted`).

#### Intake requests (new combination / new reciter)

The Submit-recitation wizard's two **slugless** types — `existing_reciter_new_combo` and `new_reciter` — carry an audio **source** (direct links / playlist; a dropped CSV/JSON file is normalised into `links[]` client-side). They land in the unified `requests` table with `slug = NULL`, everything parked in `payload` (`reciter_id`, `proposed_edits` (ProposedEdits-shaped), `source`, three required `attestations` (distribution/links-verified/storage rights — recorded for audit), cached `probe`). Service: `services/admin/intake.py`; validation `intake_validation.py`; probe `intake_probe.py`; schemas `qua_shared/schemas/wire/intake_requests.py` (codegen'd).

| Route | Purpose |
|---|---|
| `POST /requests/intake` | Submit (any signed-in user; `@require_same_origin`). Structural validation only — URL format (errors list malformed **chapter indices**), required combination + all three attestations, with 1–114 coverage / duplicates / playlist host as **warnings**. 400 carries `{errors, warnings}`. The wizard mirrors this client-side: live invalid/missing-chapter feedback in step 2, a step-4 confirm of the attestations. |
| `POST /admin/requests/<id>/probe` | **Owner-only.** Reachability probe of the source (ThreadPool HEAD/ranged-GET; playlist = single check). Caches `payload.probe`. Never on the submit path. |
| `POST /admin/requests/<id>/{return,discard}` | **Owner-only.** Id-based resolution for slugless rows — pure request-status mutation, **no** state-machine transition (no delivery/state row exists). ≥10-char reason. |
| `POST /admin/intake/<id>/ingest` | Server-to-server (offline pipeline bearer token) or owner cookie. Mints the delivery + seeds alignment. See [catalog.md](catalog.md)/the offline `ingest_intake.py`. |

**There is no accept step.** A submission lands `pending` and is directly ingestable — the owner's decision to *align* it (running the offline `ingest_intake.py` over the row) IS the acceptance, so the Requests **Open** facet just shows probe / send-back / discard (no Accept button). For a **new reciter** the canonical `reciter_id` (the lone human decision) is supplied by the operator at ingest time (`ingest_intake.py --reciter-id`), not stamped up front. Ingest flips the row to `accepted` + back-fills the slug (so the **Accepted** facet means *ingested*). `request.intake_*` are audit-only events (slugless, not in `_HANDLERS`) — silently dropped from both activity rails, no classification entry needed.

Request review is **no longer a notification**: `reciter.request_rejected_{soft,hard}` are `HIDDEN_EVENTS` and `reciter.requested` is public-prose-only (not `ADMIN_ONLY_EVENTS`) in `activity_classification.py`.

### Reviews compartment

Routes live in `admin_reviews_bp` (`routes/admin/reviews.py`); reads + per-admin view marks in `services/admin/reviews.py`; schema `qua_shared/schemas/wire/admin_reviews.py` (codegen'd). All endpoints `@require_role(Role.MAINTAINER, Role.OWNER)`.

| Route | Purpose |
|---|---|
| `GET /admin/reviews/list` | One whole-table JOIN over `delivery_states` + `deliveries` + `reciters` + open `claims`, filtered to the two states the slimmed tab covers (`awaiting_review`, `under_review`). Canonical rows (state + open-claim shape); the FE renders Under review [not marked-ready] + Available. Not cached — sub-second JOIN, refreshed on every admin action. |
| `GET /admin/reviews/<slug>` | General-drawer payload — base + current claim + claim history + transition timeline + `timestamps_job_ids` + `flagged_issues_count` (segments carrying a `flag`, from one cached `load_detailed`). Bounded queries + one detailed read; cheap enough to fetch eagerly on every drawer open. Also fed (lazily) by the Releases row's Timeline + Reviewers expands. 404 on unknown slug. |

> The marked-ready unviewed-count / per-admin view-mark surface (`/reviews/unviewed-count`, `/reviews/<slug>/view`, the `review_views` table + `repo_review_views`, the `unread` / `unviewed_marked_ready` schema fields) was **removed** with the Releases restructure — marked-ready work moved to Releases without a notification.

**Timestamps-generation** endpoints (`@require_capability("reviews.generate_timestamps")`, maintainer+) — drive the Releases-tab in-row `ReleasesTsSettings` / `ReleasesPastJobs` expands. Full subsystem: [timestamps-job.md](timestamps-job.md).
- `POST /api/admin/generate-timestamps/<slug>` — launch the in-container MFA (alignment-only) job. Body → `_parse_ts_settings` → `TsJobSettings` (`beam` → single-entry `beams`, optional `chapters` for affected-only regen, Advanced workers/flavor/timeout — no audio/peaks toggles). Valid from `under_review`+marked-ready (first publish → auto-release on success) or `released` (regen). 202 `{job_id, url}`; 409 if a job is already running for the slug; 400 invalid; 404 unknown. `@require_same_origin`. Does **not** transition the reciter at launch.
- `GET /api/admin/jobs/<job_id>` — live status + bounded log tail (HF authoritative).
- `GET /api/admin/jobs/<job_id>/record` — persisted record (settings + status + full logs) from `jobs/ts/<job_id>.json`; 404 if none.
- `GET /api/admin/reciters/<slug>/ts-jobs` — persisted records for the slug (newest first), for the Past-jobs expand's history list.

Reviewer-management mutations reuse existing per-slug routes:
- `POST /api/admin/claim/force-release/<slug>` — **owner-only** (`_require_owner` at both route + state-machine handler level). Used by the General-drawer popover's Remove path.
- `POST /api/admin/claim/reassign/<slug>` — **owner-only**. Used by the popover's Change path; resolves `to_login` server-side via `hf_users.lookup` before persisting `claim.reassigned`.
- `POST /api/admin/users/lookup` — maintainer+. Lookup-only (no mutation); the popover hits it on debounced input to render the resolved-user card.
- `POST /api/admin/send-back/<slug>` — maintainer+ (quality gate, not a claim mutation). Rejects marked-ready work for more review. Surfaced on the Releases **Ready-to-generate** row (the new home of marked-ready work); sending back returns the reciter to `under_review`, where it reappears in the Reviews tab.
- Publish / unpublish / unlock-for-revision are listed but **disabled** (see `docs/planning/reviews-tab-deferred.md`).

The owner-only tightening on force-release + reassign was a deliberate split: **owners manage who reviews; maintainers gate what ships**. Maintainers retain Send-back-to-UR but cannot eject or transfer claims.

### Jobs compartment (unified job view)

The **Jobs** tab is one place for every HF-Job kind — `timestamps`, `hf_publish`, `hf_publish_batch`, `cut_release`, `refresh_catalog` — running *and* historical, across all reciters. Clicking any row opens an in-app drawer (record + settings/logs, batch/cut members, "Open on HF", Cancel-if-running).

**Shared job-record store** (`services/admin/jobs/records.py`) — the single source the tab reads. Every kind's **server-side** `launch()` writes a `running` `JobRecord` and every terminal point (`complete()`, the webhook non-success branches, the cancel route) writes the `succeeded`/`failed` outcome via `record_terminal` (read-merge-write, so launch fields survive). The DB stays the source-of-truth for *releases*; these records are the uniform observability trail. Paths reuse `base.job_record_path` — `reciters/<slug>/jobs/<kind>/` (per-slug) and `jobs/_global/<kind>/` (global). `JobRecord` (`qua_shared/schemas/bucket/jobs.py`) is a superset of the legacy `TsJobRecord` the timestamps HF-Job container still self-writes, so old TS records parse uniformly (`records.read` injects `kind` from the path + normalizes status).

**Aggregator** (`services/admin/jobs/registry.py`) — `list_all_jobs()` unions `records.list_all()` with the live HF list (`base.list_in_flight_jobs`), dedups by `job_id` (live `running` wins), sorts newest-first, TTL-cached (`cache.{get,set,invalidate}_all_jobs_cache`, invalidated at every launch/complete/cancel). `job_detail()` enriches a running TS job with its live log tail.

| Endpoint | What |
|---|---|
| `GET /api/admin/jobs` | `JobsListResponse` — unified list + `running_count` (`reviews.view`). |
| `GET /api/admin/jobs/detail?kind=&job_id=&slug=` | One job's full `JobRecord` for the drawer (`reviews.view`). 404 if absent. |
| `POST /api/admin/release-jobs/<job_id>/cancel` | Cancel — reused (kind-generic, per-kind cap gates). |

> History caveat: `hf_publish` rows that predate the store, plus `cut_release`, are synthesized from the DB by the one-shot `scripts/backfills/backfill_job_records.py` (idempotent); going forward every kind records natively, including failures.

### Role change (`services/admin/users.py::set_role`)

Roles are tri-state with contributor **implicit** (no `role_assignments` row). `set_role` reads the current role and dispatches to the existing `services/auth/access.py` mutations so authz + audit + `durable_transaction` stay centralized:

| From → To | Action |
|---|---|
| contributor → maintainer/owner | `access.grant` |
| maintainer ↔ owner | `access.update` (in-place tier change) |
| maintainer/owner → contributor | `access.revoke(cascade_release=False)` — **preserves the user's open claim** (a contributor is a valid claim holder; demote ≠ offboard) |

Guard: refuse to remove the **last active owner** (`repo_access.active_owner_count`) → `LastOwnerError` → HTTP 409. Other mappings: `NotAuthorized`→403, `MemberNotFound`→404, bad input / `AccessError`→400.

> The legacy `/api/admin/access/{grant,revoke,update}` endpoints (`routes/admin/access.py`) remain — maintainer-capable, and `revoke` there keeps `cascade_release=True` (offboarding: auto-releases open claims). The picker is the owner-only path; offboarding is the access endpoints.

### Sync

No special wiring. `durable_transaction` bumps `db_seq` → the admin-users list cache self-invalidates on next read; `current_user()` resolves role fresh per request (no TTL), so the affected user becomes their new role on their next request. A self-demoting owner's own FE refreshes via `loadCurrentUser()` immediately (and on any reload at minimum).

See [auth-permissions.md](auth-permissions.md) for roles/predicates/edit-lock and [frontend.md](frontend.md) for the dashboard tab.
