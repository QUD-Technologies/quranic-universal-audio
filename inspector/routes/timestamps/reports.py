"""Public Timestamps-tab report routes (``/api/ts/<slug>/reports``).

The categorized, cell-addressable rework of the verse-flag routes. Any visitor —
including anonymous — files a typed report (audio / timing / tajweed /
phonemes / other) against a flexible target on the verse currently shown,
optionally with a comment. A report's targeted shard content is snapshotted
server-side at create time (the drift fingerprint). A new report fans a
notification out to review-alert recipients (owners by default). Owners resolve a
report (single terminal ``resolved`` outcome + optional comment), which notifies
the reporter.

Visibility: ``tajweed`` + ``phonemes`` flags are restricted to the reporter and
to holders of ``timestamps.view_nonpublic_reports`` (maintainer-default) —
non-holders never receive those rows or their counts (the repo filters in SQL).
Every other category (``timing`` grid flags + the verse-level audio/other
reports) stays public.

Gated by ``timestamps.report`` (anon-eligible, open by default). Resolve is gated
by ``timestamps.resolve_report`` (owner-default). Report-author identity in GET
responses is gated by ``timestamps.see_reporter_identity``; the stale-only filter
by ``timestamps.view_stale_reports``. Writes wrap ``sync.durable_transaction``;
mutations require a same-origin POST.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from qua_shared.schemas.wire.ts_reports import (
    TsReciterReports,
    TsReport,
    TsReportAuthor,
    TsReportBatchCreateRequest,
    TsReportBatchResult,
    TsReportCreateRequest,
    TsReportResolveRequest,
    TsReportSnapshot,
    TsReportTarget,
    TsReportVerseCount,
    TsVerseReports,
)
from services import auth as auth_service
from services.auth import capabilities as cap_service
from services.db import _serde, repo_ts_reports
from services.db import sync as _sync
from services.notifications import emit as _notify
from services.permissions import role_of
from services.state import state as state_service
from services.ts_reports import ts_target_snapshot
from utils.decorators import require_same_origin

from . import access

logger = logging.getLogger(__name__)

ts_reports_bp = Blueprint("ts_reports", __name__, url_prefix="/api/ts")
access.guard(ts_reports_bp)


def _validation_detail(e: ValidationError) -> list[dict]:
    """JSON-safe error summary (``e.errors()`` embeds raw ValueError ``ctx``)."""
    return [{"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in e.errors()]


_REPORT_CAP = "timestamps.report"
_RESOLVE_CAP = "timestamps.resolve_report"
_IDENTITY_CAP = "timestamps.see_reporter_identity"
_VIEW_STALE_CAP = "timestamps.view_stale_reports"
_VIEW_NONPUBLIC_CAP = "timestamps.view_nonpublic_reports"

#: Categories whose reports/notifications coalesce per word (vs one per cell).
_WORD_GROUPED = frozenset({"timing", "phonemes"})


def _snapshot_view(snap: dict) -> TsReportSnapshot | None:
    """Map the stored native fingerprint to its wire model."""
    if not snap:
        return None
    return TsReportSnapshot.model_validate(snap)


def _report_view(row: dict, *, mine: bool, show_author: bool) -> TsReport:
    author = None
    if show_author:
        author = TsReportAuthor(
            hf_user_id=row["hf_user_id"],
            login=row["login_at_time"],
            role=row["role_at_time"],
        )
    return TsReport(
        id=row["id"],
        verse_key=row["verse_key"],
        category=row["category"],
        subtype=row["subtype"],
        onset=row.get("onset"),
        offset=row.get("offset"),
        target=TsReportTarget(**row["target"]),
        snapshot=_snapshot_view(row["snapshot"]),
        comment=row["comment"],
        selected_rule_tags=row.get("selected_rule_tags") or [],
        status=row["status"],
        stale=row["stale"],
        resolver_comment=row["resolver_comment"],
        resolved_at=row["resolved_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        mine=mine,
        author=author,
    )


def _is_mine(row: dict, *, hf_user_id: str | None, anon_token: str | None) -> bool:
    if hf_user_id is not None:
        return row["hf_user_id"] == hf_user_id
    if anon_token:
        return row["anon_token"] == anon_token
    return False


@ts_reports_bp.route("/<slug>/reports", methods=["GET"])
def get_reciter_reports(slug: str):
    """Reported-verse pills + open/resolved counts for the accordion. Public read,
    but non-public tajweed/phoneme counts are visible only to the reporter or a
    ``view_nonpublic_reports`` holder."""
    try:
        user = auth_service.current_user()
        if not state_service.is_delivery_visible(slug, user):
            return jsonify({"error": "reciter not found"}), 404
        hf_user_id = user.hf_user_id if user is not None else None
        anon_token = request.args.get("anon_token") if user is None else None
        counts = repo_ts_reports.verse_counts(
            slug,
            hf_user_id=hf_user_id,
            anon_token=anon_token,
            can_view_nonpublic=cap_service.can(user, _VIEW_NONPUBLIC_CAP),
        )
        resp = TsReciterReports(reports=[TsReportVerseCount(**c) for c in counts])
        return jsonify(resp.model_dump(mode="json"))
    except Exception:  # noqa: BLE001
        logger.exception("ts_reports.get_reciter_reports failed for %s", slug)
        return jsonify({"error": "failed to load reports"}), 500


@ts_reports_bp.route("/<slug>/reports/<verse_key>", methods=["GET"])
def get_verse_reports(slug: str, verse_key: str):
    """All reports on a verse. Public read; author shown only to identity-capable
    callers. Non-public tajweed/phoneme reports are returned only to the reporter
    or a ``view_nonpublic_reports`` holder. ``?stale=1`` filters to stale reports
    (owner-gated)."""
    try:
        user = auth_service.current_user()
        if not state_service.is_delivery_visible(slug, user):
            return jsonify({"error": "reciter not found"}), 404
        show_author = cap_service.can(user, _IDENTITY_CAP)
        hf_user_id = user.hf_user_id if user is not None else None
        anon_token = request.args.get("anon_token") if user is None else None
        rows = repo_ts_reports.list_for_verse(
            slug,
            verse_key,
            hf_user_id=hf_user_id,
            anon_token=anon_token,
            can_view_nonpublic=cap_service.can(user, _VIEW_NONPUBLIC_CAP),
        )
        if request.args.get("stale") == "1":
            if not cap_service.can(user, _VIEW_STALE_CAP):
                return jsonify({"error": "not available"}), 403
            rows = [r for r in rows if r["stale"]]
        reports = [
            _report_view(
                r,
                mine=_is_mine(r, hf_user_id=hf_user_id, anon_token=anon_token),
                show_author=show_author,
            )
            for r in rows
        ]
        return jsonify(TsVerseReports(verse_key=verse_key, reports=reports).model_dump(mode="json"))
    except Exception:  # noqa: BLE001
        logger.exception("ts_reports.get_verse_reports failed for %s %s", slug, verse_key)
        return jsonify({"error": "failed to load reports"}), 500


@ts_reports_bp.route("/<slug>/reports", methods=["POST"])
@require_same_origin
def create_report(slug: str):
    """Create the caller's report on a verse. Anonymous allowed."""
    user = auth_service.current_user()
    if not cap_service.can(user, _REPORT_CAP):
        return jsonify({"error": "not available"}), 403
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "reciter not found"}), 404
    try:
        req = TsReportCreateRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return jsonify({"error": "invalid report", "detail": _validation_detail(e)}), 400

    if user is not None:
        hf_user_id: str | None = user.hf_user_id
        anon_token: str | None = None
        login_at_time: str | None = user.login
        role_at_time: str | None = role_of(user).value
    else:
        anon = (req.anon_token or "").strip()
        if not anon:
            return jsonify({"error": "anon_token required for anonymous reports"}), 400
        hf_user_id, anon_token = None, anon
        login_at_time = role_at_time = None

    target = req.target.model_dump()
    snapshot = ts_target_snapshot.build_snapshot(slug, req.verse_key, target)
    if snapshot is None:
        return jsonify({"error": "native report target does not resolve"}), 409

    try:
        with _sync.durable_transaction():
            row, created = repo_ts_reports.create(
                slug=slug,
                verse_key=req.verse_key,
                category=req.category,
                subtype=req.subtype,
                onset=req.onset,
                offset=req.offset,
                target=target,
                snapshot=snapshot,
                comment=req.comment,
                hf_user_id=hf_user_id,
                anon_token=anon_token,
                login_at_time=login_at_time,
                role_at_time=role_at_time,
                selected_rule_tags=req.selected_rule_tags,
            )
    except Exception:  # noqa: BLE001
        logger.exception("ts_reports.create_report failed for %s %s", slug, req.verse_key)
        return jsonify({"error": "failed to save report"}), 500

    if created:
        at_utc = _serde.to_iso(row["created_at"])
        if at_utc is not None:
            _notify.notify_owners_ts_report(
                slug=slug,
                verse_key=req.verse_key,
                category=req.category,
                author_id=hf_user_id,
                author_login=login_at_time,
                report_id=row["id"],
                at_utc=at_utc,
            )

    show_author = cap_service.can(user, _IDENTITY_CAP)
    return jsonify(_report_view(row, mine=True, show_author=show_author).model_dump(mode="json")), (
        201 if created else 200
    )


