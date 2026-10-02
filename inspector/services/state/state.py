"""Reciter state service: reads/writes from SQLite ``delivery_states`` + ``claims`` tables.

Single source of truth for reciter lifecycle + assignee. Inspector backend
is sole writer. The SQLite DB (via ``repo_state``, ``repo_claims``) is the
canonical store; writes commit atomically via ``durable_transaction``.

The full state machine ships in Phase 1 even though most endpoint callers
land in later phases — the dispatcher is the single source of truth and
later phases just add routes. Per-event payload shapes are colocated below
as ``TypedDict``s so callers know exactly what each event expects.

Spec:
- docs/planning/inspector-deploy/v2/inspector-state-management.md §4
  (canonical event vocabulary + transition matrix)
- docs/planning/inspector-deploy/v2/phases/state-machine.md
  (admin diagram + state-preserving actions)
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypedDict

from qua_shared.catalog_visibility import is_everyayah_channel
from qua_shared.schemas import (
    Actor,
    ReciterRow,
    ReciterState,
    ReciterStateFile,
    RevisionContext,
    Visibility,
)
from services import db as _db
from services.auth import permissions
from services.db import _serde, repo_access, repo_claims, repo_state, repo_transitions
from services.db import sync as _sync
from services.errors import Codes

from .labels import humanize_state

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


class StateError(Exception):
    pass


class UnknownReciter(StateError):
    pass


class UnknownEvent(StateError):
    pass


class InvalidTransition(StateError):
    """Raised when the matrix rejects a transition.

    The database is unchanged; no transition row is written. Callers should
    surface HTTP 400 with the message.

    Optional ``details`` carry structured payloads the FE can render (e.g.
    the offending category counts for a mark-ready submission). Optional
    ``code`` is a stable machine constant (``services.errors.Codes``) the FE
    maps to friendly copy; ``context`` carries display data (e.g. a humanized
    ``state_label``). The app-level error handler at ``app.py`` includes them
    in the JSON envelope.
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict | None = None,
        code: str | None = None,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.details = details or None
        self.code = code
        self.context = context or None


class NotAuthorizedForTransition(StateError):
    """Raised when the actor's tier/ownership doesn't permit a transition (→ 403).

    Optional ``code``/``context`` mirror ``InvalidTransition`` for the FE
    translation layer."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.context = context or None


# ----------------------------------------------------------------------
# Per-event payload shapes
# ----------------------------------------------------------------------


class ClaimedPayload(TypedDict):
    assignee_hf_id: str
    assignee_login: str


class ReassignedPayload(TypedDict):
    new_assignee_hf_id: str
    new_assignee_login: str
    previous_assignee_hf_id: str


class MergeRejectedPayload(TypedDict, total=False):
    pass  # ``reason`` is on the audit record, not the payload


class PublishedPayload(TypedDict, total=False):
    job_id: str  # the succeeded timestamps job (already linked at launch)


class UnlockedForRevisionPayload(TypedDict):
    pass


class UnpublishedPayload(TypedDict):
    pass


class DiscardedPayload(TypedDict, total=False):
    pass


class ContentDiscardedPayload(TypedDict, total=False):
    cleanup_id: str


# ----------------------------------------------------------------------
# Reads (assembled from the SQLite substrate via repo_state)
# ----------------------------------------------------------------------


def hydrate() -> None:
    """No-op under the SQLite substrate (the DB is the source of truth, loaded
    at boot by ``db.sync.pull`` + ``init_db``). Kept so legacy boot/test call
    sites don't churn."""
    return None


def is_hydrated() -> bool:
    """True if the DB is open + migrated (``/healthz`` reads this)."""
    return bool(_db.healthcheck().get("open"))


def snapshot() -> ReciterStateFile:
    """Reassemble the legacy whole-store view (parity for the few readers that
    expect a ``ReciterStateFile``)."""
    return ReciterStateFile(reciters=repo_state.all_rows())


def get_row(slug: str) -> ReciterRow | None:
    return repo_state.get_row(slug)


def is_delivery_visible(slug: str, viewer=None) -> bool:
    """Whether a delivery slug may be addressed by this Inspector viewer.

    EveryAyah remains in the canonical database for owner operations, but it
    is not a visible delivery for anonymous, contributor, or maintainer
    callers—even when they already know the slug.
    """
    if slug.startswith("sample--"):
        return True
    if viewer is None:
        from services import auth as _auth

        viewer = _auth.current_user()
    from services.state import catalog as _catalog

    delivery = _catalog.find_delivery(slug)
    if delivery is None or not is_everyayah_channel(delivery.channel):
        return True
    return viewer is not None and permissions.is_owner(viewer)


def all_rows() -> list[ReciterRow]:
    return repo_state.all_rows()


def has_audio_access(slug: str, viewer=None) -> bool:
    """Whether a delivery's *audio* (chapter MP3s, manifest durations, peaks)
    may be served to this viewer.

    Looser than :func:`has_content_access`: the dashboard footer plays any
    catalogued delivery (CDN stream-through for un-extracted ones), so the
    audio surfaces only need the slug to be a visible catalog delivery, not a
    reviewable one. Discarded deliveries have no catalog row, so they still
    404. Reviewable content (segments / validation / timestamps) keeps the
    stricter gate.
    """
    if slug.startswith("sample--"):
        return True
    if not is_delivery_visible(slug, viewer):
        return False
    from services.state import catalog as _catalog

    return _catalog.find_delivery(slug) is not None


