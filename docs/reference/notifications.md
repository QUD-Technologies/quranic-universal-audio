# Per-user notifications

The Dashboard **"My Notifications"** rail — events that happened *to* the
signed-in user, distinct from the global, identity-redacted "Recent activity"
feed. Per-user, dismissable, archived-on-dismiss.

Rail placement: right column, under the admin-dashboard button, above Recent
activity (`tabs/dashboard/components/NotificationsRail.svelte`, rendered inside
`ActivityRail.svelte`'s `.rail-wrap`). Hidden for anonymous users.

## Click-through (event-aware redirects)

Each notification card shows title + body inline (no expand) with a redirect
icon (`↗`) beside the dismiss/restore icon; its tooltip is the destination, so
the redirect is transparent on hover (`NotificationsRail.navTarget`):

| Event | Tooltip | Destination |
|---|---|---|
| `reciter.alignment_completed`, `reciter.claimed` | "Review in Segments" | `gotoSegments(slug)` — Segments tab, reciter loaded (mirrors the post-claim redirect) |
| `reciter.marked_ready` | "Review submission" | `gotoSegments(slug, {openMarkReadyReview})` — Segments tab + the read-only `MarkReadyReviewModal` (the reviewer's free-text notes, no checklist) |
| `flag.reply`, `flag.created`, `flag.replied` | "Open flagged segment" | `gotoSegments(slug, {openFlagged, focusFlaggedUid})` — Segments tab + Flagged accordion open + scrolled to the flagged segment |
| `ts_report.created`, `ts_report.resolved` | "View reported verse" | `gotoTimestamps(slug, payload.verse_key)` — Timestamps tab, that reciter loaded + playhead seeked to the verse (categorized report / its resolution) |
| `request.received` | (none) | informational — no click-through; a type pill (`payload.kind`) names the request kind |
| `shard.missing` | "View reciter" | dashboard detail modal — the chapter's shard is gone, so there is nothing to open in Timestamps |
| everything else | "View reciter" | dashboard detail modal (`openDetail`), when the reciter is still catalogued |

A `cardBadge(n)` helper renders a small type pill next to the title:
`request.received` → the kind (Edit existing combo / New riwāyah · style / New
reciter), `reciter.marked_ready` → "Has notes", `flag.created` → "Flag · comment",
`flag.replied` → "Flag · reply", `ts_report.created` → "Timestamps · report",
`ts_report.resolved` → "Timestamps · resolved", `shard.missing` → "Data · missing".

For `ts_report.created`, the card body shows the reporter's login (from
`payload.author_login`, or "an anonymous listener") only to callers holding
`timestamps.see_reporter_identity` (owner-default) — otherwise just the
verse + category. The FE gate lives in `NotificationsRail.cardBody`.

The flag deep-link is a cross-tab handoff: `gotoSegments` writes
`pendingSegmentsDeepLink` (`lib/utils/goto-segments.ts`); `ValidationPanel`
consumes it once the reciter's flagged segments load — opens the `__flagged__`
accordion and scrolls the `[data-flag-uid=...]` card into view. It waits for the
target uid to appear so a stale (previous-reciter) flag list never triggers it.
`flag.reply` / `flag.created` / `flag.replied` notifications carry
`payload.segment_uid` for this. The `openMarkReadyReview` variant is consumed by
`SegmentsTab` (not `ValidationPanel`) — once the reciter is switched in it mounts
`MarkReadyReviewModal` and clears the pending intent.

## Model

Notifications are **materialized** — one `notifications` row per (target user,
source event), written at emit time, NOT derived on read. The target user is
only reliably knowable at emit time: the pending request is archived and the
claim's assignee is cleared in the *same* transaction that fires the event.

SQLite table `notifications` (migration `0019_notifications.sql`):

| Column | Notes |
|---|---|
| `id` | PK |
| `hf_user_id` | target user (FK `users`) |
| `event` | source event name, or `flag.reply` |
| `slug` | delivery slug to link to; `NULL` for slugless intake |
| `title` | frozen collapsed-view summary (always names the reciter) |
| `body` | frozen detail (admin reason / reply text); nullable |
| `payload` | JSON extras (e.g. `segment_uid`); nullable |
| `source_key` | dedup + provenance: transition id, or `flag:<slug>:<uid>:<at_utc>` |
| `created_at` / `seen_at` / `dismissed_at` | ISO-8601 UTC; `dismissed_at IS NOT NULL` ⇒ archived |

Dedup: `UNIQUE(hf_user_id, source_key)` + `INSERT OR IGNORE` — a re-driven
transaction or retried save can't double-insert. Retention: `prune_for_user`
(called from `create`) keeps the newest 200 *dismissed* rows per user; active
rows are never auto-pruned.

Repo: `services/db/repo_notifications.py` (caller owns the txn). Routes:
`routes/auth/notifications.py` — `GET /api/me/notifications` (active + unread,
marks seen), `GET .../archived`, `POST .../<id>/dismiss`, `POST .../<id>/restore`
(all signed-in + owner-scoped). `/api/me` carries `notifications_unread` for the
first-load badge.

## Emission

`services/notifications/emit.py` has two entry points, one per source
write-path. Both are **best-effort** — wrapped in try/except-log so a
notification failure never rolls back the motivating transition or save.

1. **`emit_for_event(conn, record, *, before, extra)`** — called from
   `state._apply_event` (inside the live durable transaction, after
   `repo_transitions.append`) and from `intake.resolve`. A `_RESOLVERS` table
   maps event name → target user(s). Self-suppression drops a target equal to
   the actor, except where a resolver sets `keep_self`.
2. **`notify_flag_reply(...)`** — called from `services/segments/save.py` after
   a successful save (segment saves write the bucket, not SQLite, so this opens
   its **own** `durable_transaction`).

Every title names its reciter, resolved once via `catalog.display_name(slug)`
(or the proposed name in the request payload for slugless intake).

### Event → target

| Event | Target | Copy |
|---|---|---|
| `reciter.request_rejected_soft` | pending requester | "Your request for X was sent back" + reason |
| `reciter.request_rejected_hard` | pending requester | "Your request for X was discarded" + reason |
| `reciter.alignment_completed` | pending requester (non-auto-claim only) | "X is ready for review" |
| `reciter.claimed` (auto-claim fold) | requester (`keep_self`) | "You've been assigned to X" |
| `claim.force_released` | prior assignee (`before.assignee_hf_id`) | "Your review of X was released — it hadn't been active for a while" |
| `request.intake_returned` | requester (`requests.requester_id`) | "Your submission for X was sent back" + reason |
| `request.intake_discarded` | requester | "Your submission for X was discarded" + reason |
| `flag.reply` | original flagger | "New reply on a segment you flagged in X" + reply text |
| `reciter.marked_ready` | review-alert recipients (only when a comment box is non-empty) | "X marked ready — reviewer left notes" + the notes |

## Owner data-integrity alerts

One event, `shard.missing`, rides a **separate** capability:
`notifications.receive_integrity_alerts` (owner-only by default, delegatable).
It is deliberately not folded into the review alerts — a vanished shard is an
infrastructure alarm, not review load to share out, so the two toggle
independently from the Permissions tab.

Emitted by `emit.notify_owners_shard_integrity(findings)` from the daily
shard-integrity watchdog ([automation.md](automation.md) § Shard-integrity
watchdog), not from any transition. One card per (delivery, chapter, kind);
`source_key` is the finding's own `shardint:<kind>:<slug>:<chapter>`, so a gap
that stays unrepaired notifies **once**, not on every sweep. The body says
whether an interrupted-write temp still holds the payload (recoverable) or the
chapter needs a re-align; `payload` carries `chapter` / `kind` / `orphan_path`.

## Owner review alerts

Three event types fan out to **review-alert recipients** — everyone holding the
`notifications.receive_review_alerts` capability (owner-default-on, delegatable
to maintainers from the Permissions tab). `emit._review_alert_recipients()` →
`capabilities.users_with_capability(...)`. Per-target self-suppression drops the
acting user (an owner's own request / mark-ready / flag never notifies them).

| Event | Fired from | Copy |
|---|---|---|
| `request.received` | `notify_owners_new_request` — called from the slug-based edit-request route AND `intake.submit` (NOT a `reciter.requested` resolver, which re-fires on ingest) | "New request · X" + a body detail; `payload.kind` = `existing_combo_edit` / `existing_reciter_new_combo` / `new_reciter` |
| `reciter.marked_ready` | `_r_marked_ready` resolver (only when `comment_checks` or `comment_issues` is non-empty) | "X marked ready — reviewer left notes" + the notes; `payload.openMarkReadyReview` |
| `flag.created` / `flag.replied` | `notify_owners_flag_activity` — from the segment-save flow for `set` / `followup` flag ops | "New flag on X" / "New reply on a flag · X" + `surah:ayah — comment`; `payload.segment_uid` |
| `ts_report.created` | `notify_owners_ts_report` — from the public Timestamps-tab report route, **once per new report** (re-submits of the same category+target don't re-notify) | "Timestamps issue reported · X" + `verse_key · category`; `payload.verse_key` + `category` + `report_id` + `author_id` / `author_login` (null for anonymous). `source_key=tsreport:<slug>:<report_id>` |
| `ts_report.resolved` | `notify_reporter_ts_report_resolved` — from the resolve route, to the (signed-in) reporter only; anonymous reporters are skipped | "Your timestamps report for X was resolved" + a generic thank-you + the owner's optional comment; `payload.verse_key` + `report_id`. `source_key=tsreportresolved:<slug>:<report_id>` |

**Auto-archive.** `request.received` cards are informational. When the reciter
reaches `awaiting_review` (the `reciter.alignment_completed` event), the request
has been handled, so `emit._archive_request_alerts(slug)` dismisses every
owner's card for it — `repo_requests.ids_for_slug(slug)` →
`repo_notifications.dismiss_by_source_key("request:<id>")` (one source_key shared
across all recipients, so one call clears the whole fan-out). The intake row's
slug is back-filled at ingest, so both request paths are reachable by slug.

The old per-admin **Requests-tab unviewed badge** (entry-button dot, tab count,
per-row dot, `/api/admin/requests/unviewed-count`, the `request_views` writer)
was retired in favour of these alerts — "new request" awareness lives only on
the rail now.

The requester for the reject/alignment events is captured in `_apply_event`
**before** the handler runs (the pending row is archived mid-handler). The
auto-claim fold is marked with `payload.notify_auto_claim` so the
`reciter.claimed` resolver distinguishes it from a manual self-claim (which
notifies no one).

Deliberately **excluded**: `claim.reassigned` (event unused), `reciter.published`
(already on the public activity rail), `reciter.merge_rejected`, and all
admin/catalog/self events.

## Announcements (global broadcast)

The same rail also shows **announcements** — an owner-composed broadcast that
reaches **everyone, including signed-out visitors**. Unlike per-user
notifications (one materialized row per target), an announcement is a **single
global row**; there is no per-user fan-out, so it also reaches anonymous users
who have no `hf_user_id`.

**Model.** SQLite table `announcements` (migration `0021_announcements.sql`):
`id`, `title`, `body` (nullable), `author_hf_user_id` + `author_login`
(provenance, no FK), `created_at`, `revoked_at` (NULL ⇒ active). An announcement
stays active until an owner revokes it. Repo `services/db/repo_announcements.py`
(`create` / `list_active` / `list_all` / `revoke`); service
`services/announcements.py` (Flask-free; writes open their own
`durable_transaction`, validates title non-empty).

**Routes.**
- Public read: `GET /api/announcements` (`routes/announcements.py`) — **ungated**,
  anonymous-reachable (mirrors `/api/static/*`); serves the active rows as the
  public `Announcement` wire shape (`id`/`title`/`body`/`created_at`).
- Owner compose/manage (`routes/admin/announcements.py`, all gate
  `announcements.send`): `GET /api/admin/announcements` (active + revoked, the
  `AnnouncementAdmin` shape), `POST /api/admin/announcements` (compose, validates
  via `AnnouncementCreate`), `POST /api/admin/announcements/<id>/revoke`. The two
  POSTs also carry `@require_same_origin`.

`announcements.send` is a `G_ADMIN` capability, **owner-only by default but
toggleable** (an owner can delegate to maintainers from the Permissions tab).

**Dismiss / seen — client-side only.** There is **no** server-side per-user
dismiss state. The browser tracks it in localStorage
(`insp_dismissed_announcements`, `insp_seen_announcements`); dismissing hides an
announcement permanently on that browser. The store
(`tabs/dashboard/stores/announcements.svelte.ts`) polls the public route on the
same 30s visibility-aware cadence and computes `unread` against a page-load
snapshot of the seen-set, so a freshly-arrived announcement reads as "new" for
the session without the always-visible rail clearing it instantly.

**Rail rendering.** Announcements render as **normal notification cards** (not a
separate group): in the Active view they merge with personal notifications,
newest-first, styled identically — each with a dismiss `✕` (no nav-target, no
archive/restore). The whole rail (`NotificationsRail.svelte`, mounted
unconditionally inside the public `ActivityRail`) shows for anonymous users
whenever ≥1 announcement is active; the Active/Archive toggle + personal list
stay signed-in-only. Compose UI: the Admin → **Announcements** tab
(`AnnouncementsCompartment.svelte`).

## Email notifications

A second, opt-in channel alongside the in-app rail: no-reply emails for catalog
+ workflow events. The rail header carries an **"Email notifs" button**
(`NotificationsRail.svelte`, gated on the `notify.email_subscriptions`
capability — shown to everyone incl. anonymous) that opens
`EmailPrefsModal.svelte` (`tabs/dashboard/components/`).

**Identity = the email address, not the HF account.** Subscriptions are keyed by
the typed email so anonymous visitors can subscribe. The row carries an optional
`hf_user_id` (set when the saver is signed in — used only to match the
`request_aligned` event to its requester). Per the product decision there is **no
verification** (saving activates immediately) and **no HF auto-seed** (the field
is user-typed; the HF cookie carries no email).

**Manage token.** On first save the server mints one stable `manage_token` per
email (`secrets.token_urlsafe`). It is the only secret guarding the row and does
double duty: the one-click unsubscribe link (`GET /api/email-unsubscribe?token=`,
turns every event off) and the email "manage" deep-link
(`<app>/?manage=<token>` → `NotificationsRail` opens the modal seeded by token).
The FE caches it in `localStorage`; the modal re-fetches by token on open so it
stays consistent with an out-of-band unsubscribe (synced on open, not pushed).

**Model** (`EmailPreferences` in `qua_shared/schemas/wire/email_preferences.py`,
codegen'd → FE `EmailPrefs`). One destination `email`, six event settings, and
two *shared* selections reused across events (pick once, applies to both):

| Field | Type | Event |
|---|---|---|
| `request_aligned` | bool | A request you submitted finishes alignment |
| `recitation_published` | `off`/`all`/`selected` | A recitation is published |
| `timestamps_regenerated` | `off`/`all`/`selected` | A reciter's timestamps are regenerated |
| `github_release` | bool | A new GitHub release is cut |
| `riwayah_new_recitation` | bool | New recitation in a followed riwayah |
| `riwayah_first_available` | bool | A followed riwayah becomes available (one-time) |
| `owner_new_request` | bool | (owner-gated, see below) A new request/submission arrives |
| `reciters` | `reciter_id[]` | shared target for every `selected`-scope event |
| `riwayahs` | slug[] | shared follow-list for both riwayah events |

**`owner_new_request` is capability-gated, not open self-service.** Unlike the
other six fields, its row in `EmailPrefsModal` only renders for a holder of the
`notify.owner_request_emails` capability (owner-default, delegatable to
maintainers — mirrors `notifications.receive_review_alerts` but for the email
channel). The route also re-checks server-side on save (silently drops the flag
to `False` for a non-holder rather than 403ing the whole save), and the emitter
(`emit_owners_new_request`) re-checks *again* at send time against the live
capability set, so a since-revoked delegate stops receiving it without anyone
touching their subscription row. Fires immediately (not digested) from
`services/notifications/emit.py::notify_owners_new_request` — the same call site
that fans the in-app `request.received` rail card out to review-alert
recipients — for both request-creation paths (slug-based edit request, slugless
intake submit). Self-suppressed for the requester. Template:
`services/email/templates/owner_new_request.html`.

An enabled event whose backing selection is empty is a no-op; the modal warns
inline rather than blocking save. The reciter/riwayah option sets are derived
client-side from the loaded catalog. Reusable primitives:
`lib/components/Segmented.svelte` + `lib/components/ChipMultiSelect.svelte`;
envelope glyph at `lib/icons/mail.svg`.

**Backend.**
- **Store:** `email_subscriptions` (migration `0022`) + `repo_email_subscriptions`
  — keyed by normalized email, prefs as a JSON blob, stable `manage_token`.
- **Routes:** `routes/auth/email_preferences.py` — `GET/POST /api/me/email-preferences`
  (capability-gated, **not** 401 for anonymous; GET resolves by HF cookie → `?token=`
  → defaults; POST `@require_same_origin`, mints+echoes the token) and the public
  `GET /api/email-unsubscribe`.
- **Sender:** `services/email/` (Flask-free; named `services.email` to avoid
  shadowing the stdlib `email`) — Jinja `templates/` (one per event extending
  `base.html`, greeting "Assalamu Alaikum"), `send.py` (Brevo transactional REST
  API over HTTPS — `POST /v3/smtp/email`, auth `BREVO_API_KEY`, From the
  Brevo-verified `EMAIL_FROM_ADDRESS`; fire-and-forget `ThreadPoolExecutor`. **HF
  Spaces block outbound SMTP on every port**, so a direct `smtplib` send silently
  never delivers — hence HTTPS. When `BREVO_API_KEY` is absent it **logs the
  rendered email** instead of sending so dev exercises the flow), and `emit.py`
  (per-event recipient resolution).
- **Event hooks** (best-effort, never break the write): `reciter.published` +
  `reciter.alignment_completed` in `state._apply_event`; `reciter.ts_regenerated`
  in `timestamps_jobs._regenerate_timestamps_on_released`; the GH cut in
  `cut_release.complete`. Each sits past the existing idempotency guard so a
  webhook+poll double-fire can't double-send. A publish collapses to **one email
  per address** (precedence `riwayah_first_available > riwayah_new_recitation >
  recitation_published`); first-in-riwayah is computed from the released set in
  the catalog. Per-recipient unsubscribe links mean one message per address (no
  BCC); fan-out is best-effort with no retry — acceptable at current scale.
- **Links:** release emails point at the GH releases page
  (`config.EMAIL_GH_RELEASES_URL`); all events link the Space
  (`config.EMAIL_SITE_URL`). Functional links use `config.EMAIL_APP_BASE_URL`
  (`INSPECTOR_PUBLIC_BASE_URL`, localhost in dev).
- **Digest (burst control).** The two high-volume events — the
  `recitation_published` scope and `timestamps_regenerated` — do **not** send
  immediately. `emit.py` buffers one `email_digest` row per matched recipient
  (migration `0023` + `repo_email_digest`; recipient resolution unchanged, so
  `all`/`selected` both work) and `services/email/digest.py` sweeps that buffer
  (`start_flush_daemon`, ~60 s, opt-out `INSPECTOR_EMAIL_DIGEST_FLUSH=0`). Each
  `(email, event_kind)` group flushes as **one** email once its tumbling window
  (opened by the earliest buffered row) ages past `EMAIL_DIGEST_WINDOW_MINUTES`
  (60). **Per-event, never cross-event** → at most two batched emails per
  recipient per window. One buffered reciter reuses the singular template; two or
  more use `<event_kind>_digest.html`. Buffering rides `durable_transaction`
  (nesting-safe): inside the publish transition it adds no extra bucket upload;
  the TS path is its own top-level write. The **riwayah-follow** flavors stay
  immediate (lower frequency). Flush is send-then-delete (a rare mid-flush crash
  re-sends rather than drops — consistent with best-effort).

## Tests

- `tests/db/test_repo_announcements.py` — announcement create / list_active /
  revoke (active⇄all, no-op double-revoke).
- `tests/routes/test_route_announcements.py` — public read anon-reachable,
  compose gated owner-only (maintainer/contributor 403, anon 401), revoke drops
  from the public list, empty title 400.
- `tests/notifications/test_emit_resolvers.py` — resolver target correctness,
  self-suppression, SYSTEM-actor alignment, auto-claim keep-self, dedup,
  flag-reply self-suppression.
- `tests/db/test_repo_notifications.py` — retention prune, dedup.
- `tests/routes/test_route_notifications.py` — auth, list/mark-seen,
  dismiss/restore, owner-scoping.
- `tests/services/test_state_request_events.py::test_reject_soft_notifies_requester`
  — end-to-end through `transition()`.
- `tests/db/test_repo_email_subscriptions.py` — email-keyed upsert, token +
  created_at preserved on update, unsubscribe turns every event off.
- `tests/routes/test_route_email_preferences.py` — anonymous reachable (no 401),
  token minted/echoed, GET-by-cookie vs GET-by-token, bad email 400, unsubscribe.
- `tests/services/test_email_emit.py` — per-event recipient resolution, scope
  filtering, single-email-per-publish precedence (captured at the `send` seam);
  the two scope events buffer (no immediate send) while riwayah stays immediate.
- `tests/services/test_email_digest.py` — flush windowing, single→singular vs
  many→digest template, reciter dedup, buffer deletion, per-event isolation.
- `tests/db/test_repo_email_digest.py` — window-edge `due_groups`, oldest-first
  `items_for`, id-scoped `delete_ids` keeps post-read rows.
