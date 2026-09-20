"""Tests for the PII-critical core of ``scripts/devenv/make_fixtures_dataset.py``.

The fixtures dataset is PUBLIC, so the one property that must never regress is:
the generated ``inspector.db`` contains the chosen reciters' catalog/state and
**nothing from the identity/audit tables** (users, transitions, claims,
requests, role_assignments, activity_*).

The test builds a synthetic source DB with the real schema (init_db), populates
it with both catalog rows AND fake PII rows, then runs ``build_fixtures_db`` and
asserts the catalog made it across while every PII table is empty.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "devenv" / "make_fixtures_dataset.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("make_fixtures_dataset", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_source(tmp_path: Path) -> Path:
    """Create a synthetic source DB with the real schema + PII rows."""
    from services import db as _db

    src = tmp_path / "source.db"
    _db.set_db_path_for_test(src)
    _db.init_db()
    c = _db.get_writer()

    c.execute("INSERT INTO users(hf_user_id, login_cache) VALUES ('u-real','realperson')")
    for tbl in ("riwayahs", "styles"):
        c.execute(f"INSERT INTO {tbl}(slug, short, name) VALUES ('hafs','H','Hafs')")
    c.execute("INSERT INTO sources(slug, name) VALUES ('source_a','Source A')")
    c.execute("INSERT INTO channels(slug, short, name) VALUES ('web','W','Web')")
    c.execute("INSERT INTO recording_contexts(slug, name) VALUES ('prayer','Prayer')")
    c.execute("INSERT INTO reciters(reciter_id, name_en) VALUES ('rec1','Reciter One')")

    for slug in ("rec1_a", "rec1_b", "rec1_c"):
        c.execute(
            "INSERT INTO deliveries(slug, reciter_id, riwayah, style, source, channel, "
            "audio_category, chapter_count, added_at, added_by_hf_id) "
            "VALUES (?, 'rec1','hafs','hafs','source_a','web','by_surah',114,'2024-01-01T00:00:00+00:00','u-real')",
            (slug,),
        )
    # A transition (PII: actor identity) that a state row references.
    c.execute(
        "INSERT INTO transitions(id, ts, slug, event, actor_id, actor_login_snapshot) "
        "VALUES ('t1','2024-01-01T00:00:00+00:00','rec1_a','catalogued','u-real','realperson')"
    )
    c.execute(
        "INSERT INTO delivery_states(slug, state, state_since, created_by_transition_id) "
        "VALUES ('rec1_a','under_review','2024-01-01T00:00:00+00:00','t1')"
    )
    c.execute(
        "INSERT INTO delivery_states(slug, state, state_since) "
        "VALUES ('rec1_b','catalogued','2024-01-01T00:00:00+00:00')"
    )
    # More PII rows that must NOT cross over.
    c.execute(
        "INSERT INTO role_assignments(hf_user_id, role, granted_at) "
        "VALUES ('u-real','owner','2024-01-01T00:00:00+00:00')"
    )
    c.execute(
        "INSERT INTO claims(slug, assignee_id, claimed_at) "
        "VALUES ('rec1_a','u-real','2024-01-01T00:00:00+00:00')"
    )
    c.execute(
        "INSERT INTO requests(id, kind, slug, requester_id, submitted_at, status, comments) "
        "VALUES ('req1','existing_combo_edit','rec1_a','u-real','2024-01-01T00:00:00+00:00','pending','call me at ...')"
    )
    c.commit()
    return src


def test_build_fixtures_db_excludes_pii(tmp_path):
    mod = _load_script()
    src = _seed_source(tmp_path)
    out = tmp_path / "fixtures" / "inspector.db"

    result = mod.build_fixtures_db(src, out, ["rec1_a"])
    assert result["slugs"] == ["rec1_a"]

    # Read the generated DB independently.
    conn = sqlite3.connect(out)
    try:

        def count(table, where=""):
            return conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]

        # Catalog for the chosen slug crossed over; the other did not.
        assert count("deliveries") == 1
        assert conn.execute("SELECT slug FROM deliveries").fetchone()[0] == "rec1_a"
        assert count("reciters") == 1
        assert count("delivery_states") == 1
        assert count("riwayahs") == 1  # vocab copied wholesale

        # The lone identity field is scrubbed; the audit ref is nulled.
        assert conn.execute("SELECT added_by_hf_id FROM deliveries").fetchone()[0] == "seed"
        assert (
            conn.execute("SELECT created_by_transition_id FROM delivery_states").fetchone()[0]
            is None
        )

        # Every identity/audit table present in the current schema is empty —
        # the PII never leaves. (activity_dismissals was dropped in 0006.)
        existing = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for pii in (
            "users",
            "transitions",
            "claims",
            "requests",
            "role_assignments",
            "activity_tombstones",
        ):
            if pii in existing:
                assert count(pii) == 0, f"PII leaked into {pii}"
    finally:
        conn.close()


def test_build_fixtures_db_rejects_unknown_slug(tmp_path):
    mod = _load_script()
    src = _seed_source(tmp_path)
    out = tmp_path / "fixtures" / "inspector.db"
    with pytest.raises(SystemExit):
        mod.build_fixtures_db(src, out, ["does_not_exist"])


def test_build_fixtures_db_synthesizes_missing_state(tmp_path):
    """rec1_c has a delivery but no delivery_states row in the source; the
    builder must synthesize an editable state so it opens in the editor."""
    mod = _load_script()
    src = _seed_source(tmp_path)
    out = tmp_path / "fixtures" / "inspector.db"

    mod.build_fixtures_db(src, out, ["rec1_c"])
    conn = sqlite3.connect(out)
    try:
        state = conn.execute("SELECT state FROM delivery_states WHERE slug='rec1_c'").fetchone()
        assert state is not None, "missing state row was not synthesized"
        assert state[0] == mod._DEFAULT_FIXTURE_STATE
    finally:
        conn.close()