def _fan_batch_notifications(
    slug: str,
    verse_key: str,
    results: list[tuple[dict, bool]],
    author_id: str | None,
    author_login: str | None,
) -> None:
    """Owner notifications for a batch submit: one per NEW word-group
    (timing / phonemes), one per NEW tajweed cell. Re-submitted (``created=False``)
    rows never notify; multiple cells/phonemes in one word coalesce on the
    word-group key."""
    seen_word_groups: set[tuple[str, str, str]] = set()
    for row, created in results:
        if not created:
            continue
        at_utc = _serde.to_iso(row["created_at"])
        if at_utc is None:
            continue
        category = row["category"]
        if category in _WORD_GROUPED:
            native = (row.get("snapshot") or {}).get("native") or {}
            word_id = (
                row["target"]["target_id"]
                if row["target"]["kind"] == "word"
                else native.get("word_id")
            )
            if word_id is None:
                _notify.notify_owners_ts_report(
                    slug=slug,
                    verse_key=verse_key,
                    category=category,
                    author_id=author_id,
                    author_login=author_login,
                    report_id=row["id"],
                    at_utc=at_utc,
                )
                continue
            word_id = str(word_id)
            reading_id = row["target"]["reading_id"]
            group = (category, reading_id, word_id)
            if group in seen_word_groups:
                continue
            seen_word_groups.add(group)
            _notify.notify_owners_ts_report(
                slug=slug,
                verse_key=verse_key,
                category=category,
                author_id=author_id,
                author_login=author_login,
                report_id=row["id"],
                at_utc=at_utc,
                source_key=repo_ts_reports.word_group_key(
                    slug, verse_key, reading_id, word_id, category
                ),
            )
        else:
            _notify.notify_owners_ts_report(
                slug=slug,
                verse_key=verse_key,
                category=category,
                author_id=author_id,
                author_login=author_login,
                report_id=row["id"],
                at_utc=at_utc,
            )


