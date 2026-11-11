"""Claim / release / mark / unmark + reciter-task routes.

These endpoints mutate the bucket state file directly via
``state.transition``; no ``require_edit_lock`` here — state.py handlers
own the ownership + precondition checks and raise:

- ``InvalidTransition`` → 400 (mapped at app errorhandler).
- ``NotAuthorizedForTransition`` → 403 (mapped at app errorhandler).
- ``UnknownReciter`` → 404 (mapped at app errorhandler).

The route's job is: authenticate, do the one-claim-per-user pre-check
(application policy, not part of the state machine), build the ``Actor``,
dispatch the transition, and return the authoritative row.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from qua_shared.schemas import MarkReadyRequest, QualityStatusRequest
from routes._admin_helpers import (
    actor_for as _actor_for,
)
from routes._admin_helpers import (
    require_signed_in_or_401 as _require_user_or_401,
)
from routes._admin_helpers import (
    row_to_dict as _row_to_dict,
)
from services import auth as auth_service
from services import catalog as catalog_service
from services import permissions as permissions_service
from services import predicates as predicates_service
from services import state as state_service
from services.auth import capabilities as capabilities_service
from services.errors import Codes, error_body
from utils.decorators import require_same_origin

logger = logging.getLogger(__name__)

claims_bp = Blueprint("claims", __name__, url_prefix="/api")


@claims_bp.route("/claim/<slug>", methods=["POST"])
@require_same_origin
def claim(slug: str):
    user, err = _require_user_or_401()
    if err is not None:
        return err
    assert user is not None  # err is None ⟺ user is non-None (helper invariant)
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "unknown reciter"}), 404

    # One-claim-per-user policy (application-level; state.py doesn't enforce).
    # Owners are exempt — they may hold multiple simultaneous claims.
    # Marked-ready rows are NOT blocking: once the reviewer submits, the
    # row is admin-side until publish or send-back, so the contributor is
    # free to pick up a new one. ``repo_claims.open_claim_for_user`` bakes
    # that filter into the SQL so this route, the ``can_claim`` predicate
    # path, and the auto-claim path all agree.
    if not permissions_service.is_owner(user):
        from services.db import repo_claims as _repo_claims

        other = _repo_claims.open_claim_for_user(user.hf_user_id)
        if other is not None and other != slug:
            other_name = catalog_service.display_name(other)
            target_name = catalog_service.display_name(slug)
            return jsonify(
                {
                    "error": f"already holding a claim on {other}",
                    "existing_claim": other,
                    "existing_claim_name": other_name,
                    "target_name": target_name,
                }
            ), 409

    new_row = state_service.transition(
        slug,
        "reciter.claimed",
        actor=_actor_for(user),
    )
    return jsonify(_row_to_dict(new_row))


@claims_bp.route("/release/<slug>", methods=["POST"])
@require_same_origin
def release(slug: str):
    user, err = _require_user_or_401()
    if err is not None:
        return err
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "unknown reciter"}), 404
    new_row = state_service.transition(
        slug,
        "reciter.released",
        actor=_actor_for(user),
    )
    return jsonify(_row_to_dict(new_row))


@claims_bp.route("/mark-ready/<slug>", methods=["POST"])
@require_same_origin
def mark_ready(slug: str):
    """Submit the mark-ready form for ``slug``.

    Body MUST be a ``MarkReadyRequest``: a six-key checklist (all True) +
    two optional comment strings. The state handler validates the payload,
    re-computes live validation category counts, and rejects with a
    structured 400 if either gate fails. The submission is persisted on
    the open claim row alongside ``marked_ready_at``.

    Holders of ``claim.mark_ready_skip_gates`` (owners by default) may
    POST an empty body — the handler skips the checklist + counts gates
    and stamps ``bypass_used=True`` on the persisted submission. The
    route's capability check is a UX shortcut; the handler always
    re-checks via the resolver, so a tier losing the cap mid-flight
    falls back to the normal gated path.
    """
    user, err = _require_user_or_401()
    if err is not None:
        return err
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "unknown reciter"}), 404

    raw = request.get_json(silent=True) or {}
    bypass = capabilities_service.can(user, "claim.mark_ready_skip_gates")

    if bypass:
        # Pass whatever the body carried (typically empty) through to the
        # handler; it skips Pydantic validation when bypass is in effect.
        payload: dict = dict(raw) if isinstance(raw, dict) else {}
    else:
        # Pydantic owns the wire-shape contract; route translates a
        # malformed body into a 400 with the same envelope as the
        # count-gated path.
        try:
            submission = MarkReadyRequest.model_validate(raw)
        except ValidationError as e:
            return jsonify(
                error_body(
                    "Your mark-ready submission was incomplete. Please review and resubmit.",
                    code=Codes.MARK_READY_PAYLOAD,
                    details={"validation_errors": e.errors()},
                )
            ), 400
        payload = submission.model_dump()

    new_row = state_service.transition(
        slug,
        "reciter.marked_ready",
        actor=_actor_for(user),
        payload=payload,
    )
    return jsonify(_row_to_dict(new_row))


@claims_bp.route("/unmark-ready/<slug>", methods=["POST"])
@require_same_origin
def unmark_ready(slug: str):
    user, err = _require_user_or_401()
    if err is not None:
        return err
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "unknown reciter"}), 404
    new_row = state_service.transition(
        slug,
        "reciter.unmarked_ready",
        actor=_actor_for(user),
    )
    return jsonify(_row_to_dict(new_row))


@claims_bp.route("/quality-status/<slug>", methods=["POST"])
@require_same_origin
def set_quality_status(slug: str):
    """Set or clear the delivery's manual recitation quality rating. The
    handler owns the capability + state gate (published or marked ready to
    set; any state to clear)."""
    user, err = _require_user_or_401()
    if err is not None:
        return err
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "unknown reciter"}), 404
    try:
        body = QualityStatusRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return jsonify(
            error_body(
                "Pick a valid quality status.",
                code=Codes.QUALITY_STATUS_PAYLOAD,
                details={"validation_errors": e.errors()},
            )
        ), 400
    status = body.quality_status
    new_row = state_service.transition(
        slug,
        "reciter.quality_set",
        actor=_actor_for(user),
        payload={"quality_status": status.value if status else None},
    )
    return jsonify(_row_to_dict(new_row))


@claims_bp.route("/reciter-task/<slug>")
def reciter_task(slug: str):
    """Full row + per-user predicates. Anonymous gets predicates all false."""
    user = auth_service.current_user()
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "unknown reciter"}), 404
    row = state_service.get_row(slug)
    if row is None:
        return jsonify({"error": "unknown reciter"}), 404

    has_other = False
    if user is not None:
        has_other = state_service.has_other_active_claim(
            user.hf_user_id,
            except_slug=slug,
        )

    return jsonify(
        {
            "row": _row_to_dict(row),
            "predicates": predicates_service.build_predicates(
                row,
                user,
                has_other_active_claim=has_other,
            ),
        }
    )
