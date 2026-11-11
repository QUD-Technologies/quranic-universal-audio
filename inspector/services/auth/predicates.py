"""Server-side predicates for the ``/api/reciter-task/<slug>`` response.

Mirror of the spec in inspector-state-management.md §8 plus
``can_edit_as_admin`` (maintainer/owner override on UNDER_REVIEW rows) and
``can_edit_as_owner`` (owner editing any public row regardless of state or
marked_ready freeze — owner override is total).

Pure functions: each takes the row + optional ``User`` and returns ``bool``.
Anonymous (``user is None``) always yields ``False`` for any contribution
predicate. The frontend trusts these predicates to drive visibility but the
backend is authoritative — every mutating route re-checks state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qua_shared.schemas import ReciterState, Visibility

from . import permissions

if TYPE_CHECKING:  # pragma: no cover
    pass


def _can(user, capability: str) -> bool:
    """Resolve one capability for ``user`` (``None`` → anonymous). Lazy import
    keeps the resolver (which pulls services.db + cache) off this module's
    import-time graph — predicates is imported during ``services.auth`` init."""
    from . import capabilities as _capabilities

    return _capabilities.can(user, capability)


def can_claim(row, user, *, has_other_active_claim: bool = False) -> bool:
    """``awaiting_review`` + ``public`` + ``claim.acquire`` + (owner or no other active claim)."""
    if user is None or row is None:
        return False
    if row.state != ReciterState.AWAITING_REVIEW:
        return False
    if row.visibility != Visibility.PUBLIC:
        return False
    if not _can(user, "claim.acquire"):
        return False
    if permissions.is_owner(user):
        return True  # owners exempt from one-claim-per-user rule
    return not has_other_active_claim


def can_edit(row, user) -> bool:
    """Active reviewer of the ``under_review`` row, not yet ``marked_ready``,
    with the ``segment.edit`` capability for their tier."""
    if user is None or row is None:
        return False
    return (
        row.state == ReciterState.UNDER_REVIEW
        and not row.marked_ready
        and row.visibility == Visibility.PUBLIC
        and permissions.is_claim_holder(user, row)
        and _can(user, "segment.edit")
    )


def can_edit_as_admin(row, user) -> bool:
    """Tier holding ``segment.edit_as_admin`` editing any active-claim row."""
    if user is None or row is None or not _can(user, "segment.edit_as_admin"):
        return False
    return (
        row.state == ReciterState.UNDER_REVIEW
        and not row.marked_ready
        and row.visibility == Visibility.PUBLIC
    )


def can_edit_as_owner(row, user) -> bool:
    """Owner editing any public reciter, regardless of state or marked_ready."""
    if user is None or row is None or not permissions.is_owner(user):
        return False
    return row.visibility == Visibility.PUBLIC


def can_mark_ready(row, user) -> bool:
    """Same gate as can_edit + ``claim.mark_ready`` + not already marked."""
    if not can_edit(row, user):
        return False
    if not _can(user, "claim.mark_ready"):
        return False
    return not row.marked_ready


def can_unmark_ready(row, user) -> bool:
    """Reviewer can flip ready=False back to make edits (``claim.unmark_ready``)."""
    if user is None or row is None:
        return False
    return (
        row.state == ReciterState.UNDER_REVIEW
        and row.marked_ready
        and permissions.is_claim_holder(user, row)
        and _can(user, "claim.unmark_ready")
    )


def can_skip_mark_ready_gates(row, user) -> bool:
    """The reviewer holds ``claim.mark_ready_skip_gates`` AND would otherwise
    be allowed to mark this row ready. Owners hold the capability by default;
    the owner can grant it to other tiers via the Permissions tab.

    Gated on ``can_mark_ready`` so the FE never surfaces the bypass shortcut
    on a row the user can't even attempt to mark ready (wrong state, not the
    claim holder, already marked, etc.). The segments footer reads this to
    decide whether the Mark Ready button opens the modal or POSTs directly.
    """
    if not can_mark_ready(row, user):
        return False
    return _can(user, "claim.mark_ready_skip_gates")


def can_release(row, user) -> bool:
    """Reviewer can release their own claim BEFORE they mark it ready.

    Once marked_ready is True the row is in a terminal-for-reviewer state:
    only ``can_unmark_ready`` (back out) or an admin force-release moves it.
    Surfacing Unclaim here would let a reviewer skip the unmark step and
    leave a stale ``marked_ready_at`` on a closed claim history row.
    """
    if user is None or row is None:
        return False
    return (
        row.state == ReciterState.UNDER_REVIEW
        and not row.marked_ready
        and permissions.is_claim_holder(user, row)
    )


def quality_settable(row) -> bool:
    """A quality status can be set or changed only once the recitation is
    finished: published, or marked ready for publishing. Clearing it is
    allowed in any state."""
    return row.state == ReciterState.RELEASED or (
        row.state == ReciterState.UNDER_REVIEW and row.marked_ready
    )


def can_set_quality_status(row, user) -> bool:
    """Caller may set or change the quality status now (capability + the row
    is published or marked ready)."""
    if user is None or row is None:
        return False
    return quality_settable(row) and _can(user, "reciter.set_quality_status")


def can_clear_quality_status(row, user) -> bool:
    """Caller may clear an existing quality status (any state)."""
    if user is None or row is None or row.quality_status is None:
        return False
    return _can(user, "reciter.set_quality_status")


def build_predicates(row, user, *, has_other_active_claim: bool) -> dict:
    """Return a JSON-serialisable map of every predicate for the response.

    Centralizes the predicate set so route handlers don't drift.
    """
    return {
        "can_claim": can_claim(row, user, has_other_active_claim=has_other_active_claim),
        "can_edit": can_edit(row, user),
        "can_edit_as_admin": can_edit_as_admin(row, user),
        "can_edit_as_owner": can_edit_as_owner(row, user),
        "can_mark_ready": can_mark_ready(row, user),
        "can_skip_mark_ready_gates": can_skip_mark_ready_gates(row, user),
        "can_unmark_ready": can_unmark_ready(row, user),
        "can_release": can_release(row, user),
        "can_set_quality_status": can_set_quality_status(row, user),
        "can_clear_quality_status": can_clear_quality_status(row, user),
    }
