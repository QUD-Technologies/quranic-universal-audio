"""Native align pipeline routes — the Requests-tab **Align** button.

- ``POST /api/admin/reciter/<slug>/align``          start a run (202 + status)
- ``GET  /api/admin/reciter/<slug>/align/status``   the slug's active/latest run
- ``POST /api/admin/reciter/<slug>/align/retry``    resume a failed run
- ``POST /api/admin/reciter/<slug>/align/cancel``   stop a run

Gated by the ``intake.align`` capability (owner + maintainer by default) through
the resolver. Two credentials are accepted, the same pair as the intake ingest
route: the ``inspector_session`` cookie (dashboard; POSTs must be same-origin)
or an ``Authorization: Bearer <HF token>`` of an owner (server-to-server — an
operator script driving a run; a bearer cannot be CSRF'd so no origin check).
Refusals come back as ``{"error": …}`` with the service's status (404 unknown
slug, 409 wrong state / already running, 429 shared align budget spent — see
``align_pipeline/limits.py``, 503 pipeline unconfigured).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from qua_shared.schemas import Actor, AlignStartRequest
from routes._admin_helpers import actor_for
from services import auth as auth_service
from services.admin.align_pipeline import limits as align_limits
from services.admin.align_pipeline import runs as align_runs
from services.auth import capabilities as cap_service
from services.auth import token_auth

log = logging.getLogger("inspector")

admin_align_bp = Blueprint("admin_align", __name__, url_prefix="/api/admin")

CAPABILITY = "intake.align"


def _authorize(*, mutating: bool) -> tuple[Actor | None, tuple | None]:
    """``(actor, None)`` for a caller holding ``intake.align``, else ``(None, (resp, status))``."""
    actor, _exempt, err = _authorize_with_exemption(mutating=mutating)
    return actor, err


def _authorize_with_exemption(*, mutating: bool) -> tuple[Actor | None, bool, tuple | None]:
    """``_authorize`` plus whether the caller bypasses the shared align budget
    (an owner bearer always does; a session user when they hold the capability)."""
    token = token_auth.bearer_token_from_header(request.headers.get("Authorization"))
    if token is not None:
        try:
            return token_auth.resolve_owner_from_token(token), True, None
        except token_auth.TokenAuthError:
            return None, False, (jsonify({"error": "invalid bearer token"}), 401)
        except token_auth.NotOwner:
            return None, False, (jsonify({"error": "owner role required"}), 403)

    user = auth_service.current_user()
    if user is None:
        return None, False, (jsonify({"error": "authentication required"}), 401)
    if not cap_service.can(user, CAPABILITY):
        return None, False, (jsonify({"error": "insufficient permission for this action"}), 403)
    if mutating:
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        p = urlparse(origin)
        if not (origin and p.scheme == request.scheme and p.netloc == request.host):
            return None, False, (jsonify({"error": "cross-origin request rejected"}), 403)
    return actor_for(user), cap_service.can(user, align_limits.EXEMPT_CAPABILITY), None


def _status_payload(status):
    return jsonify(status.model_dump(mode="json") if status is not None else {"run": None})


@admin_align_bp.route("/reciter/<slug>/align", methods=["POST"])
def start_align(slug: str):
    actor, exempt, err = _authorize_with_exemption(mutating=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        body = AlignStartRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return jsonify({"error": "invalid body", "detail": exc.errors()}), 400
    try:
        status = align_runs.start(
            slug, actor, model_name=body.model_name, device=body.device, exempt=exempt
        )
    except align_runs.AlignRunError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return _status_payload(status), 202


@admin_align_bp.route("/reciter/<slug>/align/status", methods=["GET"])
def align_status(slug: str):
    _actor, err = _authorize(mutating=False)
    if err is not None:
        return err
    resp = _status_payload(align_runs.status_for_slug(slug))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@admin_align_bp.route("/reciter/<slug>/align/retry", methods=["POST"])
def retry_align(slug: str):
    actor, err = _authorize(mutating=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        status = align_runs.retry(slug, actor)
    except align_runs.AlignRunError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return _status_payload(status), 202


@admin_align_bp.route("/reciter/<slug>/align/cancel", methods=["POST"])
def cancel_align(slug: str):
    actor, err = _authorize(mutating=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        status = align_runs.cancel(slug, actor)
    except align_runs.AlignRunError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return _status_payload(status)