def has_content_access(slug: str, viewer=None) -> bool:
    """Whether a delivery is public and currently backed by reviewable data.

    Maintainer-uploaded ``sample--`` slugs are an isolated Segments-only
    namespace and intentionally have no lifecycle row, so preserve their
    existing route behavior here.
    """
    if slug.startswith("sample--"):
        return True
    if viewer is None:
        from services import auth as _auth

        viewer = _auth.current_user()
    if not is_delivery_visible(slug, viewer):
        return False
    row = get_row(slug)
    return bool(
        row is not None
        and row.visibility == Visibility.PUBLIC
        and row.state
        in (
            ReciterState.AWAITING_REVIEW,
            ReciterState.UNDER_REVIEW,
            ReciterState.RELEASED,
        )
    )


# ----------------------------------------------------------------------
# Authorization helpers
# ----------------------------------------------------------------------


def record_timestamps_job(slug: str, job_id: str) -> list[str]:
    """Append ``job_id`` to a reciter's ``timestamps_job_ids`` WITHOUT a
    lifecycle transition — the reciter stays in its current state.

    This links a launched HF Job to the reciter (for the Reviews tab + status
    proxy); it is job bookkeeping, not a state change, so it deliberately does
    NOT route through ``transition()``. Persisted under ``durable_transaction``
    (bucket sync). Returns the updated id list.
    """
    with _sync.durable_transaction():
        row = get_row(slug)
        if row is None:
            raise UnknownReciter(slug)
        ids = list(row.timestamps_job_ids)
        if job_id and job_id not in ids:
            ids.append(job_id)
            repo_state.update_state(slug, timestamps_job_ids=ids)
        return ids


def mark_timestamps_job_finished(slug: str) -> None:
    """Stamp ``last_job_finished_at = now`` on a reciter WITHOUT a lifecycle
    transition — pure notification bookkeeping (like ``record_timestamps_job``).

    Called when a timestamps job reaches a terminal state (success *and*
    failure) so the Reviews-tab dot lights up: success → on the now-released
    row (Published bucket), failure → on the still-under_review row (Marked
    ready bucket). The unread predicate compares this against the admin's
    per-slug ``viewed_at``. Persisted under ``durable_transaction`` (bucket sync).
    """
    with _sync.durable_transaction():
        if get_row(slug) is None:
            raise UnknownReciter(slug)
        repo_state.update_state(slug, last_job_finished_at=_now())


def _require_capability(actor: Actor, capability: str) -> None:
    """Tier-capability gate — the data-driven replacement for the legacy
    ``_require_maintainer`` / ``_require_owner`` / ``_require_contributor_or_higher``
    tier checks. Raises ``NotAuthorizedForTransition`` (→ 403) when the actor's
    tier lacks ``capability`` in the resolved permission matrix. Owner always
    passes; ``manage_permissions`` stays owner-only.

    Called at the SAME point in each handler where its legacy tier check sat,
    so the 400-before-403 (state-then-auth) precedence is unchanged. Ownership
    (claim-holder) checks stay separate — they ask "is this *your* row", which
    is not a tier capability. Lazy import keeps the auth package off state.py's
    import-time graph (matches the other in-handler lazy imports)."""
    from services.auth import capabilities as _capabilities

    if not _capabilities.can(actor, capability):
        raise NotAuthorizedForTransition(
            "You don't have permission for this action.",
            code=Codes.FORBIDDEN_CAPABILITY,
        )


# Event → tier capability. Authoritative map of which capability each gated
# transition enforces (the handler hardcodes the same id inline). Events absent
# here have NO tier gate by design: server-driven
# (``reciter.alignment_completed``) or pure ownership (``reciter.released`` =
# claim-holder-or-maintainer). The parity test asserts every mapped event
# rejects an under-privileged actor. Note: ``reciter.published`` is gated by
# ``reciter.publish`` but is fired by the system (SYSTEM_ACTOR, role OWNER) on
# timestamps-job success, which passes the gate.
_EVENT_CAPABILITY: dict[str, str] = {
    "catalog.added": "catalog.add",
    "catalog.edited": "catalog.edit",
    "reciter.requested": "request.submit",
    "reciter.request_rejected_soft": "request.reject_soft",
    "reciter.request_rejected_hard": "request.reject_hard",
    "reciter.claimed": "claim.acquire",
    "reciter.marked_ready": "claim.mark_ready",
    "reciter.unmarked_ready": "claim.unmark_ready",
    "reciter.merge_rejected": "review.send_back",
    "reciter.published": "reciter.publish",
    "reciter.unpublished": "reciter.unpublish",
    "reciter.discarded": "reciter.discard",
    "reciter.content_discarded": "reciter.discard_content",
    "reciter.undiscarded": "reciter.undiscard",
    "claim.force_released": "claim.force_release",
    "claim.reassigned": "claim.reassign",
    "admin.unlocked_for_revision": "reciter.unlock_for_revision",
}


def _require_claim_holder_or_maintainer(actor: Actor, row: ReciterRow) -> None:
    if not permissions.is_claim_holder_or_maintainer(actor, row):
        raise NotAuthorizedForTransition(
            "Only the contributor who claimed this reciter (or a maintainer) can do that.",
            code=Codes.NOT_CLAIM_HOLDER,
        )


def _require_claim_holder(actor: Actor, row: ReciterRow) -> None:
    if not permissions.is_claim_holder(actor, row):
        raise NotAuthorizedForTransition(
            "Only the contributor who currently holds this reciter can do that.",
            code=Codes.NOT_CLAIM_HOLDER,
        )


