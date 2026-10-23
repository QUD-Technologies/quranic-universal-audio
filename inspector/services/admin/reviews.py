"""Admin Reviews-tab read model (Flask-free).

Single whole-table JOIN over ``delivery_states`` + ``deliveries`` + ``reciters``
+ open ``claims``, filtered to the two states the slimmed Reviews tab covers
(``awaiting_review``, ``under_review``). The FE renders "Under review" (claimed,
not yet marked ready) + "Available for review". Marked-ready + released
recitations moved to the Releases tab.

No caching today — the row count is small (a few hundred), the query is one
JOIN, and the data refreshes after every admin action. If profiling shows we
need to amortize across concurrent maintainers, mirror the ``db_seq`` cache
pattern from ``services.admin.users``.
"""

from __future__ import annotations

from qua_shared.schemas import (
    AdminReviewClaimHistoryEntry,
    AdminReviewDetail,
    AdminReviewOpenClaim,
    AdminReviewRow,
    AdminReviewsResponse,
    AdminReviewTransition,
    MarkReadySubmission,
    ReciterState,
)
from services.db import _serde
from services.db.connection import get_conn

# States the slimmed Reviews tab covers.
_BUCKET_STATES: tuple[str, ...] = (
    ReciterState.AWAITING_REVIEW.value,
    ReciterState.UNDER_REVIEW.value,
)

# Checklist keys a retired validation category left behind in old claims rows.
# ``MarkReadyChecklist`` is ``extra="forbid"``, so they are dropped on read.
_RETIRED_CHECKLIST_KEYS: tuple[str, ...] = ("basmala_amin_intros",)


_SQL = (
    "SELECT "
    "  ds.slug, ds.state, ds.state_since, "
    "  d.reciter_id, d.riwayah, d.style, d.channel, "
    "  r.name_ar, r.name_en, "
    "  c.assignee_id, c.assignee_login_snapshot, c.claimed_at, c.marked_ready_at "
    "FROM delivery_states ds "
    "JOIN deliveries d ON d.slug = ds.slug "
    "JOIN reciters r ON r.reciter_id = d.reciter_id "
    "LEFT JOIN claims c ON c.slug = ds.slug AND c.released_at IS NULL "
    f"WHERE ds.state IN ({','.join(['?'] * len(_BUCKET_STATES))}) "
    "AND (? OR d.channel <> 'everyayah') "
    "ORDER BY ds.slug"
)


def _build_submission(row) -> MarkReadySubmission | None:
    """Assemble a ``MarkReadySubmission`` from a claims row, or None.

    Returns None when the row pre-dates the mark-ready form (legacy claims
    with ``marked_ready_at`` set but neither a checklist NOR a bypass flag)
    or hasn't been marked at all. The list-row helper skips reading the
    submission; the drawer detail uses this directly.

    Bypass submissions store ``mark_ready_checklist = NULL`` +
    ``mark_ready_bypass_used = 1``; the returned object carries
    ``checklist=None`` + ``bypass_used=True`` so the admin Reviews drawer
    can render the bypass pill in lieu of the checklist list.
    """
    keys = row.keys()
    raw_checklist = row["mark_ready_checklist"] if "mark_ready_checklist" in keys else None
    bypass = bool(row["mark_ready_bypass_used"]) if "mark_ready_bypass_used" in keys else False

    if not raw_checklist and not bypass:
        return None

    checklist = _serde.json_loads(raw_checklist) if raw_checklist else None
    if checklist:
        for key in _RETIRED_CHECKLIST_KEYS:
            checklist.pop(key, None)
    return MarkReadySubmission(
        checklist=checklist,
        comment_checks=row["mark_ready_comment_checks"] or "",
        comment_issues=row["mark_ready_comment_issues"] or "",
        bypass_used=bypass,
    )


def list_reviews(*, include_everyayah: bool = False) -> dict:
    """Assembled Reviews-tab payload (``AdminReviewsResponse`` dump)."""
    rows: list[AdminReviewRow] = []
    params = (*_BUCKET_STATES, 1 if include_everyayah else 0)
    for r in get_conn().execute(_SQL, params).fetchall():
        open_claim: AdminReviewOpenClaim | None = None
        if r["assignee_id"] is not None:
            open_claim = AdminReviewOpenClaim(
                assignee_id=r["assignee_id"],
                login=r["assignee_login_snapshot"],
                claimed_at=r["claimed_at"],
                marked_ready_at=r["marked_ready_at"],
            )

        rows.append(
            AdminReviewRow(
                slug=r["slug"],
                state=r["state"],
                state_since=r["state_since"],
                reciter_id=r["reciter_id"],
                name_ar=r["name_ar"],
                name_en=r["name_en"],
                riwayah=r["riwayah"],
                style=r["style"],
                channel=r["channel"],
                open_claim=open_claim,
            )
        )

    return AdminReviewsResponse(rows=rows).model_dump(mode="json")


