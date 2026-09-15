# Inspector — Auth, Permissions, Activity Rails

Single source for identity, role gating, edit-lock, CSRF, admin endpoints, audit actor, activity rails.
Code wins over prose. All paths relative to repo root.

Auth code lives in `inspector/services/auth/` (package, not a flat `services/auth.py`).
Flat shims re-export it: `from services import auth`, `from services import permissions`, `from services import predicates`, `from services import access`, `from services import hf_users`, `from services import public_activity`, `from services import activity_state` all resolve to the subpackage modules.

---

## Identity

| Concept | Truth |
|---|---|
| Identity cookie | `inspector_session`, signed via `itsdangerous.URLSafeTimedSerializer` (key = `INSPECTOR_SESSION_SECRET`, salt `inspector-session-v1`) |
| Cookie payload | `{login, hf_user_id, iat}` — **NO `role`, NO `csrf`** (`auth.py::encode_session`) |
| Cookie max-age | `SESSION_COOKIE_MAX_AGE = 60*60*24*SESSION_COOKIE_MAX_AGE_DAYS` (default 30 days; override per-Space via `INSPECTOR_SESSION_MAX_AGE_DAYS`); enforced in `decode_session` via `loads(..., max_age=...)` |
| Cookie flags (deployed) | `httponly=True`, `secure=True`, `samesite="None"` (iframe context, `INSPECTOR_BEHIND_PROXY=1`) |
| Cookie flags (local) | `secure=False`, `samesite="Lax"` (plain HTTP rejects Secure) |
| Role | Resolved **fresh per request** via `access.resolve_role(hf_user_id)` — never carried in cookie. Revoked maintainer → contributor on next request, no re-login |
| OAuth-state cookie | Flask default `session` cookie (signed via `app.secret_key`), ~30s, used only between `/authorize` and `/callback` (Authlib internal) |
| Canonical key | `hf_user_id` (OIDC `sub`, immutable). `login` is display-only, refreshable cache. Predicates/claims/dismissals/tombstones all key on `hf_user_id` |

### HF OAuth flow (`routes/auth/auth.py`, `services/auth/auth.py`)

