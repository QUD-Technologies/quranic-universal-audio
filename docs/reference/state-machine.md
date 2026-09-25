# State Machine

> **Storage:** all reciter lifecycle state lives in SQLite (`inspector.db`), NOT a `reciter_state.json`. Tables: `delivery_states` (lifecycle), `claims` (assignee + marked_ready, normalized out), `transitions` (append-only event log). Each `state.transition()` is ONE durable SQLite transaction; on commit the DB is uploaded back to the bucket (CAS on `db/inspector.seq`) before the call returns (`services/db/sync.py::durable_transaction`). No per-slug lock — `BEGIN IMMEDIATE` + single-writer serializes same-slug writes.

Where it lives:

| Concern | Location |
|---|---|
| State machine / handlers / `_HANDLERS` | `inspector/services/state/state.py` |
| `delivery_states` read/write + `ReciterRow` assembly | `inspector/services/db/repo_state.py` |
| `transitions` append + read helpers | `inspector/services/db/repo_transitions.py` |
| `claims` (assignee/marked_ready) read/write | `inspector/services/db/repo_claims.py` |
| Audit facade (delegates to `repo_transitions.append`) | `inspector/services/state/audit.py` |
| Canonical row + enums (Pydantic) | `qua_shared/schemas/config/state.py` (`ReciterRow`, `ReciterState`, `Visibility`, `RevisionContext`) |
| Server-side predicates (`can_edit`, `can_claim`, …) | `inspector/services/auth/predicates.py` |
| Role/reason gates | `inspector/services/auth/permissions.py` |

`ReciterRow` is the read DTO. `assignee_hf_id` / `assignee_login` / `assignee_since` / `marked_ready` are NOT stored on `delivery_states` — they are LEFT-JOINed from the open `claims` row (`released_at IS NULL`) by `repo_state._assemble`, and only populated when `state == under_review`.

## Lifecycle states (column: `delivery_states.state`)

Enum `ReciterState` (`qua_shared/schemas/config/state.py`):

```
catalogued → awaiting_alignment → awaiting_review → under_review → released
```

> **No `awaiting_timestamps`.** Publishing is a single edge `under_review → released`, fired by the system only when the timestamps job succeeds. The reciter stays `under_review` (marked_ready) while the job runs, so a failed job is recoverable (re-run). See `services/admin/timestamps_jobs.py::complete_timestamps_job` + the `/api/webhooks/ts-job-complete` route.

> **TS regen on a `released` reciter is NOT a transition.** Re-running the timestamps job on an already-released reciter (the Releases-tab "Regenerate TS" action) must not re-fire `reciter.published` — there's no `released → released` edge. `complete_timestamps_job` branches on state: a `released` row takes `_regenerate_timestamps_on_released`, which records the new `ts` release, supersedes the prior, stamps the slug's HF/GH releases stale, and writes a `reciter.ts_regenerated` audit row (below) — all with no state change. Idempotent on `(track='ts', slug, version=job_id)`.

| State | Editable? | Required fields | Notes |
|---|---|---|---|
| `catalogued` | No | `state_since` | In catalog; no alignment. Entry point for `catalog.added` and re-request after soft-reject. |
| `awaiting_alignment` | No | `state_since` | Alignment pipeline pending/running. Has a pending request entry (see `services/state/pending_requests.py`). |
| `awaiting_review` | No (claimable) | `state_since` | Alignment done, files under `reciters/<slug>/`. No open claim. May carry `revision_in_progress` (set by `admin.unlocked_for_revision`). |
| `under_review` | Yes (assignee, when `marked_ready=0`, `visibility=public`) | `state_since`, open claim → `assignee_hf_id` + `assignee_since` | Claimed. `marked_ready` legal ONLY here. The timestamps job also runs against a marked_ready row (it stays here until the job succeeds). |
| `released` | No (maintainer direct-edit only) | `state_since` | Files + timestamps on bucket; publicly visible via Inspector. Terminal lifecycle state. Reached directly from `under_review` on timestamps-job success. |

Invariants (`ReciterRow._check_state_invariants`): assignee fields legal ONLY on `under_review`; `marked_ready` requires `under_review` + assignee; `revision_in_progress` legal ONLY on `awaiting_review`.

## Orthogonal columns

`delivery_states` columns (writable set in `repo_state._WRITABLE`) plus claim-derived fields:

| Field | Storage | Type | Meaning |
|---|---|---|---|
| `visibility` | `delivery_states` | enum | `public` (default) / `discarded` (hidden, any state). `archived` is **deferred** — not in the enum. |
| `visibility_reason` | `delivery_states` | str \| null | Reason set alongside `discarded` (or hard-reject). |
| `timestamps_job_ids` | `delivery_states` | list[str] (JSON) | Append-only; every MFA timestamps job dispatched. Appended at launch (`record_timestamps_job`, no transition) and re-appended idempotently by `reciter.published` on success. |
| `revision_in_progress` | `delivery_states` | `RevisionContext` \| null (JSON) | Set by `admin.unlocked_for_revision`, cleared by `reciter.published` / `reciter.unpublished`. Carries `unlocked_from_state` (`released`), `unlocked_at`, `unlocked_by_hf_id`, `original_assignee_hf_id` (currently always `None`). Only valid on `awaiting_review`. |
| `last_save_at` | `delivery_states` | datetime \| null | Column exists on `delivery_states`/`ReciterRow`, but **no state-machine transition currently writes it** (carried through unchanged by `_persist_state`); maintainer direct-edit timestamping on published reciters is not implemented. |
| `last_job_finished_at` | `delivery_states` | datetime \| null (ISO) | Stamped on a timestamps job's terminal state — success (alongside publish→released) and failure (no state change) — by `state.mark_timestamps_job_finished` (no transition; job bookkeeping). **Not on `ReciterRow`** (`repo_state._assemble` reads named columns only — no schema/codegen change). Audit-only since the Reviews-tab notification dot was retired (no current reader). |
| `created_by_transition_id` | `delivery_states` | str | FK to the `transitions` row that inserted the state row. |
| `state_since` | `delivery_states` | datetime | Set on every state change. |
| `assignee_hf_id` / `assignee_login` / `assignee_since` | `claims` (open row) | str / str / datetime | Claim holder. NOT on `delivery_states`. |
| `marked_ready` | `claims.marked_ready_at` (open row) | bool (derived: `IS NOT NULL`) | Edits frozen, awaiting maintainer publish. Reviewer can flip back. |

Force-claim columns (`force_assignee_*`, leases) do **not** exist — force-claim is deferred.

## Transition matrix (matches `_HANDLERS` exactly)

`actor role` column: `system` = server/pipeline/callback (no role gate beyond plumbing); `contributor+` = `is_contributor_or_higher`; `maintainer+` = `is_maintainer` (owner inherits); `owner` = `is_owner` (strict — maintainer is NOT enough). "reason ≥10" = `_require_reason` (≥`permissions.MIN_REASON_CHARS`); "reason optional" = no `_require_reason` call (route normalizes empty/short to `""`).