@ts_reports_bp.route("/<slug>/reports/batch", methods=["POST"])
@require_same_origin
def create_reports_batch(slug: str):
    """Submit many staged cell-annotations on one verse in a single transaction.
    Notifications fire grouped: one per timing/phoneme word, one per tajweed cell."""
    user = auth_service.current_user()
    if not cap_service.can(user, _REPORT_CAP):
        return jsonify({"error": "not available"}), 403
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "reciter not found"}), 404
    try:
        req = TsReportBatchCreateRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return jsonify({"error": "invalid batch", "detail": _validation_detail(e)}), 400

    if user is not None:
        hf_user_id: str | None = user.hf_user_id
        anon_token: str | None = None
        login_at_time: str | None = user.login
        role_at_time: str | None = role_of(user).value
    else:
        anon = (req.anon_token or "").strip()
        if not anon:
            return jsonify({"error": "anon_token required for anonymous reports"}), 400
        hf_user_id, anon_token = None, anon
        login_at_time = role_at_time = None

    # Snapshots read shards — build them BEFORE opening the write transaction.
    items: list[dict] = []
    for it in req.items:
        target = it.target.model_dump()
        snapshot = ts_target_snapshot.build_snapshot(slug, req.verse_key, target)
        if snapshot is None:
            return jsonify(
                {"error": "native report target does not resolve", "target": target}
            ), 409
        items.append(
            {
                "category": it.category,
                "subtype": it.subtype,
                "onset": it.onset,
                "offset": it.offset,
                "target": target,
                "comment": it.comment,
                "selected_rule_tags": it.selected_rule_tags,
                "snapshot": snapshot,
            }
        )

    try:
        with _sync.durable_transaction():
            results = repo_ts_reports.create_many(
                slug=slug,
                verse_key=req.verse_key,
                items=items,
                hf_user_id=hf_user_id,
                anon_token=anon_token,
                login_at_time=login_at_time,
                role_at_time=role_at_time,
            )
    except Exception:  # noqa: BLE001
        logger.exception("ts_reports.create_reports_batch failed for %s %s", slug, req.verse_key)
        return jsonify({"error": "failed to save reports"}), 500

    _fan_batch_notifications(slug, req.verse_key, results, hf_user_id, login_at_time)

    show_author = cap_service.can(user, _IDENTITY_CAP)
    rows = [_report_view(r, mine=True, show_author=show_author) for r, _ in results]
    created_count = sum(1 for _, c in results if c)
    resp = TsReportBatchResult(
        verse_key=req.verse_key,
        reports=rows,
        created_count=created_count,
        updated_count=len(results) - created_count,
    )
    return jsonify(resp.model_dump(mode="json")), 201


