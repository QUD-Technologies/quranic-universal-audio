"""Visibility classifier for audit events — single source of truth.

Maps every state-machine event registered in ``services/state.py:_HANDLERS``
to one of two buckets:

- ``public`` — surfaced on the anonymous-visible Recent Activity rail.
- ``hidden`` — off the rail. Bulk/operational events, role administrivia,
  feedback-loop events (tombstones), and admin/maintainer-scoped events
  whose surface is now the **Admin dashboard** (Users / Requests / Reviews
  tabs) rather than a passive notifications feed.

The admin-only notifications rail was retired once the Admin dashboard
became self-contained (Reviews tab → marked-ready dot, per-row state, full
transition timeline; Users tab → per-user activity timeline; Requests tab →
intake / edit-request lifecycle). All former ``admin_only`` events live in
``HIDDEN_EVENTS`` now — still audited via ``transitions``, just not
surfaced as a notification card anywhere.

``services/public_activity`` reads this module so the feed can never drift
out of sync with the classifier. Adding a new event to ``state.py`` without
classifying it here trips
``test_anti_drift_every_state_handler_is_classified`` in the unit suite.
"""

from __future__ import annotations

import hashlib
from typing import Literal

Bucket = Literal["public", "hidden"]


# Public bucket: event -> public-card "kind" (drives the marker dot + template).
PUBLIC_EVENTS: dict[str, str] = {
    "catalog.added": "added",
    # `reciter.requested` shows on the public rail as plain "X was requested"
    # prose. Request *review* lives in the Admin dashboard → Requests tab.
    "reciter.requested": "requested",
    "reciter.alignment_completed": "available_for_review",
    "reciter.claimed": "under_review",
    # `reciter.published` is the public publish milestone — fired by the system
    # on timestamps-job completion (the `under_review → released` transition),
    # meaning the reciter is now in-app published. Dataset/GH publishes
    # (`released`) are operator infrastructure and live in HIDDEN_EVENTS.
    "reciter.published": "published",
}


# Hidden bucket: explicitly classified as "don't surface on the public rail".
# Listed verbatim so the anti-drift test can confirm intentional exclusion
# (vs. "developer added a new event and forgot to classify it").
#
# Includes every event that used to fire an admin notification card. Those
# now live in the Admin dashboard surfaces (Reviews / Users / Requests) and
# the audit trail; no passive feed surfaces them anymore.
HIDDEN_EVENTS: frozenset[str] = frozenset(
    {
        # Former ADMIN_ONLY_EVENTS — admin dashboard owns these now.
        "reciter.released",
        "reciter.marked_ready",
        "reciter.unmarked_ready",
        "reciter.merge_rejected",
        # Dataset/GH publishes — fired for HF dataset pushes (per-recitation)
        # and GH release cuts (global), discriminated by the payload's ``track``.
        # Operator infrastructure surfaced via the Releases tab, not the public
        # rail; the in-app publish milestone is `reciter.published` (PUBLIC).
        "released",
        # Audit-only: a TS regen on an already-released reciter (no transition).
        # Operator-facing via the Releases tab's stale-stamp, not the public rail.
        "reciter.ts_regenerated",
        # Audit-only: an out-of-band TS shard refresh (manual backfill / re-stamp /
        # local regen uploaded direct to the bucket). Same operator surface as a
        # regen — advances the ts watermark + stamps HF/GH stale.
        "reciter.ts_refreshed",
        "reciter.unpublished",
        "reciter.discarded",
        "reciter.content_discarded",
        "reciter.undiscarded",
        "claim.force_released",
        "claim.reassigned",
        "admin.unlocked_for_revision",
        # Catalog metadata edits.
        "catalog.edited",
        # Request review moved off the notifications rail into the Admin dashboard
        # → Requests tab. The submit event stays public prose (PUBLIC_EVENTS);
        # the reject outcomes are hidden (still audited).
        "reciter.request_rejected_soft",
        "reciter.request_rejected_hard",
        # Non-blocking warning emitted by ``services.pending_requests.apply_and_archive_completed``
        # when a requester's proposed (riwayah, style) matches another delivery of
        # the same reciter. Visible in the audit log for debugging only.
        "catalog.conflict_warning",
        # Recorded by the auto-claim hook when an alignment_completed transition
        # would otherwise have claimed for the requester, but they already hold
        # another UNDER_REVIEW slug (or were demoted). Audit trail only.
        "reciter.auto_claim_skipped",
        "access.role_granted",
        "access.role_revoked",
        "access.role_updated",
        "access.permission_changed",
        "specials.voice_confirmed",
        # Owner-only public-feed tombstone (the only remaining mutation against
        # the activity sidecars).
        "admin.activity_deleted",
    }
)


# Special case: pre-event state-transition records carrying to_state ==
# "awaiting_alignment" surface as the "requested" public kind. Matches the
# legacy rule in services/public_activity.py.
_TO_STATE_REQUESTED = "awaiting_alignment"
_REQUESTED_KIND = "requested"


def classify(record: dict) -> Bucket:
    """Return the visibility bucket for an audit record.

    Unknown events default to ``hidden`` — the safe choice that keeps new
    events off the rail until they're explicitly classified.
    """
    event = record.get("event")
    if isinstance(event, str):
        if event in PUBLIC_EVENTS:
            return "public"
        if event in HIDDEN_EVENTS:
            return "hidden"
    if record.get("to_state") == _TO_STATE_REQUESTED:
        return "public"
    return "hidden"


def public_kind_for(record: dict) -> str | None:
    """Return the public-card kind for ``record`` or None if not public."""
    event = record.get("event")
    if isinstance(event, str) and event in PUBLIC_EVENTS:
        return PUBLIC_EVENTS[event]
    if record.get("to_state") == _TO_STATE_REQUESTED:
        return _REQUESTED_KIND
    return None


def is_classified(event: str) -> bool:
    """True iff ``event`` has an explicit bucket. Used by the anti-drift test."""
    return event in PUBLIC_EVENTS or event in HIDDEN_EVENTS


def audit_id(record: dict) -> str:
    """Stable content-derived id for one audit record.

    Used as the key for global tombstones — the store points at audit
    records without mutating the append-only log.

    Derived from the fields that, in practice, uniquely identify a record:
    timestamp + event + slug + actor hf_user_id + result. The hash is
    truncated to 16 hex chars (64 bits) — more than enough for our scale
    (a few thousand records over the audit lifetime).
    """
    ts = record.get("ts") or ""
    event = record.get("event") or ""
    slug = record.get("slug") or ""
    actor = record.get("actor") or {}
    actor_hf = ""
    if isinstance(actor, dict):
        actor_hf = actor.get("hf_user_id") or ""
    result = record.get("result") or "ok"
    raw = f"{ts}|{event}|{slug}|{actor_hf}|{result}".encode()
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:16]