| Event | From | To | Other column changes | Actor / gate |
|---|---|---|---|---|
| `catalog.added` | (no row) | `catalogued` | inserts new row | maintainer+ |
| `catalog.edited` | (any) | (same) | none — audit-only | maintainer+ |
| `reciter.requested` | (no row) or `catalogued` (`public`) | `awaiting_alignment` | submits pending request (`proposed_edits`, `comments`, `auto_claim`) | contributor+ |
| `reciter.request_rejected_soft` | `awaiting_alignment` | `catalogued` | archives pending → `requests/returned.json` | owner, reason ≥10 |
| `reciter.request_rejected_hard` | `awaiting_alignment` | `catalogued` | `visibility=discarded`, `visibility_reason`; archives pending → discarded | owner, reason ≥10 |
| `reciter.alignment_completed` | `awaiting_alignment` | `awaiting_review` | applies + clears pending catalog edits; if pending had `auto_claim`, folds a `reciter.claimed` into the same txn. Fired by `auto_detect` when `reciters/<slug>/detailed.json` exists (the folder alone is not enough — the native align pipeline persists `audio/`+`peaks/` hours earlier; see [align-pipeline.md](align-pipeline.md)) | system |
| `reciter.claimed` | `awaiting_review` (`public`) | `under_review` | opens claim: `assignee_*`, `marked_ready=0` | contributor+ |
| `reciter.released` | `under_review` (`not marked_ready` when actor is claim holder) | `awaiting_review` | closes claim | claim-holder OR maintainer+ |
| `reciter.marked_ready` | `under_review` (+ all 5 checklist attestations True + 5 blocking validation counts == 0) | (same) | `marked_ready=1`; persists `mark_ready_checklist` + `mark_ready_comment_checks` + `mark_ready_comment_issues` onto the open claim | claim-holder |
| `reciter.unmarked_ready` | `under_review` | (same) | `marked_ready=0`; clears the three submission columns on the open claim | claim-holder |
| `reciter.merge_rejected` | `under_review` (`marked_ready=1`) | (same) | `marked_ready=0` | maintainer+, reason ≥10 |
| `reciter.published` | `under_review` (`marked_ready=1`) | `released` | closes claim; `marked_ready=0`; `revision_in_progress=None`; appends `job_id`→`timestamps_job_ids` | maintainer+ (gate `reciter.publish`); fired by the system (`SYSTEM_ACTOR`, role OWNER) on timestamps-job success |
| `reciter.unpublished` | `released` | `awaiting_review` | `revision_in_progress=None` | maintainer+, reason ≥10 |
| `reciter.discarded` | (any) | (same) | `visibility=discarded`, `visibility_reason` | maintainer+, reason ≥10 |
| `reciter.undiscarded` | (any `discarded`) | (same) | `visibility=public`, `visibility_reason=None` | maintainer+ |
| `claim.force_released` | `under_review` | `awaiting_review` | closes claim; `marked_ready=0` | owner, reason optional |
| `claim.reassigned` | `under_review` | (same) | reassigns claim: new `assignee_*`, `marked_ready=0` (requires payload `new_assignee_hf_id`+`new_assignee_login`) | owner, reason optional |
| `admin.unlocked_for_revision` | `released` | `awaiting_review` | `revision_in_progress={unlocked_from_state, unlocked_at, unlocked_by_hf_id, original_assignee_hf_id=None}` | maintainer+ |

> **Automated launches.** The release-automation reconciler ([automation.md](automation.md)) is an alternate *launcher* of these same job paths (generate-timestamps, cut, publish, refresh) on the owner's schedule/rules. The job-completion transition still fires with `SYSTEM_ACTOR` as above; the automation records `SYSTEM_AUTOMATION` as the job's `launched_by` + on its config-change audit, so the rail distinguishes an owner-configured automation from a manual action.

**Folded / audit-only events** (not in `_HANDLERS`, emitted directly via `repo_transitions.append`):
- `reciter.ts_regenerated` — written by `complete_timestamps_job` when a timestamps job succeeds on an already-`released` reciter (regen). No state change (`from_state=to_state=released`); payload `{job_id}`. Classified `hidden` in `activity_classification`. Visibly distinct from the first publish's `reciter.published`.
- `reciter.ts_refreshed` — written by `repo_releases.mark_ts_refreshed` when an **out-of-band** TS shard refresh (manual backfill / schema-bump re-stamp / local regen uploaded straight to the bucket) is reported via the internal endpoint `POST /api/admin/internal/ts-refreshed` (secret-gated, same `X-Inspector-Job-Secret` as the job-completion webhook). No state change (`from_state=to_state=released`); payload `{chapters, reason}`. Advances the current `ts` release `produced_at` (so `ts_stale_info` recomputes clean) and re-stamps HF/GH stale (`TS_REGEN`) — making a bucket-direct write no longer silent. Idempotent. Classified `hidden`. The shared `qua_shared.inspector_notify.notify_ts_refreshed` helper (best-effort POST) is what the backfill scripts call after a successful upload.
- `reciter.auto_claim_skipped` — written inside the alignment txn when a non-owner auto-claim requester already holds another claim (`_maybe_auto_claim`).
- `request.intake_{submitted,accepted,returned,discarded}` — slugless new-combo / new-reciter intake lifecycle (`services/admin/intake.py`). Slugless ⇒ silently dropped from both activity rails. **None of these fire a state-machine transition** — an unaccepted intake has no delivery/state row, and **accept doesn't create one either**: it just records the owner-confirmed `reciter_id` (new_reciter) and flips the request to `accepted`. The catalog reciter/delivery + state row + slug are minted later by the **online intake mint**, once the plan has enumerated the source and the owner has reviewed chapters + identity: `POST /api/admin/intake/<rid>/align` (`services/admin/intake_plan/mint.py` → `services/admin/intake.py::ingest`, audited `request.intake_ingested`) mints reciter+delivery+slug, writes the `audio_manifest` sidecar, and fires `reciter.requested` to seed `awaiting_alignment` — making ingest the **second caller** of that event, alongside the dashboard edit-request route. The seed ordering is load-bearing: `reciter.alignment_completed` is the sole `awaiting_alignment → awaiting_review` edge and fires **only** when `auto_detect` sees `reciters/<slug>/` for a slug already in `awaiting_alignment`, so ingest must seed the state row **before** content lands — the mint always completes before `align_runs.start`, and the align pipeline's assemble writes `detailed.json` last; a folder that lands without the seeded row is silently ignored.

