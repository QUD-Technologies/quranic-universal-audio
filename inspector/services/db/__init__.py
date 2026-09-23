"""SQLite substrate for the inspector backend.

Single container-local ``inspector.db`` is the source of truth for the
former 7 bucket JSON stores + audit log; uploaded full-file to the bucket
after each committed write. See ``connection`` (txn primitives), ``migrate``
(schema runner), ``sync`` (bucket pull/push), and ``repo_*`` (per-domain
read/write).
"""

from __future__ import annotations

from .connection import (
    _chmod_600,
    current_db_seq,
    db_path,
    get_conn,
    get_writer,
    reset,
    set_db_path_for_test,
    transaction,
)
from .migrate import current_version, run_migrations


def init_db(*, persist_migrations: bool = False) -> int:
    """Open the writer and apply migrations.

    Deployed boot passes ``persist_migrations=True`` so a pulled bucket DB is
    upgraded durably. Tests and offline tools keep the default and never gain
    an unexpected remote write merely by initializing a local database.
    """
    conn = get_writer()
    previous_version = current_version(conn)
    version = run_migrations(conn)
    # WAL/SHM sidecars only exist after the first write; tighten perms now.
    _chmod_600(db_path())
    if persist_migrations and version > previous_version:
        # Migrations write through sqlite directly rather than the service
        # transaction seam. Persist the upgraded snapshot now so a cold restart
        # does not pull and re-run a stale bucket DB. The read-only escape hatch
        # keeps local prod inspection fully disarmed.
        from . import sync

        if sync.is_sync_enabled():
            sync.upload()
    return version


def healthcheck() -> dict:
    """Cheap DB status for ``/healthz`` (never raises)."""
    try:
        return {"open": True, "schema_version": current_version(get_writer())}
    except Exception as e:  # noqa: BLE001
        return {"open": False, "error": str(e)[:200]}


__all__ = [
    "current_db_seq",
    "current_version",
    "db_path",
    "get_conn",
    "get_writer",
    "init_db",
    "reset",
    "run_migrations",
    "set_db_path_for_test",
    "transaction",
]
