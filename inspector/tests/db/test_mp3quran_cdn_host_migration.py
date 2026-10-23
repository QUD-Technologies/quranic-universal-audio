"""0033: the mp3quran channel also matches its new ``cdn.mp3quran.net`` host."""

from __future__ import annotations

import json
import sqlite3

from services.db import migrate


def _db_at_v32(tmp_path, patterns: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "channels-v32.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE channels (slug TEXT PRIMARY KEY, short TEXT, name TEXT, "
        "host_patterns TEXT NOT NULL, gh_release_eligible INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute(
        "INSERT INTO channels (slug, short, name, host_patterns) VALUES (?, ?, ?, ?)",
        ("mp3quran", "mp3q", "MP3 Quran", json.dumps(patterns)),
    )
    conn.execute(
        "INSERT INTO channels (slug, short, name, host_patterns) VALUES (?, ?, ?, ?)",
        ("tvquran", "tvq", "TV Quran", json.dumps(["download.tvquran.com"])),
    )
    conn.execute("PRAGMA user_version = 32")
    return conn


def _patterns(conn: sqlite3.Connection, slug: str) -> list[str]:
    row = conn.execute("SELECT host_patterns FROM channels WHERE slug = ?", (slug,)).fetchone()
    return json.loads(row["host_patterns"])


def test_appends_the_cdn_host_and_keeps_the_legacy_pattern(tmp_path):
    conn = _db_at_v32(tmp_path, ["server*.mp3quran.net"])

    assert migrate.run_migrations(conn) == 33
    assert _patterns(conn, "mp3quran") == ["server*.mp3quran.net", "cdn.mp3quran.net"]
    assert _patterns(conn, "tvquran") == ["download.tvquran.com"]


def test_a_channel_that_already_lists_the_cdn_host_is_left_as_is(tmp_path):
    conn = _db_at_v32(tmp_path, ["cdn.mp3quran.net", "server*.mp3quran.net"])

    assert migrate.run_migrations(conn) == 33
    assert _patterns(conn, "mp3quran") == ["cdn.mp3quran.net", "server*.mp3quran.net"]