def _require_reason(reason: str | None, event: str) -> str:
    """Coerce ``reason`` to a non-empty trimmed string or raise.

    Used by handlers whose audit-log entry must carry a human-readable
    justification (force-release, reassign, force-set, send-back, etc.).
    """
    norm = permissions.normalize_reason(reason)
    if norm is None:
        raise InvalidTransition(
            f"Please provide a reason (at least {permissions.MIN_REASON_CHARS} characters).",
            code=Codes.REASON_REQUIRED,
            context={"action": event, "min_chars": permissions.MIN_REASON_CHARS},
        )
    return norm


def _state_precondition(
    action: str, before: ReciterRow, message: str | None = None
) -> InvalidTransition:
    """Build a ``STATE_PRECONDITION`` ``InvalidTransition`` for a rejected
    transition whose row is in the wrong state. ``before`` must be non-None
    (all call sites guard ``before is None`` first). The FE renders friendly
    copy from ``code`` + ``context.state_label``; the prose is a plain fallback."""
    label = humanize_state(before.state)
    return InvalidTransition(
        message or f"This action isn't available while the reciter is {label}.",
        code=Codes.STATE_PRECONDITION,
        context={
            "action": action,
            "current_state": before.state.value,
            "state_label": label,
        },
    )


# ----------------------------------------------------------------------
# Dispatch — one durable transaction per event
# ----------------------------------------------------------------------


def transition(
    slug: str,
    event: str,
    *,
    actor: Actor,
    payload: dict[str, Any] | None = None,
    reason: str | None = None,
) -> ReciterRow:
    """Apply a slug-bound transition in ONE durable transaction. Returns the
    new row.

    The handler + the ``transitions`` row + the ``delivery_states``/``claims``
    writes (and any folded auto-claim) commit atomically; the commit is then
    uploaded to the bucket before this returns (``durable_transaction``) so a
    write is never acked without a durable bucket copy. No per-slug lock: the
    single serialized writer + ``BEGIN IMMEDIATE`` already serializes same-slug
    writes (and a per-slug lock taken before the write lock would deadlock the
    revoke cascade, which holds the write lock then closes per-slug claims).
    """
    payload = payload or {}
    if event not in _HANDLERS:
        raise UnknownEvent(f"unknown slug-bound event {event!r}")
    with _sync.durable_transaction() as conn:
        new_row = _apply_event(conn, slug, event, actor=actor, payload=payload, reason=reason)
    # The TS manifest (services/reference/timestamps.py) is a process-cached
    # projection of which slugs are released, with no other
    # invalidation hook — without this it stays frozen at boot-state until the
    # next restart. Drop it AFTER commit (a pre-commit drop could let a
    # concurrent manifest read re-cache the old state). Unconditional rather
    # than enumerating publish-affecting events: the rebuild is cheap + lazy
    # (next manifest request, warm sidecar caches) and transitions are
    # admin/edit-frequency, not request-frequency.
    from services.reference import timestamps as _ts_manifest

    _ts_manifest.invalidate()
    return new_row


def _apply_event(
    conn: sqlite3.Connection,
    slug: str,
    event: str,
    *,
    actor: Actor,
    payload: dict[str, Any],
    reason: str | None,
    after_persist: Callable[[str], None] | None = None,
) -> ReciterRow:
    """Apply one event on the active transaction connection (NON-locking).

    Used by ``transition()`` and — for the cascading ``reciter.released`` on
    role revoke — by ``services.auth.access.revoke``. FK ordering: the
    ``transitions`` row is written FIRST (``delivery_states``/``claims``
    reference it), then the state + claim diffs, then any folded auto-claim.
    """
    handler = _HANDLERS.get(event)
    if handler is None:
        raise UnknownEvent(f"unknown slug-bound event {event!r}")

    before = repo_state.get_row(slug)

    # Actor must exist for the transitions.actor_id FK BEFORE the handler runs —
    # some handlers (alignment_completed → apply_and_archive_completed) append
    # their own transition rows (catalog.edited) mid-handler.
    repo_access.ensure_user(actor.hf_user_id, login=actor.login_at_time)

    # Capture pending-request context BEFORE the handler resolves (clears) the
    # pending entry via apply_and_archive_completed / archive_returned. Used for
    # the folded auto-claim AND for per-user notifications (the requester isn't
    # recoverable post-handler — the row is archived and the slug may be reused).
    auto_claim_requester: Actor | None = None
    notify_extra: dict[str, Any] = {}
    if event in (
        "reciter.alignment_completed",
        "reciter.request_rejected_soft",
        "reciter.request_rejected_hard",
    ):
        from . import pending_requests as _pending_requests

        pending = _pending_requests.get(slug)
        if pending is not None:
            if event == "reciter.alignment_completed" and pending.auto_claim:
                # The folded reciter.claimed below sends the "assigned"
                # notification; don't also send "ready for review".
                auto_claim_requester = pending.requester
            else:
                notify_extra["requester"] = pending.requester

    new_row = handler(slug, before, actor, payload, reason)

    # Transition row FIRST (delivery_states/claims reference its id).
    record = repo_transitions.append(
        event=event,
        actor=actor,
        slug=slug,
        from_state=before.state.value if before is not None else None,
        to_state=new_row.state.value,
        payload=payload,
        reason=reason,
    )
    tid = record.request_id
    if tid is None:
        raise RuntimeError("transition record missing request_id")
    _persist_state(before, new_row, tid=tid)
    _persist_claim_diff(before, new_row, tid=tid, event=event, payload=payload)

    # Compound workflows can update adjacent projections while the lifecycle,
    # state, and claim changes are still inside the same durable transaction.
    if after_persist is not None:
        after_persist(tid)

    if auto_claim_requester is not None:
        _maybe_auto_claim(conn, slug, auto_claim_requester)

    # Per-user notifications (best-effort — never raises into the transition).
    from services.notifications import emit as _notify

    _notify.emit_for_event(conn, record, before=before, extra=notify_extra)

    # Email notifications — separate opt-in channel (subscriptions keyed by email).
    # Recipient resolution reads the in-transaction state; the send is fire-and-forget.
    # Best-effort inside the emit module; never raises into the transition.
    if event == "reciter.published":
        from services.email import emit as _email

        _email.notify_recitation_published(slug)
    elif event == "reciter.alignment_completed":
        requester = notify_extra.get("requester") or auto_claim_requester
        if requester is not None:
            from services.email import emit as _email

            _email.notify_request_aligned(slug, requester.hf_user_id)

    return new_row


