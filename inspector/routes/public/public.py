"""Public read-only endpoints powering the Phase 6 dashboard.

All responses are public — no authentication required, no per-user
predicates included. Assignee identity is redacted in
``services/public_state`` before the data ever reaches this layer.

The list/stats reads are ``Cache-Control: no-store``: they reflect reciter
lifecycle state (claims, transitions) that an editor changes live, and a
client cache made the dashboard status + reciter list show stale state for up
to its max-age after a transition. Server-side cost is already absorbed by the
``db_seq``-keyed read cache (``services/storage/cache.py``), so client caching
only bought staleness.

Slice B of phase 6.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from qua_shared.schemas import (
    AdminViewReciter,
    BucketCounts,
    ErrorEnvelope,
    PublicReciter,
    PublicReciterPage,
)
from services import db as db_service
from services import permissions
from services import public_activity as public_activity_service
from services import public_state as public_state_service
from services import search_normalize as search_normalize_service
from services import space_info as space_info_service
from services.auth import capabilities as cap_service
from utils.decorators import require_capability

public_bp = Blueprint("public", __name__, url_prefix="/api/public")


# no-store: these lists carry live lifecycle state (claims/transitions). A
# client max-age made the dashboard status pill + reciter list stale for up to
# 30s after an action; the server-side db_seq read cache absorbs the recompute.
_LIST_CACHE = "no-store"

_VALID_BUCKETS = {
    "available_for_request",
    "requested",
    "available_for_review",
    "under_review",
    "publishing",
    "published",
}

_VALID_SORTS = {"recent", "alphabetical", "deliveries"}


def _wire(model) -> dict:
    """Serialize a wire model to the exact JSON the route emits.

    ``exclude_unset`` keeps the present-with-null fields the producer sets
    explicitly (``state_since`` / ``next_cursor`` / ``country`` / …) while
    dropping the modal-only delivery keys (``bucket_dates`` / ``ts_refresh_dates``)
    on the cached list path, where the producer never sets them.
    """
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)


def _with_cache(payload, cache: str):
    """Attach a Cache-Control header to a jsonify() response."""
    resp: Response = jsonify(payload)
    resp.headers["Cache-Control"] = cache
    return resp


@public_bp.route("/version")
@require_capability("view.catalog")
def version(user):
    """Monotonic catalog version (``db_seq``), bumped by every committed write.

    A cheap change-probe the dashboard polls in place of refetching the whole
    roster every tick: the client only re-pulls ``/reciters`` + ``/stats`` when
    this number moves. no-store so a poll always sees the live counter.
    """
    return _with_cache({"db_seq": db_service.current_db_seq()}, "no-store")


@public_bp.route("/space")
def space():
    """Space identity for the FE's HF mini header — ``space_id`` null off-Space.

    Unauthenticated on purpose: this is public metadata, and the FE needs it
    before any identity call. HF only renders its own chrome on the
    ``huggingface.co/spaces/...`` page, so a custom domain or the direct
    ``*.hf.space`` URL gets no header unless the app draws one itself. We
    return ``author``/``likes`` alongside the id so the FE never has to make
    the browser-side Hub call, which 401s on a protected Space.

    no-store: the payload is a few dozen bytes fetched once per page load, and
    the like count already carries an hour-long server-side cache. A client
    max-age bought nothing and cost correctness — a browser that cached the
    first shipped shape kept replaying it across deploys, so the FE saw a
    payload with no ``author`` and silently drew no header.
    """
    return _with_cache(space_info_service.describe(), "no-store")


@public_bp.route("/stats")
@require_capability("view.catalog")
def stats(user):
    """Counts per public bucket. Mutually exclusive at the reciter level."""
    counts = BucketCounts.model_validate(
        public_state_service.stats(
            include_everyayah=user is not None and permissions.is_owner(user)
        )
    )
    return _with_cache(counts.model_dump(), _LIST_CACHE)


@public_bp.route("/activity")
@require_capability("view.public_activity")
def activity(user):
    """Paginated public activity feed.

    Six event kinds only — every other audit record is redacted at the
    service layer. Cache-Control: no-store so 30s polling stays fresh.
    """
    try:
        cursor = int(request.args.get("cursor") or 0)
    except ValueError:
        return jsonify({"error": "cursor must be an integer"}), 400
    if cursor < 0:
        return jsonify({"error": "cursor must be >= 0"}), 400

    try:
        limit = int(request.args.get("limit") or 50)
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    if limit < 1 or limit > 500:
        return jsonify({"error": "limit must be between 1 and 500"}), 400

    # Identity disclosure on the rail is a toggleable capability (default
    # owner-only). The service stays capability-agnostic — it takes a bool.
    include_identity = cap_service.can(user, "identity.see_actor")
    payload = public_activity_service.feed(
        cursor=cursor,
        limit=limit,
        include_identity=include_identity,
        include_everyayah=user is not None and permissions.is_owner(user),
    )
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@public_bp.route("/reciter/<reciter_id>")
@require_capability("view.catalog")
def reciter_detail(user, reciter_id: str):
    """Single-reciter detail payload for the dashboard detail page.

    For anonymous + contributor callers: returns the public shape, with
    discarded combos filtered out (404 if every combo is discarded).

    For maintainer + owner callers: returns the admin shape with a
    ``discarded_deliveries`` array surfacing the hidden combos in a
    separate list so the modal can render a maintainer-only section. A
    fully-discarded reciter still returns 200 in this case (with empty
    ``deliveries`` and populated ``discarded_deliveries``) — admins need
    to be able to navigate to + un-discard such a reciter.

    The caller's role is read from the session cookie; no extra auth
    parameter is required (callers without a cookie just get the public
    shape).
    """
    caller = user
    is_admin = caller is not None and permissions.is_maintainer(caller)

    if is_admin:
        admin_payload = public_state_service.admin_view_reciter(
            reciter_id,
            include_everyayah=caller is not None and permissions.is_owner(caller),
        )
        if admin_payload is None:
            return jsonify(
                ErrorEnvelope(error="reciter not found").model_dump(exclude_none=True)
            ), 404
        admin_model = AdminViewReciter.model_validate(admin_payload)
        resp = jsonify(_wire(admin_model))
        resp.headers["Cache-Control"] = "no-store"  # admin view varies by caller
        return resp

    public = public_state_service.detail(reciter_id)
    if public is None:
        return jsonify(ErrorEnvelope(error="reciter not found").model_dump(exclude_none=True)), 404
    # no-store: the detail payload folds in per-delivery lifecycle state
    # (status, which combos are discarded) that changes on claim/transition;
    # a client max-age made the detail modal stale for up to a minute.
    return _with_cache(_wire(PublicReciter.model_validate(public)), "no-store")


@public_bp.route("/reciters")
@require_capability("view.catalog")
def reciters(user):
    """Paginated reciter list.

    Query parameters:
    - ``bucket`` — filter to reciters whose ``primary_bucket`` matches.
      Repeat the parameter to OR multiple buckets.
    - ``search`` — Arabic-normalized substring match against reciter name.
    - ``sort`` — ``recent`` (default) | ``alphabetical`` | ``deliveries``.
    - ``cursor`` — opaque pagination cursor (integer offset).
    - ``limit`` — page size; defaults to 50, max 200.
    """
    buckets = set(request.args.getlist("bucket"))
    invalid = buckets - _VALID_BUCKETS
    if invalid:
        return jsonify(
            ErrorEnvelope(error=f"invalid bucket(s): {sorted(invalid)!r}").model_dump(
                exclude_none=True
            )
        ), 400

    search = (request.args.get("search") or "").strip()
    sort = (request.args.get("sort") or "recent").strip()
    if sort not in _VALID_SORTS:
        return jsonify(
            ErrorEnvelope(
                error=f"invalid sort: {sort!r}; must be one of {sorted(_VALID_SORTS)!r}"
            ).model_dump(exclude_none=True)
        ), 400

    try:
        cursor = int(request.args.get("cursor") or 0)
    except ValueError:
        return jsonify(
            ErrorEnvelope(error="cursor must be an integer").model_dump(exclude_none=True)
        ), 400
    if cursor < 0:
        return jsonify(
            ErrorEnvelope(error="cursor must be >= 0").model_dump(exclude_none=True)
        ), 400

    try:
        limit = int(request.args.get("limit") or 50)
    except ValueError:
        return jsonify(
            ErrorEnvelope(error="limit must be an integer").model_dump(exclude_none=True)
        ), 400
    if limit < 1 or limit > 500:
        return jsonify(
            ErrorEnvelope(error="limit must be between 1 and 500").model_dump(exclude_none=True)
        ), 400

    rows = public_state_service.all_public_reciters(
        include_everyayah=user is not None and permissions.is_owner(user)
    )

    if buckets:
        rows = [r for r in rows if r["primary_bucket"] in buckets]

    if search:
        rows = [r for r in rows if search_normalize_service.matches(r["name"], search)]

    if sort == "recent":
        # Most-recently active first; reciters with no activity sink to bottom.
        rows.sort(
            key=lambda r: (r["last_activity"] is not None, r["last_activity"] or ""),
            reverse=True,
        )
    elif sort == "alphabetical":
        rows.sort(key=lambda r: search_normalize_service.normalize_arabic(r["name"]))
    elif sort == "deliveries":
        rows.sort(key=lambda r: r["deliveries_count"], reverse=True)

    total = len(rows)
    page = rows[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < total else None

    page_model = PublicReciterPage.model_validate(
        {
            "reciters": page,
            "total": total,
            "next_cursor": next_cursor,
        }
    )
    return _with_cache(_wire(page_model), _LIST_CACHE)
