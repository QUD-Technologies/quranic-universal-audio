"""Repository for ``delivery_states`` + the assembled ``ReciterRow`` read DTO.

Storage is normalized (assignee/marked_ready live in ``claims``) but the read
model is the *unchanged* ``ReciterRow`` pydantic class: ``get_row``/``all_rows``
LEFT JOIN the open claim and fill ``assignee_*`` + ``marked_ready`` from it.
This keeps ``predicates``, ``/api/me``, ``/api/reciter-task`` and
``public_state`` reading the same shape they read today, and guarantees
``row_to_dict``'s ``model_dump`` stays byte-identical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from qua_shared.schemas import QualityStatus, ReciterRow, ReciterState, Visibility
from qua_shared.schemas.config.state import RevisionContext

from . import _serde
from .connection import get_conn

# delivery_states columns that callers may write, mapped to a serializer.
_WRITABLE: dict[str, Any] = {
    "state": lambda v: v.value if hasattr(v, "value") else v,
    "state_since": _serde.to_iso,
    "visibility": lambda v: v.value if hasattr(v, "value") else v,
    "visibility_reason": lambda v: v,
    "last_save_at": _serde.to_iso,
    # Set by job-completion bookkeeping (no lifecycle transition); read directly
    # by the Reviews-tab unread queries. Deliberately NOT surfaced on ReciterRow
    # (_assemble reads named columns only), so no schema/codegen change.
    "last_job_finished_at": _serde.to_iso,
    "created_by_transition_id": lambda v: v,
    "timestamps_job_ids": lambda v: _serde.json_dumps(v or []),
    "revision_in_progress": lambda v: (
        None
        if v is None
        else _serde.json_dumps(v.model_dump(mode="json") if hasattr(v, "model_dump") else v)
    ),
    "quality_status": lambda v: v.value if hasattr(v, "value") else v,
}


def _assemble(ds, claim) -> ReciterRow:
    state = ReciterState(ds["state"])
    rip_raw = _serde.json_loads(ds["revision_in_progress"])
    kwargs: dict[str, Any] = {
        "slug": ds["slug"],
        "state": state,
        "state_since": _serde.from_iso(ds["state_since"]),
        "visibility": Visibility(ds["visibility"]),
        "visibility_reason": ds["visibility_reason"],
        "last_save_at": _serde.from_iso(ds["last_save_at"]),
        "timestamps_job_ids": _serde.json_loads(ds["timestamps_job_ids"]) or [],
        "revision_in_progress": (RevisionContext.model_validate(rip_raw) if rip_raw else None),
        "quality_status": (QualityStatus(ds["quality_status"]) if ds["quality_status"] else None),
    }
    # assignee/marked_ready come from the open claim, and only exist on
    # under_review rows (ReciterRow's own invariant).
    if state == ReciterState.UNDER_REVIEW and claim is not None:
        kwargs["assignee_hf_id"] = claim["assignee_id"]
        kwargs["assignee_login"] = claim["assignee_login_snapshot"]
        kwargs["assignee_since"] = _serde.from_iso(claim["claimed_at"])
        kwargs["marked_ready"] = claim["marked_ready_at"] is not None
    return ReciterRow(**kwargs)


_SELECT = (
    "SELECT ds.*, "
    "  c.assignee_id, c.assignee_login_snapshot, c.claimed_at, c.marked_ready_at "
    "FROM delivery_states ds "
    "LEFT JOIN claims c ON c.slug = ds.slug AND c.released_at IS NULL "
)


def _split(row):
    """Return (delivery_states-ish mapping, claim-ish mapping|None) from a joined row."""
    claim = None
    if row["assignee_id"] is not None:
        claim = {
            "assignee_id": row["assignee_id"],
            "assignee_login_snapshot": row["assignee_login_snapshot"],
            "claimed_at": row["claimed_at"],
            "marked_ready_at": row["marked_ready_at"],
        }
    return row, claim


def get_row(slug: str) -> ReciterRow | None:
    row = get_conn().execute(_SELECT + "WHERE ds.slug = ?", (slug,)).fetchone()
    if row is None:
        return None
    ds, claim = _split(row)
    return _assemble(ds, claim)


def all_rows() -> list[ReciterRow]:
    rows = get_conn().execute(_SELECT + "ORDER BY ds.slug").fetchall()
    out: list[ReciterRow] = []
    for row in rows:
        ds, claim = _split(row)
        out.append(_assemble(ds, claim))
    return out


def exists(slug: str) -> bool:
    return (
        get_conn().execute("SELECT 1 FROM delivery_states WHERE slug = ?", (slug,)).fetchone()
        is not None
    )


def upsert_state(
    slug: str,
    *,
    state,
    state_since: datetime,
    visibility=Visibility.PUBLIC,
    visibility_reason: str | None = None,
    last_save_at: datetime | None = None,
    created_by_transition_id: str | None = None,
    timestamps_job_ids: list[str] | None = None,
    revision_in_progress=None,
) -> None:
    """Insert or replace the full delivery_states row for ``slug``."""
    get_conn().execute(
        "INSERT INTO delivery_states(slug, state, state_since, visibility, "
        "visibility_reason, last_save_at, created_by_transition_id, "
        "timestamps_job_ids, revision_in_progress) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(slug) DO UPDATE SET "
        "  state=excluded.state, state_since=excluded.state_since, "
        "  visibility=excluded.visibility, visibility_reason=excluded.visibility_reason, "
        "  last_save_at=excluded.last_save_at, "
        "  created_by_transition_id=excluded.created_by_transition_id, "
        "  timestamps_job_ids=excluded.timestamps_job_ids, "
        "  revision_in_progress=excluded.revision_in_progress",
        (
            slug,
            _WRITABLE["state"](state),
            _serde.to_iso(state_since),
            _WRITABLE["visibility"](visibility),
            visibility_reason,
            _serde.to_iso(last_save_at),
            created_by_transition_id,
            _serde.json_dumps(timestamps_job_ids or []),
            _WRITABLE["revision_in_progress"](revision_in_progress),
        ),
    )


def update_state(slug: str, **fields) -> None:
    """Partial update of named delivery_states columns (handlers' workhorse)."""
    if not fields:
        return
    cols, params = [], []
    for key, val in fields.items():
        if key not in _WRITABLE:
            raise KeyError(f"not a writable delivery_states column: {key!r}")
        cols.append(f"{key} = ?")
        params.append(_WRITABLE[key](val))
    params.append(slug)
    get_conn().execute(f"UPDATE delivery_states SET {', '.join(cols)} WHERE slug = ?", params)