| Step | Route / fn | Behavior |
|---|---|---|
| init | `auth.py::init_oauth(app)` | Registers `huggingface` provider; `server_metadata_url` from `OPENID_PROVIDER_URL` (default `https://huggingface.co`); scope `OAUTH_SCOPES` (default `openid profile`); idempotent |
| login | `GET /api/auth/login?return=<path>` | 503 if `is_oauth_configured()` false; stores `_safe_return_path` in flask session; `authorize_redirect(_callback_url())` |
| callback | `GET /api/auth/callback` | exchanges code; reads `userinfo.sub` → `hf_user_id`, `preferred_username`/`name` → `login`; `encode_session(login, hf_user_id)`; sets cookie; 302 to safe return path |
| logout | `POST /api/auth/logout` | clears cookie (`max_age=0`). Claims survive |
| me | `GET /api/me` | `{login, hf_user_id, role, active_claim, active_claims, dev_mode}`; uniform null-filled shape when anonymous. `active_claim` = the **blocking** claim (open, not-marked-ready; via `repo_claims.open_claim_for_user`) — the field every FE one-claim gate keys off; marking ready releases the one-at-a-time hold so it drops to null. `active_claims` = the full under-review set assigned to the user (a marked-ready reciter stays in it; drives the picker's "mine" highlight) |
| current user | `auth.py::current_user() -> User \| None` | reads cookie → `decode_session` → `resolve_role`; returns `User(hf_user_id, login, role)` frozen dataclass with live `role` |

`is_oauth_configured()` = a client id resolved by `oauth_client_credentials()` AND `get_session_secret()` doesn't raise `MissingSecret`. That resolver prefers `INSPECTOR_OAUTH_CLIENT_ID`/`INSPECTOR_OAUTH_CLIENT_SECRET` (an OAuth app we registered, needed on a custom domain — HF's auto-provisioned app allow-lists only the Space's own hosts) and otherwise falls back to the `OAUTH_CLIENT_ID`/`OAUTH_CLIENT_SECRET` pair HF injects for `hf_oauth: true`. Scopes come from `OAUTH_SCOPES`, default `openid profile`.
`_safe_return_path` rejects absolute/protocol-relative URLs (open-redirect guard) → falls back to `/`.

### Local-dev identity (`auth.py::_dev_current_user`)

| Concept | Truth |
|---|---|
| Gate | `is_dev_mode()` = `INSPECTOR_DEV_MODE == "1"` **and** `INSPECTOR_BEHIND_PROXY != "1"` (auto-set by `app.py` locally outside pytest). Forced off behind the deployed proxy; `app.py` aborts boot (`assert_dev_mode_safe`) if both are set, so the bypass can never run on a Space |
| Cookie | `inspector_dev_role` (unsigned, only honored in dev mode), default `"owner"` |
| Valid values | `DEV_ROLE_VALUES = ("owner","maintainer","contributor","anonymous")` |
| `"anonymous"` | → `current_user()` returns `None` (explicit opt-out) |
| Synthetic id | per-role: `hf_user_id = "dev-<role>"`, `login = "dev-<role>"` — distinct identity per role so switcher simulates distinct prod users (per-user dismissals/claims/audit scope correctly) |
| Override | `INSPECTOR_DEV_<ROLE>_HF_ID` / `INSPECTOR_DEV_<ROLE>_LOGIN` env vars (set when local dev points at prod bucket so audit attributes to real HF user) |
| Garbage cookie | falls back to `Role.OWNER` (don't 500 a dev page) |
| Switcher route | `POST /api/dev/role` (`routes/auth/dev.py`) — `abort(404)` first if not dev mode; sets cookie 30-day, `samesite="Lax"` |

---

## Roles (`qua_shared/schemas/config/access.py::Role`)

Tiers: `contributor < maintainer < owner`. (`Role.PIPELINE` exists but is never a user identity — only stamped on offline-extraction edit-history batches; never reaches the HTTP auth path.)

Stored in **SQLite** `users` + `role_assignments` (`services/db/repo_access.py`), NOT JSON. `contributor` is implicit (any signed-in HF user with no active assignment → `Role.CONTRIBUTOR`); only `maintainer`/`owner` are explicit rows. Partial-unique index `ux_role_active` = one active role per user. Revoke is soft (`revoked_at`/`revoked_by`/`reason`).

| Tier | Can do (cumulative) |
|---|---|
| contributor | sign in; claim `awaiting_review` row; edit own claim; mark/unmark own claim ready; release own claim; submit reciter request |
| maintainer | + admin overrides (force-release, reassign, force-set-state, send-back/merge-rejected, request reject soft/hard); admin activity rail (redacted identity); per-user dismiss/undismiss; grant/revoke/update **maintainers**; HF user lookup |
| owner | + grant/revoke/update **owners**; see actor identity on both rails; delete (tombstone) public-rail cards; **edit any public non-frozen reciter regardless of state, no claim**; **hold multiple simultaneous claims**; undiscard |

Owner-asymmetry (`access.py`): only an OWNER can revoke/update an OWNER member (`permissions.is_owner(target) and not permissions.is_owner(actor)` → `NotAuthorized`). Granting OWNER requires OWNER; granting MAINTAINER requires MAINTAINER+. `revoke()` is atomic with cascade — releasing every open claim the user holds (`reciter.released` per slug via `state._apply_event`) in the same `durable_transaction`.

`bootstrap(hf_user_id, login)` seeds the first OWNER into an empty table (refuses if any active member exists).

The "Can do (cumulative)" table above is now the **default** for each tier, not a hardcoded ceiling — an owner can deviate per the capability system below.

---

## Capabilities (data-driven authorization)

> **Canonical doc: [capabilities.md](capabilities.md)** — the full model + **the convention for adding a gate so it surfaces in the Permissions tab**. This section is the summary.

The static tier model is now a **default baseline** over a data-driven capability matrix. An owner can toggle any capability on/off per tier (anonymous / contributor / maintainer) from the **Admin → Permissions** tab; the change is durable + audited and takes effect on the affected user's next request. **The resolver `can()` is the single source of authz** — every state handler, route, predicate, and edit-lock branch calls it.

| Piece | Truth |
|---|---|
| Registry (data) | `qua_shared/schemas/config/capabilities.py` — `Capability{id, group, label, description, anon_eligible, owner_only_fixed, default_grants}` + `CAPABILITIES` tuple. `default_grants` encodes **today's exact behavior** (empty override table == legacy authz, guarded by `tests/services/test_capabilities.py::test_baseline_parity`). Backend-only; NOT codegen'd to the FE. |
| Resolver (logic) | `services/auth/capabilities.py` — `tier_of(user_or_actor)` (`None`→`anonymous`), `resolve_grants() -> {(cap,tier): bool}` (baseline ⊕ overrides, cached on `db_seq`), `can(user_or_actor, cap)`, `capabilities_for(user)`. |
| Override store | `permission_overrides(capability_id, tier, allowed, set_by, set_at)` (migration `0007`, `services/db/repo_permissions.py`). Stores **only deviations**; absent row → default; reset = `DELETE`. |
| Cache | `services/storage/cache.py::{get,set,invalidate}_capability_matrix_cache` — single `(db_seq, matrix)` tuple. Any committed override write bumps `db_seq` → transparent invalidation (no restart). |

**Invariants the resolver enforces regardless of the override table:**
- **Owner is a superuser** — `can(owner, anything) is True`.
- **`manage_permissions` is owner-only fixed** (`owner_only_fixed=True`) — never grantable to a lower tier even if a row is forced in. It gates the Permissions tab + endpoints; the recovery anchor (only owners can change permissions → the system can't be locked out).
- **Anonymous only where `anon_eligible`** — action capabilities (need an identity/claim) are always `False` for anonymous; only the public-read caps (`view.catalog`, `view.public_activity`) are anonymous-toggleable.
- **Structural integrity is NOT a capability** — the last-active-owner guard and owner-on-owner revoke asymmetry stay enforced (in `services/admin/users.py` / `services/auth/access.py`) even if `roles.*` is toggled on for maintainers.

Capability groups (registry `group`): Public read & visibility · Reciter lifecycle · Requests & intake · Claims & review · Roles & access · Activity & moderation · Admin surfaces · Identity disclosure. Each gated transition maps to a capability via `services/state/state.py::_EVENT_CAPABILITY` (anti-drift test: `test_event_capability_map_is_complete_and_valid`).

### Enforcement entry points (all call `can()`)
| Layer | Wrapper / call | Failure |
|---|---|---|
| State handlers | `state.py::_require_capability(actor, cap)` (inline in each `_h_*`, at the same point the legacy `_require_<role>` sat → 400-before-403 precedence preserved) | `NotAuthorizedForTransition` → 403 |
| Route decorator | `utils/decorators.py::@require_capability(cap)` (injects `user`; anon allowed only for `anon_eligible` caps) | 401 anon / 403 wrong tier |
| Route helper (inline) | `routes/_admin_helpers.py::require_capability_or_403` + `actions.py::_require_cap` | `(json, 403)` |
| Edit-lock | `require_edit_lock` assignee path → `can(user, "segment.edit")`; admin-bypass arm → `can(user, "segment.edit_as_admin")` (owner branch unchanged) | `abort(403)` |
| Redaction | route computes `can(caller, "identity.see_actor")` → bool into `public_activity.feed`/`requests` (services stay Flask-free) | identity redacted |
| Access service | `access.py::_require_capability` → `roles.assign_maintainer` / `roles.assign_owner` (picker + `/api/admin/access/*` unified on the same caps) | `NotAuthorized` → 403 |
| FE (global) | `/api/me.capabilities[]` + `lib/stores/capabilities.ts::can(id)` / `hasCapability(user,id)` | hides/disables UI |

> **Residual (intentional):** `reciter.released` keeps `_require_claim_holder_or_maintainer` (self-release is ownership; a maintainer releasing another's claim via `/api/release` is pre-existing behavior with its own test). FE gating migration so far covers the activity rail (`ActivityRail.svelte` → `activity.delete` / `identity.see_actor`); the segments editor (`editing-mode.ts`) + requests/reviews drawers still gate on role in the UI — the backend enforces the capability regardless, so a revoked maintainer's action 403s rather than being hidden.

Endpoints: `GET /api/admin/permissions` (grouped matrix) + `POST /api/admin/permissions/<cap>/<tier>` (`{allowed}` or `{reset:true}`), both `@require_capability("manage_permissions")`; POST stacks `@require_same_origin`, instant, audited (`access.permission_changed`, a `HIDDEN_EVENTS` audit-only event). Service: `services/admin/permissions.py`. UI: see [admin-dashboard.md](admin-dashboard.md).

---

## Predicates

### `services/auth/permissions.py` — pure role + reason (Flask-free, exception-free except programmer error)

| Predicate | Returns |
|---|---|
| `role_of(obj)` | `Role` enum; normalizes str/enum/`Member`/`User`/`Actor`. Raises `ValueError` on malformed (= bug) |
| `is_owner(obj)` | `role == OWNER` |
| `is_maintainer(obj)` | `role ∈ {MAINTAINER, OWNER}` (elevated tier) |
| `is_contributor_or_higher(obj)` | `role ∈ {CONTRIBUTOR, MAINTAINER, OWNER}` (sanity vs anonymous) |
| `has_role(obj, *allowed)` | membership test |
| `is_claim_holder(obj, row)` | `row.assignee_hf_id == obj.hf_user_id` (canonical id; False if no assignee) |
| `is_claim_holder_or_maintainer(obj, row)` | `is_maintainer(obj) or is_claim_holder(obj, row)` |
| `normalize_reason(raw, min_chars=MIN_REASON_CHARS)` | trimmed reason ≥ min, else `None` |

`MIN_REASON_CHARS = 10`. Re-exported by `routes/_admin_helpers.py`.

### `services/auth/predicates.py` — reciter-task predicates (drive `/api/reciter-task/<slug>` response; backend re-checks on mutate)

Anonymous (`user is None`) → all `False`.

| Predicate | Condition |
|---|---|
| `can_claim` | `state==AWAITING_REVIEW` + `visibility==PUBLIC` + signed in + (owner OR `not has_other_active_claim`) |
| `can_edit` | `state==UNDER_REVIEW` + `not marked_ready` + `PUBLIC` + `is_claim_holder` |
| `can_edit_as_admin` | maintainer/owner + `UNDER_REVIEW` + `not marked_ready` + `PUBLIC` (any assignee) |
| `can_edit_as_owner` | owner + `not marked_ready` + `PUBLIC` (any state) |
| `can_mark_ready` | `can_edit` + `not marked_ready` |
| `can_skip_mark_ready_gates` | `can_mark_ready` + `can("claim.mark_ready_skip_gates")` — owners by default; other tiers only if owner-granted via the Permissions tab. FE branches `onMarkReady` on this: when true, POSTs an empty body to `/api/mark-ready/<slug>` and skips the form modal entirely. |
| `can_unmark_ready` | `UNDER_REVIEW` + `marked_ready` + `is_claim_holder` |
| `can_release` | `UNDER_REVIEW` + `not marked_ready` + `is_claim_holder` |

Note on `can_release`: once the reviewer has marked the row ready, self-release is blocked at both the predicate AND the state handler (`_h_released` raises `InvalidTransition("release blocked: unmark ready first, or ask an admin to send back")` when the claim holder tries it). Admin force-release / send-back via the Reviews drawer remains available. The segments-tab footer hides Claim / Mark Ready / Unclaim entirely after mark-ready and surfaces only a passive "Marked ready" status pill.

`build_predicates(row, user, *, has_other_active_claim)` returns the full map; route handlers call it (no drift).

---

## Where authorization is enforced

**Tier authorization routes through the capability resolver `can()`** — see the *Enforcement entry points* table in [§ Capabilities](#capabilities-data-driven-authorization) above and [capabilities.md](capabilities.md). The remaining **non-capability** gates:

| Layer | Wrapper / call | Failure |
|---|---|---|
| Ownership (claim holder) | `permissions.is_claim_holder` / `is_claim_holder_or_maintainer` in state handlers + edit-lock | "is this *your* row" — NOT a capability; `NotAuthorizedForTransition`→403 / `abort(403)` |
| Edit-lock (state/visibility) | `utils/decorators.py::require_edit_lock(admin_bypass=)` — owner bypass + `UNDER_REVIEW`/`not marked_ready`/`PUBLIC` preconditions; the **tier** dimension delegates to `can("segment.edit" / "segment.edit_as_admin")` | `abort(401/403/404)` |
| CSRF | `utils/decorators.py::require_same_origin` | `abort(403)` |
| Structural integrity | last-active-owner guard + owner-on-owner revoke asymmetry (`services/admin/users.py`, `services/auth/access.py`) — enforced regardless of `roles.*` toggles | `LastOwnerError`→409 / `NotAuthorized`→403 |
| Frontend | `lib/stores/capabilities.ts::can(id)` / `hasCapability(user,id)` (capability-aware); `isOwner` still gates the owner-only Permissions tab itself | hides/disables UI |

`@require_role` / `require_role_or_403` still exist (generic, unit-tested) but have **no route call-sites** — the guard test `tests/test_capability_convention.py` forbids `@require_role` in `routes/`.

**First-edit guide gate (FE-only onboarding).** `syncEditingMode` returns
`viewReason: 'guides_unread'` before **any** editable kind (owner/editor/
maintainer) whose `currentUser.guides_read` doesn't yet cover
`REQUIRED_GUIDE_KEYS`. Applies to **all editing roles** — no dev-mode or admin
exemption. The `editGate` action opens `GuidesGateModal` instead of the usual
popover; keyboard edit shortcuts (`E`/`S`/`Enter`) honour the same check via
`gateKeyboardEdit()` in `tabs/segments/utils/keyboard.ts`. Reading every guide
lifts the gate. This is UX, **not** authorization — there is no backend
enforcement (`require_edit_lock` is unchanged); a user who bypasses the FE still
can't save anything they lack `segment.edit` for. Read marks persist in
`guide_views` and surface on `/api/me` as `guides_read`. See
[`accordion-guides.md`](accordion-guides.md).

**New gated action** = add a `Capability` to the registry + gate via `can()` / `@require_capability` / `_require_capability`. Never hardcode `Role(...) in (...)` or `is_maintainer()` at a new gate — route it through the resolver so it's owner-toggleable and surfaces in the Permissions tab. Full recipe: [capabilities.md § Adding a capability](capabilities.md).

---

## Edit lock (`utils/decorators.py::require_edit_lock(reciter_param="reciter", *, admin_bypass=False)`)

Order of refusals (first match wins):

1. `current_user() is None` → `abort(401)` "authentication required"
2. missing `slug` in route kwargs → `abort(400)`
3. `state_service.get_row(slug) is None` → `abort(404)` "unknown reciter"
4. **owner branch** (`is_owner(user)`): bypasses state check; still refuses `marked_ready` (403 frozen) and `visibility != PUBLIC` (403)
5. **non-owner branch**: `state != UNDER_REVIEW` → 403; `marked_ready` → 403; `visibility != PUBLIC` → 403; then `is_assignee = is_claim_holder` OR `is_admin = admin_bypass and is_maintainer` — neither → 403 "not editable by this user"
6. On success sets `g.current_user`, `g.current_row` (route builds `Actor` without re-resolving)

`admin_bypass=True` lets maintainer/owner edit any `UNDER_REVIEW` row regardless of assignee. Owner bypass needs no flag. `marked_ready` rows are NEVER editable via this gate (reviewer's "continue editing" flips `marked_ready=False` first).

Claim/release/mark/unmark routes (`routes/claims/claims.py`) do **not** use `require_edit_lock` — `state.transition` handlers own ownership + precondition checks.

---

## CSRF + same-origin (`utils/decorators.py::require_same_origin`)

| Method | Behavior |
|---|---|
| GET/HEAD/OPTIONS | always allowed (no mutation) |
| POST/PUT/DELETE | require `Origin` (preferred) or `Referer` whose scheme+netloc match `request.scheme`+`request.host`; mismatch → `abort(403)`; both headers missing → `abort(403)` |

Defense-in-depth on top of `SameSite` cookie. Stack `@require_same_origin` above `@require_role` on every mutating admin/claim route.

---

## Admin endpoints by tier

`@require_role` requires signed-in + tier; mutating routes also stack `@require_same_origin`. Reason validated by `_admin_helpers.validate_reason` (≥`MIN_REASON_CHARS`=10 → else 400) unless noted.

| Endpoint | Required role | Reason ≥10 | Audit event | Source |
|---|---|---|---|---|
| `POST /api/claim/<slug>` | signed-in (contributor+) | — | `reciter.claimed` | `routes/claims/claims.py` |
| `POST /api/release/<slug>` | signed-in | — | `reciter.released` | claims.py |
| `POST /api/mark-ready/<slug>` | signed-in | — | `reciter.marked_ready` — body is a `MarkReadyRequest` (6-key checklist + 2 optional comments) and the handler also re-computes the 6 blocking validation counts. Holders of `claim.mark_ready_skip_gates` (owners by default) may POST an empty body to skip both gates; the handler stamps `bypass_used=True` on the persisted submission. | claims.py |
| `POST /api/unmark-ready/<slug>` | signed-in | — | `reciter.unmarked_ready` | claims.py |
| `GET /api/reciter-task/<slug>` | open | — | (read; predicates) | claims.py |
| `POST /api/reciter/<slug>/request` | contributor+ | — | `reciter.requested` | `routes/claims/requests.py` |
| `GET /api/admin/request/<slug>` | maintainer+ | — | (read; owner sees requester identity) | requests.py |
| `POST /api/admin/request/<slug>/reject-soft` | maintainer+ | yes | `reciter.request_rejected_soft` | requests.py |
| `POST /api/admin/request/<slug>/reject-hard` | maintainer+ | yes | `reciter.request_rejected_hard` | requests.py |
| `POST /api/admin/reciter/<slug>/undiscard` | **owner** | yes | `reciter.undiscarded` | requests.py |
| `POST /api/admin/claim/force-release/<slug>` | maintainer+ | yes | `claim.force_released` | `routes/admin/actions.py` |
| `POST /api/admin/claim/reassign/<slug>` | maintainer+ | yes | `claim.reassigned` | actions.py |
| `POST /api/admin/state/force-set/<slug>` | maintainer+ | yes | `admin.force_set_state` | actions.py |
| `POST /api/admin/send-back/<slug>` | maintainer+ | yes | `reciter.merge_rejected` | actions.py |
| `POST /api/admin/users/lookup` | maintainer+ | — | (no mutation; HF proxy) | actions.py |
| `POST /api/admin/access/grant` | maintainer+; OWNER role → owner | yes | `access.role_granted` | `routes/admin/access.py` |
| `POST /api/admin/access/revoke` | maintainer+; OWNER target → owner | yes | `access.role_revoked` (+ cascade `reciter.released`) | access.py |
| `POST /api/admin/access/update` | maintainer+; OWNER role → owner | optional (≥10 if present) | `access.role_updated` | access.py |
| `GET /api/admin/activity` | maintainer+ | — | (read; `no-store`) | `routes/admin/activity.py` |
| `POST /api/admin/activity/dismiss` | maintainer+ | — | `activity.dismissed` | activity.py |
| `POST /api/admin/activity/undismiss` | maintainer+ | — | `activity.undismissed` | activity.py |
| `DELETE /api/public/activity/<audit_id>` | **owner** | yes | `admin.activity_deleted` | activity.py |
| `GET /api/public/activity` | open | — | (read; owner gets actor identity; `no-store`) | `routes/public/public.py` |

`access.grant`/`update` enforce OWNER-tier checks twice — `_require_role_or_403` at route for a clean 403, and again in `services/access.py` (`NotAuthorized` → 403). `reassign` resolves `to_login` via `hf_users.lookup` (HF public `/api/users/<login>/overview`) → canonical `hf_user_id` before persisting; lookup failure → 502, unknown login → 400.

---

## Audit actor (`qua_shared/schemas/config/audit.py`)

`AuditRecord{ts, event, slug?, from_state?, to_state?, actor, payload, request_id?, reason?, result}`.
`Actor{hf_user_id, login_at_time, role}` (`use_enum_values=True`).

| Field | Truth |
|---|---|
| `hf_user_id` | canonical id; immutable |
| `login_at_time` | snapshot of cookie login at write time — **never refetched**; preserves historical login after rename |
| `role` | frozen at action time |

Built by `_admin_helpers.actor_for(user)` = `Actor(hf_user_id=user.hf_user_id, login_at_time=user.login, role=role_of(user).value)`.

Reason discipline: admin mutations require reason ≥10 chars (`normalize_reason` `None` → 400), persisted on `AuditRecord.reason`. No wildcard "manual override" — new recovery scenarios get a new named event with its own reason check.

---

## Activity rail

One feed — the anonymous-visible Recent Activity rail — a projection of the SQLite `transitions` log (`repo_transitions`), classified by `services/activity/activity_classification.py`. The admin notifications rail was retired; admin awareness now lives in the **Admin dashboard** tabs (Users / Requests / Reviews) — see [admin-dashboard.md](admin-dashboard.md).

| Bucket | Surfaced where | Identity |
|---|---|---|
| `public` | anonymous-visible Recent Activity rail | redacted; owner caller sees `actor_login`/`actor_hf_user_id` |
| `hidden` | nowhere passive (bulk/operational, role administrivia, former admin-only events, feedback-loop events) | — |

`classify(record)` returns `public` or `hidden`; unknown → `hidden` (safe default — new events stay off the rail until explicitly classified). Pre-event records with `to_state == "awaiting_alignment"` → public `requested` kind. Anti-drift test: every event in `services/state.py:_HANDLERS` must appear in `PUBLIC_EVENTS` or `HIDDEN_EVENTS`.

| Feed | Service | Route | Pagination | Window |
|---|---|---|---|---|
| public | `services/activity/public_activity.py::feed` | `GET /api/public/activity` | `cursor`/`limit` (1–500, default 50) | rolling 2 months |

Filters `result != "ok"` records. `audit_id` = STORED transition `content_hash` (never recomputed — a migration-NULLed slug would mismatch existing tombstones; `activity_classification.audit_id()` is the fallback derivation only).

### Global tombstones (`services/db/repo_activity.py`, `services/activity/activity_state.py`)

Owner-only mutation against the activity sidecars: `DELETE /api/public/activity/<audit_id>` writes a row into `activity_tombstones` (`audit_content_hash`, `deleted_by_id`, `reason`, `ts`) so the public feed filters the referenced audit record out for everyone. Reason ≥10 chars; `@require_same_origin` on top of `@require_role(OWNER)`.

`activity_state.delete(audit_id, actor, reason)` upserts the row + appends the `admin.activity_deleted` transition in one `durable_transaction`. Idempotent (`ON CONFLICT … DO UPDATE`). `activity_state.snapshot()` returns `ActivityState{deleted}` (per-user dismissals were dropped with the admin notifications rail).

### Other activity-package modules

| Module | Purpose | Route |
|---|---|---|
| `services/activity/history_query.py` | Segments-tab edit-history parse/filter/summary; `build_split_group_index`, `build_resolved_by_edit_index`; cache-aware via `services/storage/cache`. NOT audit-log activity | `GET /api/seg/edit-history/<reciter>` (`routes/segments/validation.py`) |
| `services/activity/stats.py` | Segmentation histograms/percentiles from `detailed.json`/seg verses; pure dicts | `GET /api/seg/stats/<reciter>` (validation.py) |
| `services/activity/search_normalize.py` | Arabic-aware `normalize_arabic`/`matches`; lockstep with FE `SearchableSelect.svelte` | backs `?search=` filters |
