"""Admin Reviews-tab endpoints (maintainer/owner only).

- ``GET  /api/admin/reviews/list``               master list of review rows.
- ``GET  /api/admin/reviews/<slug>``             per-slug detail for the
                                                 General drawer (current
                                                 claim, history, timeline,
                                                 job ids, flagged-issue count).

The marked-ready unviewed-count / per-admin view-mark surface was retired with
the Releases-tab restructure — the marked-ready queue moved to Releases and no
longer carries a notification.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from qua_shared.schemas import TsJobSettings
from routes._admin_helpers import require_capability_or_403
from services import permissions
from services.admin import aligner_models as aligner_models_service
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


@admin_reviews_bp.route("/aligner-models")
@require_capability("reviews.generate_timestamps")
def aligner_models(user):
    """Selectable acoustic models for the shared Timestamps-generation defaults card."""
    return jsonify({"models": aligner_models_service.list_models()})


@admin_reviews_bp.route("/generate-timestamps/<slug>", methods=["POST"])
@require_same_origin
@require_capability("reviews.generate_timestamps")
def generate_timestamps(user, slug):
    """Launch the in-container MFA timestamps job for a reciter.

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
    # Public URL root for the job's completion callback. Deployed: ProxyFix
    # makes this the real https Space URL; dev: localhost (unreachable by the
    # job — the poll fallback releases instead). launch() only uses it when a
    # webhook secret is configured.
    webhook_base = request.url_root
    try:
        result = ts_jobs.launch(slug, settings=settings, webhook_base=webhook_base)
    except Exception as exc:  # surfaced to the drawer
        return jsonify({"error": str(exc)}), 502
    return jsonify(result), 202


def _parse_ts_settings(body: dict) -> TsJobSettings:
    """Build ``TsJobSettings`` for a manual launch.

    Every TS tunable (beam/model/workers/batch_size/download_workers/
    padding/method) comes from the owner-wide ``ts_generation_defaults`` — the
    single shared blob edited from the Releases-tab "Timestamps generation"
    accordion (the same source the automations read). The request body carries
    only the per-launch ``chapters`` scope (which chapters to regenerate); no
    settings. Raises ``ValueError`` with a user-facing message on an invalid
    ``chapters`` field.
    """
    from services.admin.automation import config as automation_config

    defaults = automation_config.load_config().ts_generation_defaults

    beams: list[int] = [defaults.beam]

    chapters_raw = body.get("chapters")
    chapters: list[int] | None = None
    if chapters_raw is not None:
        if not isinstance(chapters_raw, list) or not all(
            isinstance(c, int) and 1 <= c <= 114 for c in chapters_raw
        ):
            raise ValueError("chapters must be a list of integers in 1..114")
        chapters = sorted(set(chapters_raw))
        if not chapters:
            chapters = None  # empty list = full reciter
    try:
        return TsJobSettings(
            beams=beams,
            aligner_model=defaults.aligner_model,
            chapters=chapters,
            workers=defaults.workers,
            batch_size=defaults.batch_size,
            download_workers=defaults.download_workers,
            padding=defaults.padding,
            method=defaults.method,
        )
    except ValidationError as exc:
        raise ValueError(f"invalid settings: {exc.errors()[0].get('msg', exc)}") from exc


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