## Mark-ready submission

`reciter.marked_ready` is not a bare flag-flip — it carries a **submission payload** that the reviewer fills via a modal form before the POST. The payload + the live validation counts both gate the transition; once accepted, the submission is persisted on the open claim row so admins can audit it from the Reviews drawer.

Wire shape (`qua_shared/schemas/wire/mark_ready.py::MarkReadyRequest`):

```json
{
  "checklist": {
    "failed_alignments": true,
    "missing_words": true,
    "low_confidence": true,
    "splits_wasl_waqf": true,
    "basmala_amin_intros": true
  },
  "comment_checks": "...",   // optional, ≤4000 chars
  "comment_issues": "..."    // optional, ≤4000 chars
}
```

The five checklist keys mirror to the FE copy module at `inspector/frontend/src/tabs/segments/copy/mark-ready/index.ts`. The labels themselves live in sibling `.md` files (`form.md`, `checklist.md`, `comments.md`) so wording edits don't require touching components. Parity is asserted by `inspector/frontend/src/tabs/segments/__tests__/parity/mark-ready-copy.test.ts` and by the schema codegen guard.

Two gates run inside `_h_marked_ready` before the transition is accepted:

1. **Checklist completeness.** All five values MUST be `True`. Any `False` → `InvalidTransition("checklist incomplete", details={unchecked: [...]})`.
2. **Blocking validation counts.** The handler calls `services.validation.validate_reciter_segments(slug)` and checks the five keys in `BLOCKING_COUNT_KEYS` (`qua_shared/schemas/wire/mark_ready.py`) against the live `category_counts` (`boundary_adj` is owner-only and deliberately absent; resolved cross-verse items are not counted):

   | Key | Accordion |
   |---|---|
   | `low_confidence` | Low confidence |
   | `low_confidence_v2` | Low confidence v2 |
   | `cross_verse` | Cross-verse |
   | `basmala_amin` | Basmala + amin |
   | `repetitions` | Repetitions |

   Any non-zero count → `InvalidTransition("blocking validation counts must be zero before mark-ready", details={blocking_counts: {...}})`. The client mirrors the same gate via `$segValidation?.category_counts` as a UX guard, but the server is authoritative.

Persisted columns on `claims` (the four `mark_ready_*` columns added in migrations 0007 + 0009):

| Column | Type | Cleared on |
|---|---|---|
| `mark_ready_checklist` | TEXT (JSON) | `unmarked_ready`, `released`, `force_released`, `reassigned` (clears the *open* claim only; closed history rows keep their submission) |
| `mark_ready_comment_checks` | TEXT | same |
| `mark_ready_comment_issues` | TEXT | same |
| `mark_ready_bypass_used` | INTEGER (0/1) | same. Set to 1 when an owner (or a tier granted `claim.mark_ready_skip_gates`) submitted without the form. |

These ride alongside `marked_ready_at` and are surfaced on `AdminReviewOpenClaim.mark_ready_submission` / `AdminReviewClaimHistoryEntry.mark_ready_submission` for the Reviews drawer. A send-back-and-re-mark cycle writes fresh values onto the new open claim; the prior cycle's submission lives on the closed history row.

`InvalidTransition` carries an optional `details` dict (added when the mark-ready gate fails); the app-level error handler at `inspector/app.py` includes it in the JSON envelope so the FE can render structured guidance instead of a flat error string.

### Owner-bypass submission

Holders of the `claim.mark_ready_skip_gates` capability (owners by default, plus any tier the owner grants it to via the Permissions tab) skip both gates entirely. The route accepts an empty body; the handler short-circuits the Pydantic validation, the unchecked-keys gate, and the live `validate_reciter_segments` count check, then stamps `bypass_used=True` on the persisted submission. `mark_ready_checklist` is `NULL` for these rows — the admin Reviews drawer renders an "owner bypass" pill plus a single explanatory line in place of the checklist tick list, on both current claim and closed history entries. The route's capability check is a UX shortcut; the handler always re-checks the capability, so the bypass can't be exploited by a tier that lost the cap mid-flight.