def _persist_state(before: ReciterRow | None, new_row: ReciterRow, *, tid: str) -> None:
    """Insert (new row) or update the changed ``delivery_states`` columns."""
    if before is None:
        repo_state.upsert_state(
            new_row.slug,
            state=new_row.state,
            state_since=new_row.state_since,
            visibility=new_row.visibility,
            visibility_reason=new_row.visibility_reason,
            last_save_at=new_row.last_save_at,
            created_by_transition_id=tid,
            timestamps_job_ids=list(new_row.timestamps_job_ids),
            revision_in_progress=new_row.revision_in_progress,
        )
        return
    updates: dict[str, Any] = {}
    for col in (
        "state",
        "state_since",
        "visibility",
        "visibility_reason",
        "last_save_at",
        "timestamps_job_ids",
        "revision_in_progress",
    ):
        if getattr(new_row, col) != getattr(before, col):
            updates[col] = getattr(new_row, col)
    if updates:
        repo_state.update_state(new_row.slug, **updates)


def _persist_claim_diff(
    before: ReciterRow | None,
    new_row: ReciterRow,
    *,
    tid: str,
    event: str,
    payload: dict[str, Any],
) -> None:
    """Derive claim operations from the assignee/marked_ready delta. The open
    claim is the source of truth for ``assignee_*`` + ``marked_ready`` on the
    assembled ``ReciterRow`` (only populated on UNDER_REVIEW rows).

    For ``reciter.marked_ready`` events, ``payload`` is the validated
    ``MarkReadyRequest`` shape — the checklist + two comment boxes are
    persisted onto the open claim alongside ``marked_ready_at``."""
    before_assignee = before.assignee_hf_id if before is not None else None
    new_assignee = new_row.assignee_hf_id
    before_ready = before.marked_ready if before is not None else False

    if new_assignee and not before_assignee:
        repo_access.ensure_user(new_assignee, login=new_row.assignee_login)
        repo_claims.open_claim(
            slug=new_row.slug,
            assignee_id=new_assignee,
            assignee_login=new_row.assignee_login,
            claimed_at=new_row.assignee_since,
            opened_by_transition_id=tid,
        )
    elif before_assignee and new_assignee and new_assignee != before_assignee:
        repo_access.ensure_user(new_assignee, login=new_row.assignee_login)
        repo_claims.reassign(
            slug=new_row.slug,
            new_assignee_id=new_assignee,
            new_assignee_login=new_row.assignee_login,
            at=new_row.assignee_since,
            closed_by_transition_id=tid,
            opened_by_transition_id=tid,
        )
    elif before_assignee and not new_assignee:
        repo_claims.close_claim(slug=new_row.slug, close_reason=event, closed_by_transition_id=tid)

    # marked_ready toggle on a still-held claim (same assignee).
    if new_assignee and before_assignee == new_assignee and new_row.marked_ready != before_ready:
        if new_row.marked_ready:
            # mark-ready: payload was validated by the handler unless the
            # owner-bypass branch ran (no checklist in that case). Pull
            # whichever fields the payload carries.
            bypass = bool(payload.get("bypass_used"))
            checklist = payload.get("checklist") or {}
            # Bypass submissions don't carry a checklist; persist NULL so
            # the admin Reviews drawer can distinguish "submitted via
            # bypass" from "form submission with empty checklist".
            checklist_json = None if bypass or not checklist else _serde.json_dumps(checklist)
            repo_claims.set_marked_ready(
                new_row.slug,
                ready=True,
                checklist_json=checklist_json,
                comment_checks=str(payload.get("comment_checks") or ""),
                comment_issues=str(payload.get("comment_issues") or ""),
                bypass_used=bypass,
            )
        else:
            # unmark-ready: clears marked_ready_at + the four submission
            # columns on the open claim. A subsequent re-mark writes fresh
            # values; the prior cycle's submission lives on the closed
            # history row (set_marked_ready only touches the OPEN claim).
            repo_claims.set_marked_ready(new_row.slug, ready=False)


