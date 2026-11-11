"""Repository for the ``transitions`` table — the canonical event log.

Replaces the append-only ``audit/<YYYY>-<MM>.jsonl`` store. Every state
mutation, claim, request, catalog and access change appends exactly one row
here, inside the caller's transaction (``append`` enrolls in the active txn
via the re-entrant ``transaction()`` CM, so it is atomic with the state write
that motivated it).

Read helpers reconstruct records in the same shape the activity layer expects
(``ts``/``event``/``slug``/``actor``/``result`` + ``content_hash``) so the
existing ``activity_classification`` code keeps working unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from qua_shared.schemas import Actor, AuditRecord

from . import _serde, repo_access
from .connection import get_conn, transaction


def append(
    *,
    event: str,
    actor: Actor,
    slug: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
    reason: str | None = None,
    result: str = "ok",
) -> AuditRecord:
    """Insert one transition row and return the equivalent ``AuditRecord``.

    Enrolls in the active write transaction (re-entrant): if the caller is
    already inside ``transaction()`` this is a SAVEPOINT-scoped insert that
    commits with the outer unit; otherwise it opens its own short txn.
    """
    ts = _serde.now()
    ts_iso = _serde.to_iso(ts)
    assert ts_iso is not None  # now() is never None, so to_iso never returns None
    tid = request_id or _serde.new_transition_id()
    role_val = actor.role.value if hasattr(actor.role, "value") else str(actor.role)
    ch = _serde.content_hash(
        ts=ts_iso, event=event, slug=slug, actor_hf=actor.hf_user_id, result=result
    )
    with transaction() as conn:
        # Guarantee the actor_id FK target exists (every transition references
        # it) — inside the txn so it uses the writer, not a read-only reader.
        # Do NOT pass login here: actor.login_at_time is a cookie snapshot that
        # may be stale, and refreshing users.login_cache on every transition
        # would clobber a fresher cache (login refresh belongs to the auth
        # callback / access.update). The transition's own actor_login_snapshot
        # column already records the snapshot login for the audit trail.
        repo_access.ensure_user(actor.hf_user_id)
        conn.execute(
            "INSERT INTO transitions(id, ts, slug, event, from_state, to_state, "
            "actor_id, actor_login_snapshot, actor_role_snapshot, reason, result, "
            "payload, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                ts_iso,
                slug,
                event,
                from_state,
                to_state,
                actor.hf_user_id,
                actor.login_at_time,
                role_val,
                reason,
                result,
                _serde.json_dumps(payload or {}),
                ch,
            ),
        )
    return AuditRecord(
        ts=ts,
        event=event,
        slug=slug,
        from_state=from_state,
        to_state=to_state,
        actor=actor,
        payload=payload or {},
        request_id=tid,
        reason=reason,
        result=result,  # type: ignore[arg-type]
    )


def _row_to_record(row) -> dict:
    """Reconstruct an audit-record-shaped dict.

    ``ts`` is the exact stored string, so ``activity_classification.audit_id``
    on this dict recomputes the same value stored in ``content_hash``.
    """
    return {
        "id": row["id"],
        "seq": row["seq"],
        "ts": row["ts"],
        "slug": row["slug"],
        "event": row["event"],
        "from_state": row["from_state"],
        "to_state": row["to_state"],
        "actor": {
            "hf_user_id": row["actor_id"],
            "login_at_time": row["actor_login_snapshot"],
            "role": row["actor_role_snapshot"],
        },
        "reason": row["reason"],
        "result": row["result"],
        "payload": _serde.json_loads(row["payload"]) or {},
        "content_hash": row["content_hash"],
    }


_COLS = (
    "seq, id, ts, slug, event, from_state, to_state, actor_id, "
    "actor_login_snapshot, actor_role_snapshot, reason, result, payload, content_hash"
)


def get(tid: str) -> dict | None:
    row = get_conn().execute(f"SELECT {_COLS} FROM transitions WHERE id = ?", (tid,)).fetchone()
    return _row_to_record(row) if row else None


def get_by_content_hash(ch: str) -> dict | None:
    row = (
        get_conn()
        .execute(
            f"SELECT {_COLS} FROM transitions WHERE content_hash = ? ORDER BY seq DESC LIMIT 1",
            (ch,),
        )
        .fetchone()
    )
    return _row_to_record(row) if row else None


#: Transitions that mean "alignment finished": the pipeline's completion event,
#: plus an owner force-move straight out of awaiting_alignment. Re-entries into
#: awaiting_review from a released claim or a revision unlock are NOT alignments.
_ALIGNED_SQL = (
    "SELECT slug, MAX(ts) AS ts FROM transitions "
    "WHERE to_state = 'awaiting_review' AND slug IS NOT NULL AND ("
    "event = 'reciter.alignment_completed' "
    "OR (event = 'admin.force_set_state' AND from_state = 'awaiting_alignment')) "
    "GROUP BY slug"
)


def aligned_at_by_slug() -> dict[str, str]:
    """``{slug: iso}`` — when each delivery last finished alignment."""
    return {r["slug"]: r["ts"] for r in get_conn().execute(_ALIGNED_SQL).fetchall()}


def for_slug(slug: str) -> list[dict]:
    """Chronological timeline for one delivery."""
    rows = (
        get_conn()
        .execute(f"SELECT {_COLS} FROM transitions WHERE slug = ? ORDER BY seq", (slug,))
        .fetchall()
    )
    return [_row_to_record(r) for r in rows]


def since(cutoff_iso: str, *, limit: int | None = None) -> Iterator[dict]:
    """Yield transition records with ``ts >= cutoff_iso``, newest-first.

    Backs the activity feeds (which read a rolling time window and classify in
    Python). ``ts`` is an ISO-8601 string in the canonical stored form, so a
    lexical ``>=`` is also chronological."""
    sql = f"SELECT {_COLS} FROM transitions WHERE ts >= ? ORDER BY seq DESC"
    params: list[Any] = [cutoff_iso]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    for row in get_conn().execute(sql, params).fetchall():
        yield _row_to_record(row)


def feed(
    *,
    events: Iterable[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Iterator[dict]:
    """Most-recent-first feed, optionally filtered to an event-kind set.

    ``events`` is a fixed app-supplied allow-list (never request input), so the
    ``IN (...)`` placeholders are generated, not interpolated.
    """
    sql = f"SELECT {_COLS} FROM transitions"
    params: list[Any] = []
    if events is not None:
        ev = list(events)
        if not ev:
            return
        sql += " WHERE event IN (" + ",".join("?" * len(ev)) + ")"
        params.extend(ev)
    sql += " ORDER BY seq DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    for row in get_conn().execute(sql, params).fetchall():
        yield _row_to_record(row)
