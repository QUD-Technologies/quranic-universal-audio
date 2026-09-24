"""Repository for ``align_runs`` — the native align pipeline's durable rows.

Writes require the caller's active transaction (``durable_transaction``). Reads
run on the shared connection. Rows are plain dicts; ``services/admin/
align_pipeline/runs.py`` projects them onto the wire model.
"""

from __future__ import annotations

from datetime import UTC, datetime

from . import _serde
from .connection import get_conn

ACTIVE_STATUSES = ("pending", "running", "failed")


def _now() -> str:
    return _serde.to_iso(datetime.now(UTC)) or ""


def get(run_id: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM align_runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def active_for_slug(slug: str) -> dict | None:
    """The one pending/running/failed run for ``slug``, if any."""
    row = (
        get_conn()
        .execute(
            "SELECT * FROM align_runs WHERE slug = ? AND status IN ('pending','running','failed')",
            (slug,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def latest_for_slug(slug: str) -> dict | None:
    row = (
        get_conn()
        .execute(
            "SELECT * FROM align_runs WHERE slug = ? ORDER BY started_at DESC LIMIT 1",
            (slug,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def list_active() -> list[dict]:
    rows = (
        get_conn()
        .execute(
            "SELECT * FROM align_runs WHERE status IN ('pending','running','failed') "
            "ORDER BY started_at"
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def insert(
    *, run_id: str, slug: str, requested_by: str, params_json: str, chapters_total: int
) -> None:
    now = _now()
    get_conn().execute(
        "INSERT INTO align_runs (run_id, slug, stage, status, requested_by, params_json, "
        "acquire_job_id, attempt, chapters_total, last_error, started_at, updated_at, ended_at) "
        "VALUES (?, ?, 'acquire', 'pending', ?, ?, NULL, 1, ?, NULL, ?, ?, NULL)",
        (run_id, slug, requested_by, params_json, chapters_total, now, now),
    )


def update(run_id: str, **fields) -> None:
    """Set the given columns (+ ``updated_at``). Terminal statuses stamp ``ended_at``."""
    if not fields:
        return
    fields["updated_at"] = _now()
    if fields.get("status") in ("succeeded", "failed", "canceled"):
        fields.setdefault("ended_at", fields["updated_at"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    get_conn().execute(
        f"UPDATE align_runs SET {cols} WHERE run_id = ?",  # noqa: S608 — column names are ours
        (*fields.values(), run_id),
    )


# ---------------------------------------------------------------------------
# Usage-limit reads (``align_pipeline/limits.py``). Exempt runs — started by a
# holder of the unlimited capability — never count against the shared budget.
# ---------------------------------------------------------------------------

_COUNTED = (
    "json_extract(params_json, '$.device') = ? "
    "AND COALESCE(json_extract(params_json, '$.quota_exempt'), 0) = 0"
)


def counted_starts_since(device: str, since_iso: str) -> list[str]:
    """``started_at`` of every non-exempt ``device`` run started at/after ``since_iso``, oldest first."""
    rows = (
        get_conn()
        .execute(
            f"SELECT started_at FROM align_runs WHERE {_COUNTED} AND started_at >= ? "  # noqa: S608
            "ORDER BY started_at",
            (device, since_iso),
        )
        .fetchall()
    )
    return [r["started_at"] for r in rows]


def counted_running(device: str, *, exclude_run_id: str | None = None) -> int:
    """Non-exempt ``device`` runs currently executing (pending/running — a failed run holds no lane)."""
    row = (
        get_conn()
        .execute(
            f"SELECT COUNT(*) AS n FROM align_runs WHERE {_COUNTED} "  # noqa: S608
            "AND status IN ('pending','running') AND run_id != ?",
            (device, exclude_run_id or ""),
        )
        .fetchone()
    )
    return int(row["n"])
