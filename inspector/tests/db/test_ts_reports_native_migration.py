"""Lossless preparation of the guarded native report-target cutover."""

from __future__ import annotations

import json
import sqlite3

import pytest

from services.db import migrate
from services.ts_reports.legacy_target_migration import (
    LegacyReportMappingError,
    assert_native_report_schema,
    prepare_native_report_map,
)


def _v27_db(path, monkeypatch) -> sqlite3.Connection:
    migrations = migrate._discover()
    monkeypatch.setattr(migrate, "_discover", lambda: [row for row in migrations if row[0] <= 27])
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    assert migrate.run_migrations(conn) == 27
    monkeypatch.setattr(migrate, "_discover", lambda: migrations)
    return conn


def _document() -> dict:
    def column(**over):
        return {
            "id": 10,
            "role": "letter",
            "text": "a",
            "source_character_ids": [],
            "source_unit_ids": [0],
            "slot_ids": [],
            "tier": "main",
            "attached_to_column_id": None,
            "status": "present",
            "variant_id": None,
            "variant_choice": None,
            "anchor_unit_id": None,
            "side": None,
            "owned_sound_ids": [0],
            "presented_sound_ids": [],
            "rule_occurrence_ids": [],
            "silence": None,
            **over,
        }

    return {
        "_meta": {"schema_version": 13},
        "readings": [
            {
                "id": "r1",
                "parts": [{"ref": "1:1", "t": [100, 500], "word_ids": [0]}],
                "analysis": {
                    "result": {
                        "words": [
                            {
                                "id": 0,
                                "ref": "1:1:1",
                                "text": "a",
                                "before_boundary_id": 0,
                                "after_boundary_id": 1,
                                "sound_ids": [0],
                            }
                        ],
                        "sounds": [{"id": 0}],
                        "boundaries": [
                            {"id": 0, "before": None, "after": 0, "state": "join"},
                            {"id": 1, "before": 0, "after": None, "state": "stop"},
                        ],
                        "rule_occurrences": [],
                    }
                },
                "cells": {
                    "cell_view": {
                        "words": [
                            {
                                "word_id": 0,
                                "columns": [
                                    column(),
                                    column(
                                        id=11,
                                        role="haraka",
                                        text="َ",
                                        source_unit_ids=[1],
                                        tier="above",
                                        attached_to_column_id=10,
                                    ),
                                ],
                                "groups": [
                                    {
                                        "key": 10,
                                        "kind": "base",
                                        "column_ids": [10, 11],
                                        "sound_ids": [0],
                                    }
                                ],
                                "sounds": [
                                    {
                                        "sound_id": 0,
                                        "column_ids": [10, 11],
                                        "rule_occurrence_ids": [],
                                    }
                                ],
                                "bridges": [],
                            }
                        ],
                        "boundaries": [],
                    }
                },
                "timing": {
                    "words": [{"word_id": 0, "start_ms": 100, "end_ms": 400}],
                    "sounds": [{"sound_id": 0, "start_ms": 100, "end_ms": 300}],
                    "animation_tokens": [
                        {
                            "id": 0,
                            "word_id": 0,
                            "text": "aَ",
                            "source_unit_ids": [0, 1],
                            "policy": "timed",
                            "start_ms": 100,
                            "end_ms": 300,
                        },
                    ],
                    "columns": [],
                    "boundaries": [
                        {"boundary_id": 0, "start_ms": 100, "end_ms": 100},
                        {"boundary_id": 1, "start_ms": 400, "end_ms": 500},
                    ],
                },
            }
        ],
    }