# ---- detail drawer ----


def get_review_detail(slug: str, *, include_everyayah: bool = False) -> dict | None:
    """Per-slug payload for the General drawer.

    Returns ``None`` when the slug isn't in ``delivery_states`` (caller maps
    to 404). Three small bounded queries: base + open claim + closed claims +
    transitions. Cheap relative to ``validate_reciter_segments`` so it's safe
    to fetch eagerly on every drawer open.
    """
    conn = get_conn()

    base = conn.execute(
        "SELECT ds.slug, ds.state, ds.state_since, ds.timestamps_job_ids, "
        "  d.reciter_id, d.riwayah, d.style, d.channel, "
        "  r.name_ar, r.name_en "
        "FROM delivery_states ds "
        "JOIN deliveries d ON d.slug = ds.slug "
        "JOIN reciters r ON r.reciter_id = d.reciter_id "
        "WHERE ds.slug = ? AND (? OR d.channel <> 'everyayah')",
        (slug, 1 if include_everyayah else 0),
    ).fetchone()
    if base is None:
        return None

    current_row = conn.execute(
        "SELECT assignee_id, assignee_login_snapshot, claimed_at, marked_ready_at, "
        "  mark_ready_checklist, mark_ready_comment_checks, mark_ready_comment_issues, "
        "  mark_ready_bypass_used "
        "FROM claims WHERE slug = ? AND released_at IS NULL",
        (slug,),
    ).fetchone()
    current_claim: AdminReviewOpenClaim | None = None
    if current_row is not None:
        current_claim = AdminReviewOpenClaim(
            assignee_id=current_row["assignee_id"],
            login=current_row["assignee_login_snapshot"],
            claimed_at=current_row["claimed_at"],
            marked_ready_at=current_row["marked_ready_at"],
            mark_ready_submission=_build_submission(current_row),
        )

    history_rows = conn.execute(
        "SELECT assignee_id, assignee_login_snapshot, claimed_at, released_at, "
        "  marked_ready_at, close_reason, "
        "  mark_ready_checklist, mark_ready_comment_checks, mark_ready_comment_issues, "
        "  mark_ready_bypass_used "
        "FROM claims WHERE slug = ? AND released_at IS NOT NULL "
        "ORDER BY claimed_at DESC",
        (slug,),
    ).fetchall()
    claim_history = [
        AdminReviewClaimHistoryEntry(
            assignee_id=r["assignee_id"],
            login=r["assignee_login_snapshot"],
            claimed_at=r["claimed_at"],
            released_at=r["released_at"],
            marked_ready_at=r["marked_ready_at"],
            close_reason=r["close_reason"],
            mark_ready_submission=_build_submission(r),
        )
        for r in history_rows
    ]

    transition_rows = conn.execute(
        "SELECT ts, event, from_state, to_state, actor_login_snapshot, "
        "  actor_role_snapshot, reason "
        "FROM transitions WHERE slug = ? "
        "ORDER BY seq DESC",
        (slug,),
    ).fetchall()
    transitions = [
        AdminReviewTransition(
            ts=r["ts"],
            event=r["event"],
            from_state=r["from_state"],
            to_state=r["to_state"],
            actor_login=r["actor_login_snapshot"],
            actor_role=r["actor_role_snapshot"],
            reason=r["reason"],
        )
        for r in transition_rows
    ]

    job_ids = _serde.json_loads(base["timestamps_job_ids"]) or []

    # Flagged-issue count from detailed.json (one cached read on drawer open).
    # Best-effort: a bucket read failure must not break the DB-backed drawer
    # detail — the count is non-critical metadata, default to 0 on error.
    from services.segments.flags import count_flagged
    from services.storage.data_loader import load_detailed

    try:
        flagged_count = count_flagged(load_detailed(slug) or [])
    except Exception:  # noqa: BLE001 — count is best-effort; never fail the detail
        import logging

        logging.getLogger(__name__).warning(
            "[%s] flagged-issue count read failed; defaulting to 0", slug, exc_info=True
        )
        flagged_count = 0

    return AdminReviewDetail(
        slug=base["slug"],
        state=base["state"],
        state_since=base["state_since"],
        reciter_id=base["reciter_id"],
        name_ar=base["name_ar"],
        name_en=base["name_en"],
        riwayah=base["riwayah"],
        style=base["style"],
        channel=base["channel"],
        current_claim=current_claim,
        claim_history=claim_history,
        transitions=transitions,
        timestamps_job_ids=job_ids,
        flagged_issues_count=flagged_count,
    ).model_dump(mode="json")