def _maybe_auto_claim(conn: sqlite3.Connection, slug: str, requester: Actor) -> None:
    """Fold ``reciter.claimed`` into the alignment txn for an ``auto_claim``
    request. Owners are exempt from the one-claim check; a non-owner already
    holding another claim gets an in-txn ``reciter.auto_claim_skipped`` audit."""
    repo_access.ensure_user(requester.hf_user_id, login=requester.login_at_time)
    if not permissions.is_owner(requester):
        held = repo_claims.open_claim_for_user(requester.hf_user_id)
        if held is not None and held != slug:
            logger.info(
                "auto_claim: %s already holds claim on %s; skipping %s",
                requester.hf_user_id,
                held,
                slug,
            )
            repo_transitions.append(
                event="reciter.auto_claim_skipped",
                actor=requester,
                slug=slug,
                payload={"reason": "other_active_claim", "existing_claim_slug": held},
            )
            return
    _apply_event(
        conn,
        slug,
        "reciter.claimed",
        actor=requester,
        payload={
            "assignee_hf_id": requester.hf_user_id,
            "assignee_login": requester.login_at_time,
            # Marks this claim as the auto-claim fold (not a manual self-claim)
            # so the notifications resolver sends the "assigned" notification.
            "notify_auto_claim": True,
        },
        reason=None,
    )


# ----------------------------------------------------------------------
# Per-event handlers
# ----------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


# Each handler:
# - validates (state, role, payload)
# - returns the new ReciterRow
# - never persists itself — the dispatcher does


def _h_catalog_added(slug, before, actor, payload, reason):
    """Catalog admin added a reciter; insert a state row in CATALOGUED."""
    if before is not None:
        raise InvalidTransition(f"slug {slug!r} already has a state row")
    _require_capability(actor, "catalog.add")
    return ReciterRow(
        slug=slug,
        state=ReciterState.CATALOGUED,
        state_since=_now(),
    )


def _h_catalog_edited(slug, before, actor, payload, reason):
    """No-op on the state row; audit-only. The dispatcher still records it."""
    if before is None:
        raise UnknownReciter(slug)
    _require_capability(actor, "catalog.edit")
    return before  # unchanged


def _h_alignment_completed(slug, before, actor, payload, reason):
    """Server-side or admin-triggered acceptance: the alignment pipeline has
    produced files under ``reciters/<slug>/`` and the row is ready for human
    review.

    Side effect: if a pending user request exists for this slug, applies the
    proposed catalog edits (riwayah/style/recording_*/names/country) at the
    same time so the catalog reflects whatever the requester proposed before
    the row leaves AWAITING_ALIGNMENT. See ``services.pending_requests``.
    The pending entry is cleared regardless of whether edits applied
    cleanly — catalog failures are logged but don't block the transition.

    If the pending request carried ``auto_claim=True``, ``_apply_event`` folds
    a follow-up ``reciter.claimed`` into THIS transaction on the requester's
    behalf (skipping, with an audit, if a non-owner already holds a claim).
    """
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.AWAITING_ALIGNMENT:
        raise _state_precondition("alignment_completed", before)

    # The auto_claim requester is captured by ``_apply_event`` BEFORE this
    # handler resolves the pending request; the follow-up ``reciter.claimed``
    # is folded into the same transaction post-persist (no recursion through
    # ``transition()``, no post-commit queue).
    # Imported here (not at module top) to avoid a circular import.
    from . import pending_requests as _pending_requests

    _pending_requests.apply_and_archive_completed(slug, actor=actor)

    return _replace(
        before,
        state=ReciterState.AWAITING_REVIEW,
        state_since=_now(),
    )


def _h_requested(slug, before, actor, payload, reason):
    """User-submitted request to align this reciter combination.

    Any signed-in user (contributor or above) can fire this. Accepts two
    source shapes — both map to the same "available for request" public
    bucket and both land in ``AWAITING_ALIGNMENT``:

    - No state row exists yet (``before is None``). The dispatcher inserts
      a fresh row. This is the common case: catalog deliveries don't get a
      state row until a lifecycle event fires.
    - A row exists in ``CATALOGUED``. Transition in place. This is the
      post-soft-reject re-request path: ``reciter.request_rejected_soft``
      drops the row back to CATALOGUED, and the next request transitions
      it forward again.

    Stores the proposed catalog edits + free-form comments in the
    ``pending_requests`` bucket store. Acceptance is implicit and
    server-side (see ``_h_alignment_completed``); admins can reject
    pre-acceptance via ``reciter.request_rejected_*``.
    """
    _require_capability(actor, "request.submit")

    if before is not None:
        if before.state != ReciterState.CATALOGUED:
            raise _state_precondition("requested", before)
        if before.visibility != Visibility.PUBLIC:
            raise InvalidTransition(
                "This reciter isn't available to request.",
                code=Codes.VISIBILITY_BLOCKED,
                context={"visibility": before.visibility.value},
            )

    # Defense-in-depth: also checked by the route layer.
    from . import pending_requests as _pending_requests

    if _pending_requests.get(slug) is not None:
        raise InvalidTransition(f"slug {slug!r} already has a pending request")

    from qua_shared.schemas import ProposedEdits as _ProposedEdits

    edits_raw = payload.get("proposed_edits") or {}
    try:
        edits = _ProposedEdits.model_validate(edits_raw)
    except Exception as e:  # noqa: BLE001
        raise InvalidTransition(f"invalid proposed_edits: {e}") from e
    comments = payload.get("comments")
    if comments is not None and not isinstance(comments, str):
        raise InvalidTransition("comments must be a string or null")
    if comments is not None and len(comments) > 1000:
        raise InvalidTransition("comments exceeds 1000 chars")

    auto_claim = bool(payload.get("auto_claim", False))

    # Persist the pending entry inside the same transaction as the state row;
    # the partial-unique index + the get() check above reject a duplicate, and
    # a downstream failure rolls the whole event back atomically.
    _pending_requests.submit(
        slug,
        requester=actor,
        edits=edits,
        comments=comments,
        auto_claim=auto_claim,
    )

    now = _now()
    if before is None:
        return ReciterRow(
            slug=slug,
            state=ReciterState.AWAITING_ALIGNMENT,
            state_since=now,
        )
    return _replace(
        before,
        state=ReciterState.AWAITING_ALIGNMENT,
        state_since=now,
    )


