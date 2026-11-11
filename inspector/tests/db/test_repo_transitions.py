"""repo_transitions round-trip + content_hash parity with the activity layer."""

from __future__ import annotations

from qua_shared.schemas import Actor, Role
from services import db
from services.activity import activity_classification
from services.db import repo_transitions


def _actor(hf="u1", login="alice", role=Role.OWNER) -> Actor:
    return Actor(hf_user_id=hf, login_at_time=login, role=role)


def _seed_user(hf="u1", login="alice"):
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(hf_user_id, login_cache) VALUES (?,?)",
            (hf, login),
        )


def test_append_and_get_roundtrip(fresh_db):
    _seed_user()
    rec = repo_transitions.append(
        event="access.role_granted",
        actor=_actor(),
        payload={"target_hf_user_id": "x"},
        reason="because reasons",
    )
    assert rec.event == "access.role_granted"
    assert rec.request_id and rec.request_id.startswith("req_")

    got = repo_transitions.get(rec.request_id)
    assert got is not None
    assert got["event"] == "access.role_granted"
    assert got["payload"] == {"target_hf_user_id": "x"}
    assert got["reason"] == "because reasons"
    assert got["actor"]["hf_user_id"] == "u1"
    assert got["actor"]["role"] == "owner"


def test_content_hash_matches_activity_audit_id(fresh_db):
    """The stored content_hash MUST equal what activity_classification.audit_id
    computes for the reconstructed record — otherwise migrated dismissals /
    tombstones (keyed on that hash) silently stop matching."""
    _seed_user()
    rec = repo_transitions.append(event="reciter.released", actor=_actor())
    assert rec.request_id is not None
    got = repo_transitions.get(rec.request_id)
    assert got is not None
    assert got["content_hash"] == activity_classification.audit_id(got)


def test_feed_order_and_event_filter(fresh_db):
    _seed_user()
    repo_transitions.append(event="reciter.claimed", actor=_actor())
    repo_transitions.append(event="reciter.released", actor=_actor())
    repo_transitions.append(event="access.role_granted", actor=_actor())

    # most-recent-first
    all_recs = list(repo_transitions.feed(limit=10))
    assert [r["event"] for r in all_recs] == [
        "access.role_granted",
        "reciter.released",
        "reciter.claimed",
    ]
    # event-kind filter
    only = list(repo_transitions.feed(events={"reciter.released", "reciter.claimed"}))
    assert {r["event"] for r in only} == {"reciter.released", "reciter.claimed"}
    # empty allow-list yields nothing
    assert list(repo_transitions.feed(events=set())) == []


def test_append_enrolls_in_outer_transaction(fresh_db):
    """If the caller is already in a transaction and it rolls back, the
    appended transition rolls back too (atomicity of multi-store handlers)."""
    _seed_user()
    try:
        with db.transaction():
            repo_transitions.append(event="reciter.claimed", actor=_actor())
            raise RuntimeError("outer boom")
    except RuntimeError:
        pass
    assert list(repo_transitions.feed(limit=10)) == []


def test_for_slug_timeline(fresh_db):
    _seed_user()
    # seed a delivery so the slug FK holds
    with db.transaction() as conn:
        conn.execute("INSERT INTO reciters(reciter_id, name_en) VALUES ('r','R')")
        conn.execute("INSERT INTO riwayahs(slug,short,name) VALUES ('hafs','h','Hafs')")
        conn.execute("INSERT INTO styles(slug,short,name) VALUES ('mur','m','Mur')")
        conn.execute("INSERT INTO sources(slug,name) VALUES ('src','Src')")
        conn.execute("INSERT INTO channels(slug,short,name) VALUES ('ch','c','Ch')")
        conn.execute(
            "INSERT INTO deliveries(slug, reciter_id, riwayah, style, source, channel,"
            " audio_category, chapter_count, added_at, added_by_hf_id) VALUES "
            "('d1','r','hafs','mur','src','ch','by_surah',114,'2026-01-01T00:00:00Z','u1')"
        )
    repo_transitions.append(
        event="reciter.requested",
        actor=_actor(),
        slug="d1",
        from_state="catalogued",
        to_state="awaiting_alignment",
    )
    repo_transitions.append(
        event="reciter.alignment_completed",
        actor=_actor(),
        slug="d1",
        from_state="awaiting_alignment",
        to_state="awaiting_review",
    )
    timeline = repo_transitions.for_slug("d1")
    assert [t["event"] for t in timeline] == [
        "reciter.requested",
        "reciter.alignment_completed",
    ]
    assert timeline[0]["from_state"] == "catalogued"


def test_aligned_at_by_slug_counts_only_alignment_events(fresh_db):
    """Only a real alignment completion (or an owner force-move out of
    awaiting_alignment) dates a delivery; a claim force-released back to
    awaiting_review is not an alignment."""
    from tests.conftest import _seed_delivery_chain

    _seed_user()
    with db.transaction() as conn:
        for slug in ("s1", "s2", "s3"):
            _seed_delivery_chain(conn, slug)
    a = _actor()
    first = repo_transitions.append(
        event="reciter.alignment_completed", actor=a, slug="s1",
        from_state="awaiting_alignment", to_state="awaiting_review",
    )
    repo_transitions.append(
        event="claim.force_released", actor=a, slug="s1",
        from_state="under_review", to_state="awaiting_review",
    )
    forced = repo_transitions.append(
        event="admin.force_set_state", actor=a, slug="s2",
        from_state="awaiting_alignment", to_state="awaiting_review",
    )
    repo_transitions.append(
        event="claim.force_released", actor=a, slug="s3",
        from_state="under_review", to_state="awaiting_review",
    )

    got = repo_transitions.aligned_at_by_slug()

    assert first.request_id is not None and forced.request_id is not None
    first_rec = repo_transitions.get(first.request_id)
    forced_rec = repo_transitions.get(forced.request_id)
    assert first_rec is not None and forced_rec is not None
    assert set(got) == {"s1", "s2"}
    assert got["s1"] == first_rec["ts"]
    assert got["s2"] == forced_rec["ts"]
