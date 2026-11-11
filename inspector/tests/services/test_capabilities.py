"""Capability resolver tests — baseline parity, overrides, superuser, the
owner-only-fixed recovery anchor, and anonymous eligibility.

The autouse ``_substrate_db`` fixture gives every test a fresh migrated DB, so
``resolve_grants`` (which reads ``db_seq`` + ``permission_overrides``) works.
"""

from __future__ import annotations

import types

import pytest

from qua_shared.schemas import CAPABILITIES, CAPABILITIES_BY_ID
from services.auth import capabilities as caps
from services.db import connection, repo_access, repo_permissions


def _actor(role: str):
    return types.SimpleNamespace(role=role, hf_user_id=f"u-{role}", login_at_time=role)


def _caller(tier: str):
    """The ``can()`` caller for a tier — anonymous is ``None`` (no identity),
    every other tier is an actor carrying that role."""
    return None if tier == "anonymous" else _actor(tier)


def _set_override(cap: str, tier: str, allowed: bool, by: str = "dev-owner") -> None:
    with connection.transaction():
        repo_access.ensure_user(by, login=by)
        repo_permissions.set_override(
            capability_id=cap,
            tier=tier,
            allowed=allowed,
            set_by=by,
        )


def _clear_override(cap: str, tier: str) -> None:
    with connection.transaction():
        repo_permissions.clear_override(capability_id=cap, tier=tier)


# ---- Baseline parity (empty override table == legacy behavior) ----

# (capability, tier, expected) tuples that encode the pre-capability hardcoded
# truth table. If any of these flips, the migration silently changed authz.
_PARITY = [
    ("view.catalog", "anonymous", True),
    ("view.catalog", "contributor", True),
    ("view.public_activity", "anonymous", True),
    ("reciter.publish", "contributor", False),
    ("reciter.publish", "maintainer", True),
    ("catalog.add", "maintainer", True),
    ("request.submit", "contributor", True),
    ("request.review", "contributor", False),
    ("request.review", "maintainer", True),
    ("request.reject_soft", "maintainer", False),
    ("claim.acquire", "contributor", True),
    ("claim.force_release", "maintainer", False),
    ("claim.reassign", "maintainer", False),
    ("reciter.undiscard", "maintainer", False),
    ("reciter.discard_content", "maintainer", False),
    ("roles.assign_maintainer", "maintainer", True),
    ("roles.assign_owner", "maintainer", False),
    ("activity.delete", "maintainer", False),
    ("reviews.view", "maintainer", True),
    ("identity.see_actor", "maintainer", False),
    ("segment.edit_as_admin", "maintainer", True),
    ("segment.edit", "contributor", True),
]


@pytest.mark.parametrize("cap,tier,expected", _PARITY)
def test_baseline_parity(cap, tier, expected):
    assert caps.can(_caller(tier), cap) is expected


def test_owner_is_superuser_for_every_capability():
    owner = _actor("owner")
    for cap in CAPABILITIES:
        assert caps.can(owner, cap.id) is True, cap.id


def test_override_flips_then_resets():
    m = _actor("maintainer")
    assert caps.can(m, "claim.force_release") is False
    _set_override("claim.force_release", "maintainer", True)
    assert caps.can(m, "claim.force_release") is True
    _clear_override("claim.force_release", "maintainer")
    assert caps.can(m, "claim.force_release") is False


def test_manage_permissions_is_owner_only_and_immutable():
    assert caps.can(_actor("owner"), "manage_permissions") is True
    assert caps.can(_actor("maintainer"), "manage_permissions") is False
    # Forcing an override row must NOT grant it (the resolver ignores it).
    _set_override("manage_permissions", "maintainer", True)
    assert caps.can(_actor("maintainer"), "manage_permissions") is False


def test_anonymous_only_where_eligible():
    # Action cap → anonymous always denied, even with a forced row.
    assert caps.can(None, "claim.acquire") is False
    _set_override("claim.acquire", "anonymous", True)
    assert caps.can(None, "claim.acquire") is False
    # View cap → anonymous honors the toggle.
    assert caps.can(None, "view.catalog") is True
    _set_override("view.catalog", "anonymous", False)
    assert caps.can(None, "view.catalog") is False


def test_unknown_capability_raises():
    with pytest.raises(ValueError):
        caps.can(_actor("owner"), "nope.nope")


def test_capabilities_for_owner_is_everything():
    ids = set(caps.capabilities_for(_actor("owner")))
    assert ids == {c.id for c in CAPABILITIES}


def test_capabilities_for_anonymous_is_anon_defaults():
    assert set(caps.capabilities_for(None)) == {
        "view.catalog",
        "view.public_activity",
        "timestamps.report",
        "notify.email_subscriptions",
    }


def test_manage_permissions_capability_exists():
    """The Permissions tab gate is the boot-time backstop for owner-only
    access to the matrix editor; the rest of the surface uses the gate's id
    string by reference, so silently dropping it would un-gate the editor."""
    assert "manage_permissions" in CAPABILITIES_BY_ID


def test_event_capability_map_is_complete_and_valid():
    """Anti-drift: every state event that had a tier gate is mapped to a real
    capability, and the mapped set hasn't silently grown/shrunk (a missing
    entry would un-gate a transition)."""
    from services.state import state as state_service

    mapping = state_service._EVENT_CAPABILITY
    for event, cap in mapping.items():
        assert event in state_service._HANDLERS, event
        assert cap in CAPABILITIES_BY_ID, cap
    assert set(mapping) == {
        "catalog.added",
        "catalog.edited",
        "reciter.requested",
        "reciter.request_rejected_soft",
        "reciter.request_rejected_hard",
        "reciter.claimed",
        "reciter.marked_ready",
        "reciter.unmarked_ready",
        "reciter.merge_rejected",
        "reciter.published",
        "reciter.unpublished",
        "reciter.discarded",
        "reciter.content_discarded",
        "reciter.undiscarded",
        "claim.force_released",
        "claim.reassigned",
        "admin.unlocked_for_revision",
        "reciter.quality_set",
    }