def _h_request_rejected_soft(slug, before, actor, payload, reason):
    """Admin sends a pending request back. Row returns to CATALOGUED.

    The pending entry moves to ``requests/returned.json`` (with the
    admin's reason) so the requester can recover what they originally
    asked for and resubmit a corrected version. Reason is required and
    also lands in the audit record for accountability.
    """
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.AWAITING_ALIGNMENT:
        raise _state_precondition("request_rejected_soft", before)
    _require_capability(actor, "request.reject_soft")
    norm_reason = _require_reason(reason, "request_rejected_soft")

    from . import pending_requests as _pending_requests

    _pending_requests.archive_returned(slug, reason=norm_reason, by_actor=actor)

    return _replace(
        before,
        state=ReciterState.CATALOGUED,
        state_since=_now(),
    )


def _h_request_rejected_hard(slug, before, actor, payload, reason):
    """Admin rejects a pending request and discards the combination.

    Row returns to CATALOGUED with ``visibility=DISCARDED`` so the public
    dashboard hides it. Admins can still view discarded rows in the modal's
    separate discarded section; owners can un-discard via ``reciter.undiscarded``.
    """
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.AWAITING_ALIGNMENT:
        raise _state_precondition("request_rejected_hard", before)
    _require_capability(actor, "request.reject_hard")
    norm_reason = _require_reason(reason, "request_rejected_hard")

    from . import pending_requests as _pending_requests

    _pending_requests.archive_discarded(slug, reason=norm_reason, by_actor=actor)

    return _replace(
        before,
        state=ReciterState.CATALOGUED,
        state_since=_now(),
        visibility=Visibility.DISCARDED,
        visibility_reason=norm_reason,
    )


