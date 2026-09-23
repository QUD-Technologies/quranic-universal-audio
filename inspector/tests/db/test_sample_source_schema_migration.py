"""Regression coverage for stale sample source-schema labels."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from services import db
from services.db import migrate
from services.db import sync


def test_normalizes_legacy_sample_labels_without_touching_concrete_schemas(tmp_path):
    conn = sqlite3.connect(tmp_path / "samples-v31.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE samples (
            id TEXT PRIMARY KEY,
            source_schema TEXT NOT NULL
        );
        INSERT INTO samples (id, source_schema) VALUES
            ('old-bare-alignment', 'legacy'),
            ('new-bare-alignment', 'alignment'),
            ('wrapped-alignment', 'alignment_resource');
        PRAGMA user_version = 31;
        """
    )

    assert migrate.run_migrations(conn) == 32
    assert dict(conn.execute("SELECT id, source_schema FROM samples").fetchall()) == {
        "old-bare-alignment": "alignment",
        "new-bare-alignment": "alignment",
        "wrapped-alignment": "alignment_resource",
    }


def test_init_db_uploads_a_new_schema_version(monkeypatch):
    writer = object()
    uploads: list[bool] = []

    @contextmanager
    def durable_transaction():
        uploads.append(True)
        yield writer

    monkeypatch.setattr(db, "get_writer", lambda: writer)
    monkeypatch.setattr(db, "current_version", lambda conn: 31)
    monkeypatch.setattr(db, "run_migrations", lambda conn: 32)
    monkeypatch.setattr(db, "_chmod_600", lambda path: None)
    monkeypatch.setattr(db, "db_path", lambda: "unused.db")
    monkeypatch.setattr(sync, "is_sync_enabled", lambda: True)
    monkeypatch.setattr(sync, "durable_transaction", durable_transaction)

    assert db.init_db(persist_migrations=True) == 32
    assert uploads == [True]


def test_init_db_does_not_upload_without_an_upgrade(monkeypatch):
    writer = object()
    uploads: list[bool] = []

    @contextmanager
    def durable_transaction():
        uploads.append(True)
        yield writer

    monkeypatch.setattr(db, "get_writer", lambda: writer)
    monkeypatch.setattr(db, "current_version", lambda conn: 32)
    monkeypatch.setattr(db, "run_migrations", lambda conn: 32)
    monkeypatch.setattr(db, "_chmod_600", lambda path: None)
    monkeypatch.setattr(db, "db_path", lambda: "unused.db")
    monkeypatch.setattr(sync, "is_sync_enabled", lambda: True)
    monkeypatch.setattr(sync, "durable_transaction", durable_transaction)

    assert db.init_db(persist_migrations=True) == 32
    assert uploads == []