## What's NOT a state

- `ready_for_merge` — superseded by `marked_ready` (a `claims` flag on `under_review`).
- `awaiting_timestamps` — **removed.** Publish is now the single `under_review → released` edge fired on timestamps-job success; the reciter sits in `under_review` (marked_ready) while the job runs.
- `discarded` — superseded by `visibility='discarded'`; round-trip preserves lifecycle `state`.
- `archived` — deferred; not in the `Visibility` enum.
- `completed` / "published to HF dataset" — **not** a lifecycle state. `ReciterState` ends at `released`; dataset publication is out-of-band and not modeled in the state machine.

## What's NOT a transition

- `under_review → released` directly **by a human** — there's no manual publish button; the only thing that fires `reciter.published` is the timestamps-job completion path (webhook + poll fallback), with `SYSTEM_ACTOR`. The maintainer's deliberate action is launching Generate-TS on a marked-ready row (gated `reviews.generate_timestamps` + `reciter.publish`).
- `released → released` on TS regen — re-running Generate-TS on an already-released reciter records a new `ts` release + `reciter.ts_regenerated` audit row but fires **no** transition (see the note box up top). The same route (`POST /api/admin/generate-timestamps/<slug>`) handles both first publish (marked-ready row) and regen (released row); any other state 409s.
- Wildcard `state.manual_override` — replaced by discrete `admin.*` / `claim.*` events; each new recovery scenario gets a new named handler, not a free-form override.
- Direct contributor re-edit of `released` — re-edits are maintainer-only, via `admin.unlocked_for_revision` to reopen back to `awaiting_review`.
- Force-claim / force-acquire — entirely deferred (no lease, no `force_assignee_*`).

## Server-side predicates (`services/auth/predicates.py`)

Pure `(row, user) → bool`; anonymous user → `False`. Backend re-checks on every mutating route — predicates only drive FE visibility. `build_predicates(row, user, *, has_other_active_claim)` returns the response map.

| Predicate | Condition |
|---|---|
| `can_claim` | `state=awaiting_review` ∧ `visibility=public` ∧ signed in ∧ (owner OR `not has_other_active_claim`) |
| `can_edit` | `state=under_review` ∧ `not marked_ready` ∧ `visibility=public` ∧ `is_claim_holder` |
| `can_edit_as_admin` | maintainer+ ∧ `state=under_review` ∧ `not marked_ready` ∧ `visibility=public` |
| `can_edit_as_owner` | owner ∧ `not marked_ready` ∧ `visibility=public` (any state) |
| `can_mark_ready` | `can_edit` ∧ `not marked_ready` |
| `can_unmark_ready` | `state=under_review` ∧ `marked_ready` ∧ `is_claim_holder` |
| `can_release` | `state=under_review` ∧ `not marked_ready` ∧ `is_claim_holder` |

All claim-ownership checks use `hf_user_id`, never `login`. One-claim-per-user is enforced via `claims` index (`state.has_other_active_claim` / `repo_claims.open_claim_for_user`); owners are exempt.

## Transition audit

Every `transition()` writes exactly one `transitions` row (FK-first, before the `delivery_states`/`claims` diffs) via `repo_transitions.append`, enrolled in the same transaction (SAVEPOINT when nested). Columns: `id`, `seq`, `ts`, `slug`, `event`, `from_state`, `to_state`, `actor_id`, `actor_login_snapshot`, `actor_role_snapshot`, `reason`, `result`, `payload` (JSON), `content_hash`. Actor snapshot = `{hf_user_id, login_at_time (cookie snapshot, never refetched), role}`. `repo_transitions` read helpers (`for_slug`, `since`, `feed`) back the activity feeds.

After the transition row + state/claim diffs, `_apply_event` calls `services.notifications.emit_for_event` (best-effort, inside the same txn) to fan the event out to the affected user's "My Notifications" rail — e.g. the requester on a soft/hard reject, the prior assignee on `claim.force_released`, the requester on the auto-claim fold. The requester for reject/alignment events is captured BEFORE the handler runs (the pending row is archived mid-handler). See [notifications.md](notifications.md).
