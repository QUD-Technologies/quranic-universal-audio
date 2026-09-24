"""Admin Requests-tab read models (Flask-free).

``list_requests()`` assembles the review-queue payload for one status facet:
a catalog-joined base list (cached on ``db_seq`` like ``admin/users.py``) with
a per-caller overlay applied live (requester redaction for maintainers).
"Incoming request" awareness is surfaced via the per-user My Notifications rail
(``services/notifications``) — this tab has no unviewed badge of its own.

The proposed-changes ``changes`` list is built strictly over the
``ProposedEdits`` field set (the same fields the request form can edit), with
the current catalog value joined in as the ``from`` side.
"""

from __future__ import annotations

import logging
from typing import cast

from qua_shared.catalog_visibility import is_everyayah_channel
from services.db import _serde, repo_requests
from services.db.connection import current_db_seq
from services.state import catalog as catalog_service
from services.storage import cache

# UI status facet → DB status column value.
log = logging.getLogger("inspector")

_STATUS_DB = {
    "open": "pending",
    "accepted": "accepted",
    "returned": "returned",
    "discarded": "discarded",
}

# (key, label) — MUST mirror ProposedEdits / the request form's editable fields.
_PROPOSED_FIELDS: list[tuple[str, str]] = [
    ("riwayah", "Riwayah"),
    ("style", "Style"),
    ("name_en", "English name"),
    ("name_ar", "Arabic name"),
    ("country", "Country"),
    ("recording_context", "Recording context"),
    ("recording_year", "Recording year"),
]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_requests(
    *,
    status: str,
    caller_is_owner: bool,
    caller_hf_id: str,
    align_exempt: bool | None = None,
) -> dict:
    """Assembled Requests-tab payload (``AdminRequestsResponse`` shape).

    ``status`` is a UI facet (``open``/``accepted``/``returned``/``discarded``).
    ``align_exempt`` is ``None`` when the caller cannot align (no budget in the
    payload), else whether they bypass the shared align budget.
    Returns a plain dict ready to ``jsonify``.
    """
    db_status = _STATUS_DB.get(status, "pending")
    db_seq = current_db_seq()

    base = cast("list[dict] | None", cache.get_admin_requests_cache(db_seq, db_status))
    if base is None:
        base = _build_base_rows(db_status)
        cache.set_admin_requests_cache(db_seq, db_status, base)

    visible_base = [row for row in base if caller_is_owner or not row.get("_everyayah", False)]
    rows = [_serialize(r, owner=caller_is_owner) for r in visible_base]
    if db_status == "pending":
        _overlay_align_runs(rows)

    counts: dict[str, int] = {}
    for db_key in _STATUS_DB.values():
        status_base = cast("list[dict] | None", cache.get_admin_requests_cache(db_seq, db_key))
        if status_base is None:
            status_base = _build_base_rows(db_key)
            cache.set_admin_requests_cache(db_seq, db_key, status_base)
        counts[db_key] = sum(
            1 for row in status_base if caller_is_owner or not row.get("_everyayah", False)
        )
    payload: dict = {
        "rows": rows,
        "counts": {
            "open": counts.get("pending", 0),
            "accepted": counts.get("accepted", 0),
            "returned": counts.get("returned", 0),
            "discarded": counts.get("discarded", 0),
        },
    }
    if db_status == "pending" and align_exempt is not None:
        payload["align_quota"] = _align_quota(exempt=align_exempt)
    return payload


# ---------------------------------------------------------------------------
# Base-row assembly (caller-agnostic; cached)
# ---------------------------------------------------------------------------


_INTAKE_KINDS = ("existing_reciter_new_combo", "new_reciter")