def _h_claimed(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    _require_capability(actor, "claim.acquire")
    if before.state != ReciterState.AWAITING_REVIEW:
        raise _state_precondition("claimed", before)
    if before.visibility != Visibility.PUBLIC:
        raise InvalidTransition(
            "This reciter isn't available to claim.",
            code=Codes.VISIBILITY_BLOCKED,
            context={"visibility": before.visibility.value},
        )

    # Payload contributes assignee_login at write time (display cache).
    login = payload.get("assignee_login") or actor.login_at_time
    now = _now()
    return _replace(
        before,
        state=ReciterState.UNDER_REVIEW,
        state_since=now,
        assignee_hf_id=actor.hf_user_id,
        assignee_login=login,
        assignee_since=now,
        marked_ready=False,
    )


def _h_released(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.UNDER_REVIEW:
        raise _state_precondition("released", before)
    # A reviewer who has already marked ready must unmark first (or an
    # admin must force-release / send-back). Self-release on a marked
    # row would silently drop the submission and leave a half-finished
    # cycle behind. Maintainers are routed through ``claim.force_released``
    # for that case (different audit reason). The route still uses this
    # handler for the maintainer-self-release-of-own-claim edge case, so
    # the gate fires only when the claim holder owns the marked row.
    if before.marked_ready and permissions.is_claim_holder(actor, before):
        raise InvalidTransition(
            "You've marked this reciter ready for publish. Choose “Continue editing” "
            "to reopen it, or ask an admin to send it back.",
            code=Codes.RELEASE_BLOCKED_MARKED_READY,
        )
    _require_claim_holder_or_maintainer(actor, before)
    return _replace(
        before,
        state=ReciterState.AWAITING_REVIEW,
        state_since=_now(),
        assignee_hf_id=None,
        assignee_login=None,
        assignee_since=None,
        marked_ready=False,
    )


def _h_marked_ready(slug, before, actor, payload, reason):
    """Reviewer mark-ready handler.

    Validates the submission payload (well-formed ``MarkReadyRequest`` with
    all six checklist values True) and re-computes the six gated validation
    category counts against the on-disk segs. Either gate raises
    ``InvalidTransition`` so the reviewer sees a structured 400.

    Holders of ``claim.mark_ready_skip_gates`` (owners by default) skip
    BOTH gates entirely — no checklist parsing, no count check. The
    handler stamps ``bypass_used=True`` on the payload so
    ``_persist_claim_diff`` writes it to the claim row, giving admins an
    auditable record of which submissions came in via the bypass.

    The submission lives on the open claim row (persisted by
    ``_persist_claim_diff`` via the payload it threads through).
    """
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.UNDER_REVIEW:
        raise _state_precondition("marked_ready", before)
    _require_capability(actor, "claim.mark_ready")
    _require_claim_holder(actor, before)
    if before.marked_ready:
        raise InvalidTransition("already marked_ready")

    # Owner-bypass shortcut: holders of ``claim.mark_ready_skip_gates``
    # skip the form contract entirely. The route accepts an empty body
    # in that case; we still re-check capabilities here (the handler is
    # authoritative — the route's check is a UX shortcut to avoid a
    # 400-then-retry envelope). We stamp ``bypass_used=True`` only on
    # the bypass path so the non-bypass payload stays a clean
    # ``MarkReadyRequest`` shape (which is ``extra="forbid"``); the
    # non-bypass branch records ``bypass_used=False`` implicitly via
    # ``_persist_claim_diff``'s ``payload.get("bypass_used")`` default.
    from services.auth import capabilities as _capabilities

    bypass = _capabilities.can(actor, "claim.mark_ready_skip_gates")
    if bypass:
        payload["bypass_used"] = True

    if not bypass:
        # Lazy import: qua_shared.schemas is a sibling import (already used
        # elsewhere in this module), but the validation module pulls heavy
        # bucket loaders — keep it off the top-level state.py import graph.
        from pydantic import ValidationError

        from qua_shared.schemas import BLOCKING_COUNT_KEYS, MarkReadyRequest

        try:
            submission = MarkReadyRequest.model_validate(payload)
        except ValidationError as e:
            raise InvalidTransition(
                "Your mark-ready submission was incomplete. Please review and resubmit.",
                code=Codes.MARK_READY_PAYLOAD,
                details={"validation_errors": e.errors()},
            ) from e

        # Authoritative: re-compute live category counts against on-disk segs.
        # The FE applies the same gate as a UX layer, but the server is the
        # source of truth — a race against unsaved edits or a stale snapshot
        # in the browser can mean the FE's view diverges.
        from config import LOW_CONFIDENCE_THRESHOLDS
        from services.reference.delivery_edition import sdk_riwayah_for
        from services.validation import validate_reciter_segments

        result = validate_reciter_segments(slug)
        if result is None:
            raise InvalidTransition(
                "Can't mark ready — this reciter has no saved segments yet.",
                code=Codes.MARK_READY_NO_SEGMENTS,
            )

        # Only the boxes whose categories ever had an item are shown, so only
        # those are required.
        checked = submission.checklist.model_dump()
        unchecked = [k for k in result.get("checklist_keys") or [] if not checked.get(k)]
        if unchecked:
            raise InvalidTransition(
                "Please check every box in the list before marking ready.",
                code=Codes.MARK_READY_CHECKLIST,
                details={"unchecked": unchecked},
            )

        # ``category_counts.low_confidence`` is the length of the DETAIL list
        # populated against ``LOW_CONFIDENCE_DETAIL_THRESHOLD = 1.0`` — i.e.
        # every segment with confidence < 100%, often thousands. The FE
        # accordion badge (and the form's blocking-counts panel) instead
        # count items below the strict ``LOW_CONFIDENCE_THRESHOLD = 0.80``
        # cutoff that drives the user's "is this blocking" intuition. The
        # gate has to agree with what the reviewer just looked at, so
        # recompute the LC count from the items list at the strict
        # threshold; trusting ``category_counts.low_confidence`` directly
        # would reject every reciter that has any segment with confidence
        # in the 80–100% band — i.e. essentially all of them.
        counts = dict(result.get("category_counts") or {})
        lc_items = result.get("low_confidence") or []
        # The cutoff is per edition (config.LOW_CONFIDENCE_THRESHOLDS); the gate
        # has to use the same one the reviewer's accordion badge showed.
        cutoff = LOW_CONFIDENCE_THRESHOLDS[sdk_riwayah_for(slug)]
        counts["low_confidence"] = sum(
            1 for it in lc_items if (it.get("confidence") or 0.0) < cutoff
        )
        nonzero = {
            k: int(counts.get(k, 0)) for k in BLOCKING_COUNT_KEYS if int(counts.get(k, 0)) > 0
        }
        if nonzero:
            raise InvalidTransition(
                "Fix all blocking validation issues before marking ready.",
                code=Codes.MARK_READY_BLOCKING_COUNTS,
                details={"blocking_counts": nonzero},
            )

    return _replace(before, marked_ready=True)


def _h_unmarked_ready(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.UNDER_REVIEW:
        raise _state_precondition("unmarked_ready", before)
    _require_capability(actor, "claim.unmark_ready")
    _require_claim_holder(actor, before)
    if not before.marked_ready:
        raise InvalidTransition("not currently marked_ready")
    return _replace(before, marked_ready=False)


def _h_merge_rejected(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.UNDER_REVIEW or not before.marked_ready:
        raise _state_precondition(
            "merge_rejected",
            before,
            "This reciter must be marked ready before it can be sent back.",
        )
    _require_capability(actor, "review.send_back")
    _require_reason(reason, "merge_rejected")
    return _replace(before, marked_ready=False)


def _h_published(slug, before, actor, payload, reason):
    """Publish a marked-ready reciter straight to RELEASED.

    This is the sole publication edge: a reciter stays ``under_review``
    (marked_ready) while its timestamps job runs, and is published only when
    the job succeeds — fired by ``services.admin.timestamps_jobs``
    ``complete_timestamps_job`` with ``SYSTEM_ACTOR`` (role OWNER, so the
    ``reciter.publish`` gate passes). There is no intermediate
    ``awaiting_timestamps`` state any more: publish == "timestamps generated +
    released". The job id (already linked at launch via
    ``record_timestamps_job``) is re-appended idempotently for the audit.
    Closes the open claim and clears ``revision_in_progress``.
    """
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.UNDER_REVIEW or not before.marked_ready:
        raise _state_precondition(
            "published",
            before,
            "This reciter must be marked ready before it can be published.",
        )
    _require_capability(actor, "reciter.publish")
    job_id = payload.get("job_id")
    job_ids = list(before.timestamps_job_ids)
    if job_id and job_id not in job_ids:
        job_ids.append(job_id)
    return _replace(
        before,
        state=ReciterState.RELEASED,
        state_since=_now(),
        assignee_hf_id=None,
        assignee_login=None,
        assignee_since=None,
        marked_ready=False,
        timestamps_job_ids=job_ids,
        revision_in_progress=None,
    )


def _h_unpublished(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.RELEASED:
        raise _state_precondition("unpublished", before)
    _require_capability(actor, "reciter.unpublish")
    _require_reason(reason, "unpublished")
    return _replace(
        before,
        state=ReciterState.AWAITING_REVIEW,
        state_since=_now(),
        revision_in_progress=None,
    )


def _h_unlocked_for_revision(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.RELEASED:
        raise _state_precondition("unlocked_for_revision", before)
    _require_capability(actor, "reciter.unlock_for_revision")
    context = RevisionContext(
        unlocked_from_state="released",
        unlocked_at=_now(),
        unlocked_by_hf_id=actor.hf_user_id,
        original_assignee_hf_id=None,
    )
    return _replace(
        before,
        state=ReciterState.AWAITING_REVIEW,
        state_since=_now(),
        revision_in_progress=context,
    )


def _h_discarded(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    _require_capability(actor, "reciter.discard")
    if before.visibility == Visibility.DISCARDED:
        raise InvalidTransition("already discarded")
    reason = _require_reason(reason, "discarded")
    return _replace(before, visibility=Visibility.DISCARDED, visibility_reason=reason)


def _h_content_discarded(slug, before, actor, payload, reason):
    """Owner-only destructive discard; distinct from legacy soft discard."""
    if before is None:
        raise UnknownReciter(slug)
    _require_capability(actor, "reciter.discard_content")
    if before.visibility == Visibility.DISCARDED:
        raise InvalidTransition("already discarded")
    if before.state not in (
        ReciterState.AWAITING_REVIEW,
        ReciterState.UNDER_REVIEW,
        ReciterState.RELEASED,
    ):
        raise _state_precondition("content_discarded", before)
    norm_reason = _require_reason(reason, "content_discarded")
    return _replace(
        before,
        state=ReciterState.CATALOGUED,
        state_since=_now(),
        assignee_hf_id=None,
        assignee_login=None,
        assignee_since=None,
        marked_ready=False,
        visibility=Visibility.DISCARDED,
        visibility_reason=norm_reason,
        last_save_at=None,
        timestamps_job_ids=[],
        revision_in_progress=None,
    )


def _h_undiscarded(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    _require_capability(actor, "reciter.undiscard")
    if before.visibility != Visibility.DISCARDED:
        raise InvalidTransition("not currently discarded")
    return _replace(before, visibility=Visibility.PUBLIC, visibility_reason=None)


def _h_force_released(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.UNDER_REVIEW:
        raise _state_precondition("claim.force_released", before)
    # Owner-only: claim-mutation surfaces (force-release + reassign) are
    # an owner privilege; maintainers gate quality (send-back-to-UR) but
    # don't manage who reviews. See Reviews-tab plan §"Reassign popover".
    _require_capability(actor, "claim.force_release")
    # Reason is optional — the actor + slug + transition row already make
    # the action auditable. The route layer normalizes empty/short strings
    # to "" via validate_reason(required=False).
    return _replace(
        before,
        state=ReciterState.AWAITING_REVIEW,
        state_since=_now(),
        assignee_hf_id=None,
        assignee_login=None,
        assignee_since=None,
        marked_ready=False,
    )


def _h_reassigned(slug, before, actor, payload, reason):
    if before is None:
        raise UnknownReciter(slug)
    if before.state != ReciterState.UNDER_REVIEW:
        raise _state_precondition("claim.reassigned", before)
    # Owner-only — pairs with _h_force_released (see comment there).
    _require_capability(actor, "claim.reassign")
    new_hf = payload.get("new_assignee_hf_id")
    new_login = payload.get("new_assignee_login")
    if not new_hf or not new_login:
        raise InvalidTransition(
            "claim.reassigned requires payload {new_assignee_hf_id, new_assignee_login}"
        )
    # Reason is optional — pairs with _h_force_released (see comment there).
    return _replace(
        before,
        assignee_hf_id=new_hf,
        assignee_login=new_login,
        assignee_since=_now(),
        marked_ready=False,
    )


_HANDLERS: dict[str, Any] = {
    "catalog.added": _h_catalog_added,
    "catalog.edited": _h_catalog_edited,
    "reciter.requested": _h_requested,
    "reciter.request_rejected_soft": _h_request_rejected_soft,
    "reciter.request_rejected_hard": _h_request_rejected_hard,
    "reciter.alignment_completed": _h_alignment_completed,
    "reciter.claimed": _h_claimed,
    "reciter.released": _h_released,
    "reciter.marked_ready": _h_marked_ready,
    "reciter.unmarked_ready": _h_unmarked_ready,
    "reciter.merge_rejected": _h_merge_rejected,
    "reciter.published": _h_published,
    "reciter.unpublished": _h_unpublished,
    "reciter.discarded": _h_discarded,
    "reciter.content_discarded": _h_content_discarded,
    "reciter.undiscarded": _h_undiscarded,
    "claim.force_released": _h_force_released,
    "claim.reassigned": _h_reassigned,
    "admin.unlocked_for_revision": _h_unlocked_for_revision,
}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _replace(row: ReciterRow, **changes: Any) -> ReciterRow:
    """Return a new ``ReciterRow`` with ``changes`` applied. Validates."""
    data = row.model_dump()
    data.update(changes)
    return ReciterRow.model_validate(data)


def has_other_active_claim(hf_user_id: str, *, except_slug: str | None = None) -> bool:
    """True if ``hf_user_id`` holds an open claim (other than ``except_slug``).
    Used to enforce one-claim-per-user — an O(1) index lookup on ``claims``."""
    held = repo_claims.open_claim_for_user(hf_user_id)
    if held is None:
        return False
    return held != except_slug
