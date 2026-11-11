"""Segments tab specials routes (/api/seg/specials) — read, and confirm a Basmala voice."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from qua_shared.schemas.wire._envelopes import ErrorEnvelope, OkAck
from qua_shared.schemas.wire.seg import SegSpecialsResponse, SegSpecialsVoiceRequest
from routes._admin_helpers import actor_for
from services import state as state_service
from services.segments import specials as specials_service
from utils.decorators import require_capability, require_same_origin

seg_specials_bp = Blueprint("seg_specials", __name__, url_prefix="/api/seg/specials")


def _error(message: str, status: int):
    return jsonify(ErrorEnvelope(error=message).model_dump(exclude_none=True)), status


@seg_specials_bp.route("/<reciter>")
def get_specials(reciter):
    if not state_service.has_content_access(reciter):
        return _error("Reciter not found", 404)
    doc = specials_service.read_doc(reciter)
    body = SegSpecialsResponse(chapters=doc.chapters if doc else {})
    return jsonify(body.model_dump(mode="json"))


@seg_specials_bp.route("/<reciter>/voice", methods=["POST"])
@require_same_origin
@require_capability("segments.confirm_basmala_voice")
def confirm_voice(user, reciter):
    if not state_service.has_content_access(reciter):
        return _error("Reciter not found", 404)
    try:
        body = SegSpecialsVoiceRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _error(f"invalid body: {exc.errors()[0]['msg']}", 400)
    found = specials_service.confirm_voice(
        reciter,
        chapter=body.chapter,
        start_ms=body.start_ms,
        same_voice=body.same_voice,
        actor=actor_for(user),
    )
    if not found:
        return _error("Special not found", 404)
    return jsonify(OkAck().model_dump())
