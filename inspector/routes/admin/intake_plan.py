"""Online intake routes — plan a slugless submission and mint + align it.

- ``GET  /api/admin/intake/<id>/plan``   the stored plan + live check (``null`` if none)
- ``POST /api/admin/intake/<id>/plan``   (re)enumerate the source in the background
- ``PUT  /api/admin/intake/<id>/plan``   save the owner's chapter + identity review
- ``POST /api/admin/intake/<id>/align``  mint the delivery and start its align run

Gated by ``intake.ingest`` (owner by default); ``/align`` also needs
``intake.align``. Same credentials as the align routes: the session cookie
(POST/PUT same-origin) or an owner's bearer token.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from qua_shared.schemas import IntakeAlignRequest, IntakePlanUpdate
from routes.admin.align import authorize_with_exemption
from services.admin import intake as intake_service
from services.admin.intake_plan import mint as intake_mint
from services.admin.intake_plan import plan as intake_plan
from services.state import state as state_service

admin_intake_plan_bp = Blueprint("admin_intake_plan", __name__, url_prefix="/api/admin")

CAPABILITY = "intake.ingest"


def _view_payload(view):
    return jsonify(view.model_dump(mode="json") if view is not None else {"plan": None})


@admin_intake_plan_bp.route("/intake/<rid>/plan", methods=["GET"])
def get_plan(rid: str):
    _actor, _exempt, err = authorize_with_exemption(mutating=False, capabilities=(CAPABILITY,))
    if err is not None:
        return err
    try:
        view = intake_plan.get(rid)
    except intake_plan.PlanError as exc:
        return jsonify({"error": str(exc)}), exc.status
    resp = _view_payload(view)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@admin_intake_plan_bp.route("/intake/<rid>/plan", methods=["POST"])
def build_plan(rid: str):
    _actor, _exempt, err = authorize_with_exemption(mutating=True, capabilities=(CAPABILITY,))
    if err is not None:
        return err
    try:
        view = intake_plan.build(rid)
    except intake_plan.PlanError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return _view_payload(view), 202


@admin_intake_plan_bp.route("/intake/<rid>/plan", methods=["PUT"])
def update_plan(rid: str):
    _actor, _exempt, err = authorize_with_exemption(mutating=True, capabilities=(CAPABILITY,))
    if err is not None:
        return err
    try:
        body = IntakePlanUpdate.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return jsonify({"error": "invalid body", "detail": exc.errors()}), 400
    try:
        view = intake_plan.update(rid, body)
    except intake_plan.PlanError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return _view_payload(view)


@admin_intake_plan_bp.route("/intake/<rid>/align", methods=["POST"])
def mint_and_align(rid: str):
    actor, exempt, err = authorize_with_exemption(
        mutating=True, capabilities=(CAPABILITY, "intake.align")
    )
    if err is not None:
        return err
    assert actor is not None
    try:
        body = IntakeAlignRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return jsonify({"error": "invalid body", "detail": exc.errors()}), 400
    try:
        result = intake_mint.mint_and_align(rid, actor, device=body.device, exempt=exempt)
    except intake_plan.PlanError as exc:
        return jsonify({"error": str(exc)}), exc.status
    except intake_service.IngestSlugCollision as exc:
        return jsonify({"error": str(exc)}), 409
    except intake_service.IngestVocabMissing as exc:
        return jsonify({"error": str(exc)}), 422
    except (intake_service.IngestError, state_service.InvalidTransition) as exc:
        return jsonify({"error": str(exc)}), 400
    except state_service.NotAuthorizedForTransition as exc:
        return jsonify({"error": str(exc)}), 403
    return jsonify(result.model_dump(mode="json")), 201
