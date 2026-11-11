"""Segments tab validation, stats, and edit-history routes (/api/seg/)."""

from flask import Blueprint, jsonify

from qua_shared.schemas.wire._envelopes import ErrorEnvelope
from qua_shared.schemas.wire.seg import SegValidateResponse
from services import cache
from services import state as state_service
from services.auth import capabilities
from services.auth.auth import current_user
from services.history_query import load_edit_history
from services.segments.history_tiers import generation_timeline
from services.stats import compute_stats
from services.validation import review_signals, strip_boundary_review, validate_reciter_segments
from utils.json_response import orjson_cached_response

seg_val_bp = Blueprint("seg_val", __name__, url_prefix="/api/seg")


def _serialize_validate(result: dict) -> dict:
    """Project the validation result through ``SegValidateResponse``.

    The model is the wire contract for ``/api/seg/validate``; dumping through it
    pins the emitted key set. Two faithfulness details:

    * ``exclude_unset`` (not ``exclude_none``) — chapter-level items carry an
      explicit ``segment_uid: null`` that the wire emits and the FE relies on;
      ``exclude_none`` would strip it. ``exclude_unset`` keeps every key the
      service actually set (including explicit nulls) and drops only the
      optionals the service never populated.
    * ``stats`` is restored from the raw result verbatim because ``SegValStats``
      types its duration/pause fields as ``float`` and ``model_dump`` would
      coerce the integer zeros the service emits (``0`` → ``0.0``) — a byte
      change on the wire. The block is an opaque numeric map, so passing it
      through unchanged keeps the response identical.
    """
    dumped = SegValidateResponse.model_validate(result).model_dump(
        mode="json", exclude_unset=True, by_alias=True
    )
    if "stats" in result:
        dumped["stats"] = result["stats"]
    return dumped


def _viewer_payload(payload: dict) -> dict:
    """Redact the boundary-review categories for viewers without
    ``segments.view_boundary_review``. The cache holds the full payload (it is
    keyed by reciter only); the projection is per request."""
    if capabilities.can(current_user(), "segments.view_boundary_review"):
        return payload
    return strip_boundary_review(payload)


@seg_val_bp.route("/validate/<reciter>")
def seg_validate(reciter):
    """Validate all chapters for a reciter (cached; invalidated on save)."""
    if not state_service.has_content_access(reciter):
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    cached = cache.get_seg_validate_cache(reciter)
    if cached is not None:
        return orjson_cached_response(_viewer_payload(cached))
    result = validate_reciter_segments(reciter)
    if result is None:
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    payload = _serialize_validate(result)
    cache.set_seg_validate_cache(reciter, payload)
    review_signals.record(reciter, payload)
    return orjson_cached_response(_viewer_payload(payload))


@seg_val_bp.route("/stats/<reciter>")
def seg_stats(reciter):
    """Return segmentation statistics and histogram distributions (cached)."""
    if not state_service.has_content_access(reciter):
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    cached = cache.get_seg_stats_cache(reciter)
    if cached is not None:
        return orjson_cached_response(cached)
    result = compute_stats(reciter)
    if result is None:
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    cache.set_seg_stats_cache(reciter, result)
    return orjson_cached_response(result)


# v2: `seg_save_chart` removed — debug-only route with no UI surface.
# See inspector-cleanup-registry.md §2.


@seg_val_bp.route("/edit-history/<reciter>")
def seg_edit_history(reciter):
    """Return edit history batches, summary stats, and the TS-generation timeline.

    ``generations`` (the per-generation boundary list) is computed fresh from the
    release tables on each call and merged onto the cached ``{batches, summary}``
    blob — so the heavy edit-history parse stays cached while the boundary list
    stays current after a regeneration without an explicit cache bust.
    """
    if not state_service.has_content_access(reciter):
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    history = load_edit_history(reciter)
    payload = {**history, "generations": generation_timeline(reciter)}
    return orjson_cached_response(payload)
