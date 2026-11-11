"""Admin Reviews-tab endpoints (maintainer/owner only).

- ``GET  /api/admin/reviews/list``               master list of review rows.
- ``GET  /api/admin/reviews/<slug>``             per-slug detail for the
                                                 General drawer (current
                                                 claim, history, timeline,
                                                 job ids, flagged-issue count).
- ``POST /api/admin/generate-timestamps/<slug>`` launch a timestamps run.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from qua_shared.schemas import TsJobSettings
from routes._admin_helpers import require_capability_or_403
from services import permissions
from services.admin import reviews as reviews_service
from services.admin import timestamps_jobs as ts_jobs
from services.state import state as state_service
from utils.decorators import require_capability, require_same_origin

admin_reviews_bp = Blueprint("admin_reviews", __name__, url_prefix="/api/admin")


@admin_reviews_bp.route("/reviews/list")
@require_capability("reviews.view")
def list_reviews(user):
    return jsonify(
        reviews_service.list_reviews(
            include_everyayah=user is not None and permissions.is_owner(user)
        )
    )


@admin_reviews_bp.route("/reviews/<slug>")
@require_capability("reviews.view")
def review_detail(user, slug):
    detail = reviews_service.get_review_detail(
        slug, include_everyayah=user is not None and permissions.is_owner(user)
    )
    if detail is None:
        return jsonify({"error": "unknown slug"}), 404
    return jsonify(detail)


@admin_reviews_bp.route("/generate-timestamps/<slug>", methods=["POST"])
@require_same_origin
@require_capability("reviews.generate_timestamps")
def generate_timestamps(user, slug):
    """Launch the aligner timestamps run for a reciter.

    Two valid entry states (same route, same caps):

    - **First publish** — ``under_review`` + ``marked_ready``. On success the
      reciter is auto-released (``reciter.published``). Surfaced from the Reviews
      tab.
    - **Regenerate** — already ``released``. On success the reciter stays
      released; its HF/GH releases are stamped stale so the operator re-publishes
      (``reciter.ts_regenerated``). Surfaced from the Releases tab.

    Any other state (catalogued / awaiting_alignment / awaiting_review /
    under_review-without-mark-ready) has nothing to publish → 409. Generating
    timestamps IS publishing, so the caller must also hold ``reciter.publish``
    (checked inline — a second ``@require_capability`` decorator can't stack, it
    would inject ``user`` twice).

    Single-flight: rejects (409) if a job for ``slug`` is already running —
    two jobs would race the same ``timestamps/`` shards. Does NOT transition
    the reciter at launch; the launched job id is linked via
    ``timestamps_job_ids``. Returns 202 with ``{job_id, url}``.
    """
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "unknown slug"}), 404
    row = state_service.get_row(slug)
    if row is None:
        return jsonify({"error": "unknown slug"}), 404
    is_first_publish = row.state.value == "under_review" and row.marked_ready
    is_regen = row.state.value == "released"
    if not (is_first_publish or is_regen):
        return jsonify(
            {
                "error": "timestamps can only be generated for a marked-ready "
                "reciter (first publish) or an already-released reciter "
                "(regenerate)",
                "state": row.state.value,
            }
        ), 409
    err = require_capability_or_403(user, "reciter.publish")
    if err is not None:
        return err
    existing = ts_jobs.running_job_for(slug)
    if existing:
        return jsonify({"error": "a timestamps job is already running", "job_id": existing}), 409
    body = request.get_json(silent=True) or {}
    try:
        settings = _parse_ts_settings(body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    full = body.get("full", False)
    if not isinstance(full, bool):
        return jsonify({"error": "full must be a boolean"}), 400
    try:
        result = ts_jobs.launch(slug, settings=settings, full=full)
    except Exception as exc:  # surfaced to the drawer
        return jsonify({"error": str(exc)}), 502
    return jsonify(result), 202


def _parse_ts_settings(body: dict) -> TsJobSettings:
    """Build ``TsJobSettings`` from the request's ``chapters`` scope.

    An absent or empty list is the full reciter. Raises ``ValueError`` with a
    user-facing message on an invalid ``chapters`` field.
    """
    chapters_raw = body.get("chapters")
    if chapters_raw is None:
        return TsJobSettings()
    if not isinstance(chapters_raw, list) or not all(
        isinstance(c, int) and 1 <= c <= 114 for c in chapters_raw
    ):
        raise ValueError("chapters must be a list of integers in 1..114")
    return TsJobSettings(chapters=sorted(set(chapters_raw)) or None)


@admin_reviews_bp.route("/reciters/<slug>/jobs/<job_id>")
@require_capability("reviews.generate_timestamps")
def job_status(user, slug, job_id):
    """Live status + bounded log tail for a launched job (HF is authoritative).

    Reciter-scoped: the durable record lives at ``reciters/<slug>/jobs/ts/`` so
    the slug is needed to read/backstop it (the drawer always has it)."""
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "unknown slug"}), 404
    try:
        return jsonify(ts_jobs.job_status(slug, job_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@admin_reviews_bp.route("/reciters/<slug>/jobs/<job_id>/cancel", methods=["POST"])
@require_same_origin
@require_capability("reviews.generate_timestamps")
def cancel_job(user, slug, job_id):
    """Cancel an in-flight timestamps job for ``slug``.

    Same gate as launching (``reviews.generate_timestamps``) — anyone who can
    start a job can stop it. Returns 200 on success with the reconciled
    status, 404 if the slug is unknown, 502 if the HF API call failed (the
    job stays in whatever state HF reports — caller can retry)."""
    if not state_service.is_delivery_visible(slug, user) or state_service.get_row(slug) is None:
        return jsonify({"error": "unknown slug"}), 404
    try:
        result = ts_jobs.cancel_job(slug, job_id)
    except Exception as exc:  # surfaced to the drawer
        return jsonify({"error": str(exc)}), 502
    if not result.get("canceled"):
        return jsonify({"error": result.get("reason", "cancel failed")}), 502
    return jsonify(result)


@admin_reviews_bp.route("/reciters/<slug>/jobs/<job_id>/record")
@require_capability("reviews.generate_timestamps")
def job_record(user, slug, job_id):
    """Persisted record (settings + status + full logs) for one past job."""
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "no record for job"}), 404
    rec = ts_jobs.read_job_record(slug, job_id)
    if rec is None:
        return jsonify({"error": "no record for job"}), 404
    return jsonify(rec)


@admin_reviews_bp.route("/reciters/<slug>/ts-jobs")
@require_capability("reviews.generate_timestamps")
def reciter_ts_jobs(user, slug):
    """Persisted timestamps-job records for ``slug`` (newest first)."""
    if not state_service.is_delivery_visible(slug, user) or state_service.get_row(slug) is None:
        return jsonify({"error": "unknown slug"}), 404
    return jsonify({"jobs": ts_jobs.list_job_records(slug)})
