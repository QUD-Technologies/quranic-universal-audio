"""Internal machine-to-machine admin endpoints (secret-gated, no OAuth/CSRF).

Endpoints here are called by Inspector's own out-of-band tooling — manual
backfills, schema-bump re-stamps, local TS regens — not by a browser. Auth is
the same shared-secret mechanism the job-completion webhook uses: an
``X-Inspector-Job-Secret`` header HMAC-compared against ``INSPECTOR_WEBHOOK_SECRET``.
When the secret env is unset the endpoints are disabled (503) — dev tooling that
runs against a local Inspector either configures the secret or accepts that the
refresh stays silent.

``POST /api/admin/internal/ts-refreshed`` — record an out-of-band TS shard
refresh so it is no longer silent: advances the reciter's ``ts`` release
``produced_at`` (clearing computed staleness), re-stamps HF/GH stale, and audits
``reciter.ts_refreshed``, and rebuilds the reciter's readings summary from the new
shards on a background thread. Idempotent + best-effort on the caller's side (the
shared ``qua_shared.inspector_notify.notify_ts_refreshed`` helper swallows
errors so a failed callback never fails the backfill).
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from qua_shared.schemas import ErrorEnvelope, OkAck, TsRefreshedRequest

log = logging.getLogger("inspector")

admin_internal_bp = Blueprint("admin_internal", __name__, url_prefix="/api/admin/internal")

_SECRET_HEADER = "X-Inspector-Job-Secret"


def _check_secret() -> tuple[bool, tuple]:
    """HMAC-compare the ``X-Inspector-Job-Secret`` header against the env secret.

    Returns ``(ok, (response, status))``. 503 when the secret env is unset
    (disabled), 401 on a missing/mismatched header.
    """
    secret = os.environ.get("INSPECTOR_WEBHOOK_SECRET", "").strip()
    if not secret:
        return False, (
            jsonify(
                ErrorEnvelope(error="internal endpoints disabled").model_dump(exclude_none=True)
            ),
            503,
        )
    provided = request.headers.get(_SECRET_HEADER, "")
    if not provided or not hmac.compare_digest(provided, secret):
        return False, (
            jsonify(ErrorEnvelope(error="unauthorized").model_dump(exclude_none=True)),
            401,
        )
    return True, (None, None)


@admin_internal_bp.route("/ts-refreshed", methods=["POST"])
def ts_refreshed():
    """Record an out-of-band TS shard refresh for a reciter. Idempotent.

    Body: ``TsRefreshedRequest{slug, chapters?, reason?, produced_at?}``. Updates
    the current ``ts`` release ``produced_at`` to the supplied/``now`` watermark,
    re-stamps HF/GH stale, emits ``reciter.ts_refreshed`` and rebuilds ``readings.json`` in
    the background. A reciter with no
    current ``ts`` release (never generated) is acked with ``refreshed: false``.
    """
    ok, err = _check_secret()
    if not ok:
        return err

    try:
        body = TsRefreshedRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return jsonify(
            ErrorEnvelope(error="invalid body", detail=str(exc)).model_dump(exclude_none=True)
        ), 400

    at = datetime.now(UTC)
    if body.produced_at:
        try:
            at = datetime.fromisoformat(body.produced_at.replace("Z", "+00:00"))
        except ValueError:
            return jsonify(
                ErrorEnvelope(error="produced_at is not ISO-8601").model_dump(exclude_none=True)
            ), 400

    from services.db import repo_releases
    from services.db.sync import durable_transaction

    try:
        with durable_transaction():
            refreshed = repo_releases.mark_ts_refreshed(
                body.slug, at=at, chapters=body.chapters, reason=body.reason
            )
    except Exception as exc:  # noqa: BLE001 — surface as 502 so the caller can log
        log.warning("ts-refreshed for %s failed: %s", body.slug, exc)
        return jsonify(ErrorEnvelope(error=str(exc)).model_dump(exclude_none=True)), 502

    from services.reference import readings

    readings.refresh_in_background(body.slug)
    log.info("ts-refreshed slug=%s chapters=%s refreshed=%s", body.slug, body.chapters, refreshed)
    return jsonify({**OkAck().model_dump(), "refreshed": refreshed})
