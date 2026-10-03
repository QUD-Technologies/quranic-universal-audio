"""Per-user notification rows for the Dashboard "My Notifications" rail.

One row per (target user, source event), materialized at emit time by
``services.notifications`` (lifecycle transitions + segment-flag replies). Each
row is dismissable; dismissing sets ``dismissed_at`` (the row moves to the
archive but is retained + restorable). Dedup is on ``(hf_user_id, source_key)``
via ``INSERT OR IGNORE`` — a re-driven transaction or retried save can't
double-insert.

The caller owns the transaction: writes assume an active ``transaction()``
(the route's ``durable_transaction`` or the emit hook's live connection). Reads
use ``get_conn()`` directly. Mirrors ``repo_guides`` conventions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import _serde, repo_access
from .connection import get_conn

# Keep at most this many DISMISSED rows per user; oldest beyond it are pruned on
# each ``create``. Active (un-dismissed) rows are never auto-pruned.
_KEEP_ARCHIVED = 200

_COLS = "id, hf_user_id, event, slug, title, body, payload, source_key, created_at, seen_at, dismissed_at"


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "event": row["event"],
        "slug": row["slug"],
        "title": row["title"],
        "body": row["body"],
        "payload": _serde.json_loads(row["payload"]),
        "created_at": row["created_at"],
        "seen_at": row["seen_at"],
        "dismissed_at": row["dismissed_at"],
    }


def create(
    *,
    hf_user_id: str,
    event: str,
    slug: str | None,
    title: str,
    body: str | None,
    payload: dict[str, Any] | None,
    source_key: str,
    at: datetime | None = None,
) -> int | None:
    """Insert one notification (idempotent on ``(hf_user_id, source_key)``).

    Returns the new rowid, or ``None`` when the row already existed (dedup).
    ``ensure_user`` keeps the FK valid for a target whose ``users`` row hasn't
    been created yet. Prunes the user's archived backlog opportunistically.
    """
    repo_access.ensure_user(hf_user_id)
    conn = get_conn()
    cur = conn.execute(
        "INSERT OR IGNORE INTO notifications"
        "(hf_user_id, event, slug, title, body, payload, source_key, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            hf_user_id,
            event,
            slug,
            title,
            body,
            _serde.json_dumps(payload) if payload else None,
            source_key,
            _serde.to_iso(at or _serde.now()),
        ),
    )
    if cur.rowcount == 0:
        return None
    prune_for_user(hf_user_id)
    return cur.lastrowid


def list_active(hf_user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Newest-first active (un-dismissed) notifications for a user."""
    rows = (
        get_conn()
        .execute(
            f"SELECT {_COLS} FROM notifications "
            "WHERE hf_user_id = ? AND dismissed_at IS NULL "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (hf_user_id, limit),
        )
        .fetchall()
    )
    return [_row_to_dict(r) for r in rows]


def list_archived(hf_user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Most-recently-dismissed-first archived notifications for a user."""
    rows = (
        get_conn()
        .execute(
            f"SELECT {_COLS} FROM notifications "
            "WHERE hf_user_id = ? AND dismissed_at IS NOT NULL "
            "ORDER BY dismissed_at DESC, id DESC LIMIT ?",
            (hf_user_id, limit),
        )
        .fetchall()
    )
    return [_row_to_dict(r) for r in rows]


def dismiss(notification_id: int, hf_user_id: str, *, at: datetime | None = None) -> bool:
    """Archive one active notification owned by this user. Returns False if the
    id doesn't exist, isn't theirs, or was already dismissed."""
    cur = get_conn().execute(
        "UPDATE notifications SET dismissed_at = ? "
        "WHERE id = ? AND hf_user_id = ? AND dismissed_at IS NULL",
        (_serde.to_iso(at or _serde.now()), notification_id, hf_user_id),
    )
    return cur.rowcount > 0


def dismiss_by_source_key(source_key: str, *, at: datetime | None = None) -> int:
    """Archive every active notification sharing ``source_key`` — across ALL
    users (a fan-out writes one row per recipient under the same source_key, so
    one call clears the whole set). Returns the number archived. Used for
    auto-dismissing a request alert once its reciter reaches review."""
    cur = get_conn().execute(
        "UPDATE notifications SET dismissed_at = ? WHERE source_key = ? AND dismissed_at IS NULL",
        (_serde.to_iso(at or _serde.now()), source_key),
    )
    return cur.rowcount


def active_source_keys(event: str) -> list[tuple[str, str | None]]:
    """Distinct ``(source_key, slug)`` of every active notification for ``event``,
    across all users — what an emitter needs to archive the ones it has resolved."""
    rows = (
        get_conn()
        .execute(
            "SELECT DISTINCT source_key, slug FROM notifications "
            "WHERE event = ? AND dismissed_at IS NULL",
            (event,),
        )
        .fetchall()
    )
    return [(r[0], r[1]) for r in rows]


def restore(notification_id: int, hf_user_id: str) -> bool:
    """Move one archived notification back to active. Returns False if the id
    doesn't exist, isn't theirs, or wasn't dismissed."""
    cur = get_conn().execute(
        "UPDATE notifications SET dismissed_at = NULL "
        "WHERE id = ? AND hf_user_id = ? AND dismissed_at IS NOT NULL",
        (notification_id, hf_user_id),
    )
    return cur.rowcount > 0


def mark_seen(hf_user_id: str, *, at: datetime | None = None) -> None:
    """Stamp ``seen_at`` on the user's active, unseen notifications (clears the
    unread badge once the rail is opened)."""
    get_conn().execute(
        "UPDATE notifications SET seen_at = ? "
        "WHERE hf_user_id = ? AND dismissed_at IS NULL AND seen_at IS NULL",
        (_serde.to_iso(at or _serde.now()), hf_user_id),
    )


def unread_count(hf_user_id: str) -> int:
    """Count of active, unseen notifications for the badge."""
    row = (
        get_conn()
        .execute(
            "SELECT COUNT(*) FROM notifications "
            "WHERE hf_user_id = ? AND dismissed_at IS NULL AND seen_at IS NULL",
            (hf_user_id,),
        )
        .fetchone()
    )
    return int(row[0]) if row else 0


def prune_for_user(hf_user_id: str, *, keep: int = _KEEP_ARCHIVED) -> int:
    """Delete the user's oldest DISMISSED rows beyond ``keep``. Active rows are
    untouched. Returns the number deleted."""
    cur = get_conn().execute(
        "DELETE FROM notifications WHERE id IN ("
        "  SELECT id FROM notifications "
        "  WHERE hf_user_id = ? AND dismissed_at IS NOT NULL "
        "  ORDER BY dismissed_at DESC, id DESC LIMIT -1 OFFSET ?"
        ")",
        (hf_user_id, keep),
    )
    return cur.rowcount