@ts_reports_bp.route("/<slug>/reports/<int:report_id>/resolve", methods=["POST"])
@require_same_origin
def resolve_report(slug: str, report_id: int):
    """Mark a report resolved (owner-gated) and notify the reporter."""
    user = auth_service.current_user()
    if not cap_service.can(user, _RESOLVE_CAP):
        return jsonify({"error": "not available"}), 403
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "reciter not found"}), 404
    try:
        req = TsReportResolveRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return jsonify({"error": "invalid resolve", "detail": _validation_detail(e)}), 400

    try:
        with _sync.durable_transaction():
            row = repo_ts_reports.resolve(
                report_id=report_id,
                resolver_hf_user_id=user.hf_user_id if user is not None else None,
                resolver_login=user.login if user is not None else None,
                resolver_comment=req.comment,
            )
    except Exception:  # noqa: BLE001
        logger.exception("ts_reports.resolve_report failed for %s #%s", slug, report_id)
        return jsonify({"error": "failed to resolve report"}), 500

    if row is None:
        return jsonify({"error": "report not found or already resolved"}), 404

    reporter_id = row["hf_user_id"]
    if reporter_id is not None:
        at_utc = _serde.to_iso(row["resolved_at"])
        if at_utc is not None:
            _notify.notify_reporter_ts_report_resolved(
                reporter_id=reporter_id,
                slug=slug,
                verse_key=row["verse_key"],
                resolver_comment=req.comment,
                report_id=report_id,
                at_utc=at_utc,
            )

    show_author = cap_service.can(user, _IDENTITY_CAP)
    return jsonify(_report_view(row, mine=False, show_author=show_author).model_dump(mode="json"))


@ts_reports_bp.route(
    "/<slug>/reports/<verse_key>/reading/<reading_id>/word/<word_id>/<category>/resolve",
    methods=["POST"],
)
@require_same_origin
def resolve_word_group(slug: str, verse_key: str, reading_id: str, word_id: str, category: str):
    """Resolve a timing/phoneme word-group as a unit (owner-gated). Notifies each
    distinct signed-in reporter in the group once."""
    user = auth_service.current_user()
    if not cap_service.can(user, _RESOLVE_CAP):
        return jsonify({"error": "not available"}), 403
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "reciter not found"}), 404
    if category not in _WORD_GROUPED:
        return jsonify({"error": "group resolve is only for timing/phoneme word-groups"}), 400
    try:
        req = TsReportResolveRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return jsonify({"error": "invalid resolve", "detail": _validation_detail(e)}), 400

    try:
        with _sync.durable_transaction():
            rows = repo_ts_reports.resolve_group(
                slug=slug,
                verse_key=verse_key,
                reading_id=reading_id,
                word_id=word_id,
                category=category,
                resolver_hf_user_id=user.hf_user_id if user is not None else None,
                resolver_login=user.login if user is not None else None,
                resolver_comment=req.comment,
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "ts_reports.resolve_word_group failed for %s %s word %s", slug, verse_key, word_id
        )
        return jsonify({"error": "failed to resolve group"}), 500

    if not rows:
        return jsonify({"error": "no open reports in group"}), 404

    at_utc = _serde.to_iso(rows[0]["resolved_at"])
    if at_utc is not None:
        for reporter_id in {r["hf_user_id"] for r in rows if r["hf_user_id"]}:
            _notify.notify_reporter_ts_report_resolved(
                reporter_id=reporter_id,
                slug=slug,
                verse_key=verse_key,
                resolver_comment=req.comment,
                report_id=rows[0]["id"],
                at_utc=at_utc,
            )

    show_author = cap_service.can(user, _IDENTITY_CAP)
    return jsonify(
        TsVerseReports(
            verse_key=verse_key,
            reports=[_report_view(r, mine=False, show_author=show_author) for r in rows],
        ).model_dump(mode="json")
    )


@ts_reports_bp.route("/<slug>/reports/<int:report_id>", methods=["DELETE"])
@require_same_origin
def delete_report(slug: str, report_id: int):
    """Remove the caller's own report. Soft delete — the row drops out of every
    view but is retained internally; re-filing the same target un-hides it."""
    user = auth_service.current_user()
    if not cap_service.can(user, _REPORT_CAP):
        return jsonify({"error": "not available"}), 403
    if not state_service.is_delivery_visible(slug, user):
        return jsonify({"error": "reciter not found"}), 404
    if user is not None:
        hf_user_id: str | None = user.hf_user_id
        anon_token: str | None = None
    else:
        anon_token = (request.args.get("anon_token") or "").strip()
        if not anon_token:
            return jsonify({"error": "anon_token required"}), 400
        hf_user_id = None
    try:
        with _sync.durable_transaction():
            removed = repo_ts_reports.delete(
                report_id=report_id, hf_user_id=hf_user_id, anon_token=anon_token
            )
    except Exception:  # noqa: BLE001
        logger.exception("ts_reports.delete_report failed for %s #%s", slug, report_id)
        return jsonify({"error": "failed to delete report"}), 500
    return jsonify({"ok": removed})