def _build_base_rows(db_status: str) -> list[dict]:
    out: list[dict] = []
    for row in repo_requests.admin_list_rows(status=db_status):
        payload = _serde.json_loads(row["payload"]) or {}
        slug = row["slug"]
        kind = row["kind"]
        proposed = payload.get("proposed_edits") or {}
        is_intake = kind in _INTAKE_KINDS

        delivery = catalog_service.find_delivery(slug) if slug else None
        # Resolve the reciter: from the delivery (slug-based / accepted intake),
        # else from the payload's reciter_id (new-combo against an existing
        # reciter). new_reciter has neither — identity lives in proposed_edits.
        reciter_id = delivery.reciter_id if delivery is not None else payload.get("reciter_id")
        reciter = catalog_service.find_reciter(reciter_id) if reciter_id else None

        if delivery is not None:
            name_en = (
                reciter.name_en
                if reciter is not None
                else (catalog_service.display_name(slug) or slug)
            )
            name_ar = reciter.name_ar if reciter is not None else None
            riwayah = delivery.riwayah
            style = delivery.style
        elif kind == "new_reciter":
            name_en = proposed.get("name_en")
            name_ar = proposed.get("name_ar")
            riwayah = proposed.get("riwayah")
            style = proposed.get("style")
        else:  # existing_reciter_new_combo (no delivery yet)
            name_en = reciter.name_en if reciter is not None else None
            name_ar = reciter.name_ar if reciter is not None else None
            riwayah = proposed.get("riwayah")
            style = proposed.get("style")

        current = _current_values(delivery, reciter)
        if kind == "existing_reciter_new_combo":
            conflict = _intake_combo_conflict(reciter_id, proposed)
        else:
            conflict = _conflict(slug, delivery, proposed)

        out.append(
            {
                "id": row["id"],
                "slug": slug,
                "kind": kind,
                "status": row["status"],
                "submitted_at": row["submitted_at"],
                "resolved_at": row["resolved_at"],
                "resolution_reason": row["resolution_reason"],
                "auto_claim": bool(row["auto_claim"]),
                "comments": row["comments"],
                "reciter_id": reciter_id,
                "name_en": name_en,
                "name_ar": name_ar,
                "riwayah": riwayah,
                "style": style,
                "proposed_edits": proposed,
                "changes": _changes_list(proposed, current),
                "conflict": conflict,
                "source": payload.get("source") if is_intake else None,
                "probe": payload.get("probe") if is_intake else None,
                "_requester": payload.get("requester") or {},
                "_transitioned_by": payload.get("transitioned_by") or {},
                "_everyayah": delivery is not None and is_everyayah_channel(delivery.channel),
            }
        )
    return out


def _intake_combo_conflict(reciter_id, proposed: dict) -> bool:
    """True iff a new-combo intake's proposed (riwayah, style) already exists as
    a delivery of the same reciter. Advisory; mirrors ``_conflict`` for the
    slugless case."""
    if not reciter_id:
        return False
    riwayah = proposed.get("riwayah")
    style = proposed.get("style")
    if not riwayah or not style:
        return False
    catalog = catalog_service.snapshot()
    return any(
        d.reciter_id == reciter_id and d.riwayah == riwayah and d.style == style
        for d in catalog.deliveries
    )


def _current_values(delivery, reciter) -> dict:
    cur: dict[str, object] = {}
    if delivery is not None:
        cur["riwayah"] = delivery.riwayah
        cur["style"] = delivery.style
        cur["recording_context"] = getattr(delivery, "recording_context", None)
        cur["recording_year"] = getattr(delivery, "recording_year", None)
    if reciter is not None:
        cur["name_en"] = reciter.name_en
        cur["name_ar"] = reciter.name_ar
        cur["country"] = getattr(reciter, "country", None)
    return cur


def _changes_list(proposed: dict, current: dict) -> list[dict]:
    """One entry per populated ProposedEdits field, with the joined ``from``."""
    out: list[dict] = []
    for key, label in _PROPOSED_FIELDS:
        new = proposed.get(key)
        if new is None:
            continue
        out.append({"field": key, "label": label, "from": current.get(key), "to": new})
    return out


def _conflict(slug, delivery, proposed: dict) -> bool:
    """True iff the proposed (riwayah, style) — when changed — collides with
    another delivery of the same reciter. Mirrors the request form's predicate
    and ``pending_requests._apply_edits``; non-blocking, advisory only."""
    if delivery is None:
        return False
    proposed_riwayah = proposed.get("riwayah") or delivery.riwayah
    proposed_style = proposed.get("style") or delivery.style
    if proposed_riwayah == delivery.riwayah and proposed_style == delivery.style:
        return False
    catalog = catalog_service.snapshot()
    return any(
        d.slug != slug
        and d.reciter_id == delivery.reciter_id
        and d.riwayah == proposed_riwayah
        and d.style == proposed_style
        for d in catalog.deliveries
    )


def _overlay_align_runs(rows: list[dict]) -> None:
    """Attach the slug's active native align run to each open slug row."""
    from services.admin.align_pipeline import runs as align_runs

    try:
        active = align_runs.active_status_by_slug()
    except Exception as exc:  # noqa: BLE001 — the queue must render without it
        log.warning("admin requests: align overlay failed: %s", exc)
        return
    for row in rows:
        run = active.get(row.get("slug") or "")
        if run is not None:
            row["align"] = run.model_dump(mode="json")


def _align_quota(*, exempt: bool) -> dict | None:
    from services.admin.align_pipeline import limits as align_limits

    try:
        return align_limits.quota(exempt=exempt).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 — the queue must render without it
        log.warning("admin requests: align quota failed: %s", exc)
        return None


def _serialize(base_row: dict, *, owner: bool) -> dict:
    out = {k: v for k, v in base_row.items() if not k.startswith("_")}
    requester = base_row["_requester"]
    out["requester_role"] = requester.get("role")
    if owner:
        out["requester_login"] = requester.get("login_at_time")
        out["requester_hf_user_id"] = requester.get("hf_user_id")

    tb = base_row["_transitioned_by"]
    if tb:
        out["resolved_by_role"] = tb.get("role")
        if owner:
            out["resolved_by_login"] = tb.get("login_at_time")
    return out