def _insert_legacy(conn: sqlite3.Connection, **over) -> int:
    values = {
        "slug": "reciter-a",
        "verse_key": "1:1",
        "chapter": 1,
        "category": "timing",
        "subtype": None,
        "timing_onset": "early",
        "target_kind": "cell",
        "word_index": 0,
        "cell_index": 0,
        "target_key": "cell:0:0",
        "snap_chars": "a",
        "snap_role": "base",
        "snap_status": "present",
        "snap_word_text": "a",
        "snap_onset_ms": 100,
        "snap_offset_ms": 300,
        "anon_token": "anon-cell",
        "comment": "preserve me",
        "status": "open",
        "stale": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    values.update(over)
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    cursor = conn.execute(
        f"INSERT INTO ts_reports ({columns}) VALUES ({placeholders})", tuple(values.values())
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def test_prepares_every_legacy_target_then_migration_preserves_rows(tmp_path, monkeypatch):
    conn = _v27_db(tmp_path / "legacy.db", monkeypatch)
    cell_id = _insert_legacy(conn)
    gap_id = _insert_legacy(
        conn,
        category="silence",
        subtype="pause_wasl",
        timing_onset=None,
        target_kind="gap",
        target_key="gap:0",
        cell_index=None,
        snap_chars=None,
        snap_role=None,
        snap_status=None,
        snap_word_text=None,
        snap_onset_ms=None,
        snap_offset_ms=None,
        anon_token="anon-gap",
    )
    group_id = _insert_legacy(
        conn,
        category="tajweed",
        subtype="wrong_rule",
        timing_onset=None,
        target_kind="cell_group",
        target_key="cell_group:0:1",
        cell_index=1,
        snap_chars="aَ",
        snap_role=None,
        snap_status=None,
        selected_rule_tags=json.dumps(["rule-a"]),
        anon_token="anon-group",
    )
    word_id = _insert_legacy(
        conn,
        target_kind="word",
        target_key="word:0",
        cell_index=None,
        snap_chars=None,
        snap_role=None,
        snap_status=None,
        snap_offset_ms=400,
        anon_token="anon-word",
    )
    sound_id = _insert_legacy(
        conn,
        target_kind="phoneme",
        target_key="phoneme:0:0",
        cell_index=None,
        phoneme_flat_index=0,
        snap_chars=None,
        snap_role=None,
        snap_status=None,
        anon_token="anon-sound",
    )
    verse_id = _insert_legacy(
        conn,
        target_kind="verse",
        target_key="verse",
        word_index=None,
        cell_index=None,
        snap_chars=None,
        snap_role=None,
        snap_status=None,
        snap_word_text=None,
        snap_onset_ms=100,
        snap_offset_ms=500,
        anon_token="anon-verse",
    )

    def loader(_slug, _chapter):
        return _document()

    assert prepare_native_report_map(conn, load_document=loader) == 6
    assert prepare_native_report_map(conn, load_document=loader) == 6
    assert migrate.run_migrations(conn) == 32
    assert_native_report_schema(conn)

    rows = {
        row["id"]: row
        for row in conn.execute(
            "SELECT id,reading_id,target_kind,target_id,comment,selected_rule_tags "
            "FROM ts_reports ORDER BY id"
        )
    }
    assert (
        rows[cell_id]["reading_id"],
        rows[cell_id]["target_kind"],
        rows[cell_id]["target_id"],
    ) == (
        "r1",
        "column",
        "10",
    )
    assert rows[cell_id]["comment"] == "preserve me"
    assert (rows[gap_id]["target_kind"], rows[gap_id]["target_id"]) == ("boundary", "1")
    assert (rows[group_id]["target_kind"], rows[group_id]["target_id"]) == (
        "group",
        "10:11",
    )
    assert json.loads(rows[group_id]["selected_rule_tags"]) == ["rule-a"]
    assert (rows[word_id]["target_kind"], rows[word_id]["target_id"]) == ("word", "0")
    assert (rows[sound_id]["target_kind"], rows[sound_id]["target_id"]) == ("sound", "0")
    assert (rows[verse_id]["target_kind"], rows[verse_id]["target_id"]) == ("verse", "1:1")


def test_refuses_drift_without_leaving_a_partial_map(tmp_path, monkeypatch):
    conn = _v27_db(tmp_path / "drift.db", monkeypatch)
    _insert_legacy(conn, snap_chars="different")

    with pytest.raises(LegacyReportMappingError, match="column text drift"):
        prepare_native_report_map(conn, load_document=lambda _slug, _chapter: _document())

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 27
    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ts_report_v12_map'"
    ).fetchone()
