"""Regression coverage for stale sample source-schema labels."""

from __future__ import annotations

import sqlite3

from services.db import migrate


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
