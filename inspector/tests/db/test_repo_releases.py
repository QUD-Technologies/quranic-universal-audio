"""Smoke tests for the v2 release repository.

Covers per_recitation_releases insert + supersede + stale-stamp, gh_releases
+ gh_release_recitations insert/membership lookup, and the partial-unique
"one current row per (track, slug)" invariant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone

import pytest

from qua_shared.schemas import StaleReason
from services import db
from services.admin.timestamps_jobs import _ts_regen_provenance
from services.db import get_conn, repo_releases
from services.segments import ts_staleness


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_minimal_delivery(slug: str = "minshawy_murattal") -> str:
    """Insert vocab + a delivery so FK constraints on slug pass.

    Must run inside a ``db.transaction()`` block — writes are gated by the
    writer connection.
    """
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO riwayahs(slug,short,name) VALUES ('hafs_an_asim','hafs','Hafs')"
    )
    conn.execute("INSERT OR IGNORE INTO styles(slug,short,name) VALUES ('murattal','m','Murattal')")
    conn.execute(
        "INSERT OR IGNORE INTO sources(slug,name,url,audio_categories) VALUES ('everyayah','Everyayah',NULL,'[]')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO channels(slug,short,name,host_patterns,gh_release_eligible) VALUES ('everyayah','ea','EveryAyah','[]',1)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO reciters(reciter_id,name_en) VALUES ('minshawy','Minshawy')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO deliveries("
        "slug,reciter_id,riwayah,style,source,channel,audio_category,chapter_count,"
        "codec,container,bitrate_mode,added_at,added_by_hf_id"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            slug,
            "minshawy",
            "hafs_an_asim",
            "murattal",
            "everyayah",
            "everyayah",
            "by_surah",
            114,
            "mp3",
            "mp3",
            "cbr",
            "2024-01-01T00:00:00Z",
            "system",
        ),
    )
    return slug


def test_per_recitation_release_insert_and_current(fresh_db):
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        rid = repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version="1",
            produced_at=now,
            produced_by="alice",
        )
    assert rid > 0
    current = repo_releases.current_release("ts", slug)
    assert current is not None
    assert current["id"] == rid
    assert current["version"] == "1"


def test_supersede_current_marks_zero_prior_rows(fresh_db):
    """Real write order: insert v1, supersede v1, insert v2. The partial-unique
    requires the prior current row to be superseded before a new one inserts."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version="1",
            produced_at=now,
            produced_by="a",
        )
        repo_releases.supersede_current("ts", slug, except_id=-1, at=now)
        id2 = repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version="2",
            produced_at=now,
            produced_by="a",
        )
        n = repo_releases.supersede_current("ts", slug, except_id=id2, at=now)
    # id1 was superseded in the pre-insert sweep; supersede(except=id2) sweeps
    # nothing more (id1 already superseded).
    assert n == 0
    cur = repo_releases.current_release("ts", slug)
    assert cur is not None
    assert cur["id"] == id2


def test_partial_unique_blocks_two_current(fresh_db):
    """The partial-unique ``ux_per_recitation_current`` enforces at-most-one
    current row per (track, slug). Inserting a second current row without
    superseding the first must fail."""
    import sqlite3

    now = _now()
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction():
            slug = _seed_minimal_delivery()
            repo_releases.insert_per_recitation_release(
                track="ts",
                slug=slug,
                version="1",
                produced_at=now,
                produced_by="a",
            )
            repo_releases.insert_per_recitation_release(
                track="ts",
                slug=slug,
                version="2",
                produced_at=now,
                produced_by="a",
            )


def test_stamp_stale_marks_hf_with_reason(fresh_db):
    """A stamp records both stale_since and the reason on the current HF row."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="hf",
            slug=slug,
            version="abc123",
            produced_at=now,
            produced_by="a",
        )
        n = repo_releases.stamp_stale(slug, at=now, reason=StaleReason.TS_REGEN)
    assert n == 1
    hf_row = repo_releases.current_release("hf", slug)
    assert hf_row is not None
    assert hf_row["stale_since"] is not None
    assert hf_row["stale_reason"] == "ts_regen"


def test_stamp_stale_can_be_limited_to_the_gh_track(fresh_db):
    """A change only the GH cut ships (specials.json) stamps the GH member, not HF."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="hf", slug=slug, version="abc123", produced_at=now, produced_by="a"
        )
        rel_id = repo_releases.insert_gh_release(version="v0.1.0", produced_at=now, produced_by="a")
        repo_releases.insert_gh_release_recitation(
            release_id=rel_id, slug=slug, catalog_snapshot={"slug": slug}, zip_sha256="abc",
            zip_bytes=1, coverage_ayahs=1, content_hash="d", ts_version="1", change_kind="added",
        )  # fmt: skip
        n = repo_releases.stamp_stale(slug, at=now, reason=StaleReason.CATALOG_EDIT, tracks=("gh",))
    assert n == 1
    hf_row = repo_releases.current_release("hf", slug)
    assert hf_row is not None and hf_row["stale_since"] is None
    [gh] = repo_releases.gh_release_recitations(rel_id)
    assert gh["stale_reason"] == "catalog_edit"


def test_stamp_stale_catalog_edit_then_ts_regen_upgrades_reason(fresh_db):
    """A more-severe ts_regen overrides an existing catalog_edit reason (the
    clear path must not later treat the row as catalog-only)."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="hf", slug=slug, version="abc123", produced_at=now, produced_by="a"
        )
        repo_releases.stamp_stale(slug, at=now, reason=StaleReason.CATALOG_EDIT)
        first_row = repo_releases.current_release("hf", slug)
        assert first_row is not None
        first = first_row["stale_since"]
        repo_releases.stamp_stale(slug, at=now, reason=StaleReason.TS_REGEN)
    hf_row = repo_releases.current_release("hf", slug)
    assert hf_row is not None
    assert hf_row["stale_reason"] == "ts_regen"
    # stale_since records FIRST stale time — preserved across the upgrade.
    assert hf_row["stale_since"] == first


def test_stamp_stale_ts_regen_not_downgraded_by_catalog_edit(fresh_db):
    """A weaker catalog_edit must NOT overwrite an existing ts_regen reason."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="hf", slug=slug, version="abc123", produced_at=now, produced_by="a"
        )
        repo_releases.stamp_stale(slug, at=now, reason=StaleReason.TS_REGEN)
        n = repo_releases.stamp_stale(slug, at=now, reason=StaleReason.CATALOG_EDIT)
    assert n == 0  # nothing changed
    hf_row = repo_releases.current_release("hf", slug)
    assert hf_row is not None
    assert hf_row["stale_reason"] == "ts_regen"


def test_mark_ts_refreshed_advances_produced_at_and_stamps_stale(fresh_db):
    """An out-of-band refresh advances the current ts ``produced_at``, stamps the
    HF row stale, audits ``reciter.ts_refreshed``, and clears computed staleness."""
    from services.db import repo_transitions

    old = datetime(2020, 1, 1, tzinfo=UTC)
    new = datetime(2026, 6, 29, tzinfo=UTC)
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="ts", slug=slug, version="job-1", produced_at=old, produced_by="a"
        )
        repo_releases.insert_per_recitation_release(
            track="hf", slug=slug, version="abc123", produced_at=old, produced_by="a"
        )
        refreshed = repo_releases.mark_ts_refreshed(
            slug, at=new, chapters=[1, 114], reason="backfill_cells"
        )
    assert refreshed is True
    ts_row = repo_releases.current_release("ts", slug)
    assert ts_row is not None
    # produced_at advanced to the refresh watermark.
    assert ts_row["produced_at"].startswith("2026-06-29")
    # HF row stamped stale (the published dataset is now behind the refreshed shards).
    hf_row = repo_releases.current_release("hf", slug)
    assert hf_row is not None and hf_row["stale_reason"] == "ts_regen"
    # The audit event was appended with the chapters/reason payload.
    events = [t for t in repo_transitions.for_slug(slug) if t["event"] == "reciter.ts_refreshed"]
    assert len(events) == 1
    assert events[0]["payload"]["chapters"] == [1, 114]
    assert events[0]["payload"]["reason"] == "backfill_cells"


def test_mark_ts_refreshed_no_ts_row_returns_false(fresh_db):
    """A refresh callback for a reciter with no current ts release is a clean
    no-op (a backfill on a never-generated reciter), returning ``False``."""
    from services.db import repo_transitions

    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        refreshed = repo_releases.mark_ts_refreshed(slug, at=now)
    assert refreshed is False
    assert [
        t for t in repo_transitions.for_slug(slug) if t["event"] == "reciter.ts_refreshed"
    ] == []


def test_stamp_stale_no_hf_row_does_not_error(fresh_db):
    """Regen on a released reciter that has a TS row but was never published to
    HF: ``stamp_stale`` must no-op cleanly (no current hf row, no gh membership)
    rather than raise — the "Behind edits" → regen path runs for ts-only rows."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="ts", slug=slug, version="t1", produced_at=now, produced_by="a"
        )
        n = repo_releases.stamp_stale(slug, at=now, reason=StaleReason.TS_REGEN)
    assert n == 0
    assert repo_releases.current_release("hf", slug) is None


def test_clear_catalog_stale_hf_clears_only_catalog_edit(fresh_db):
    """The catalog refresh clears catalog_edit HF rows but leaves ts_regen ones
    (those still need a republish)."""
    now = _now()
    with db.transaction():
        cat = _seed_minimal_delivery("minshawy_murattal")
        repo_releases.insert_per_recitation_release(
            track="hf", slug=cat, version="c1", produced_at=now, produced_by="a"
        )
        ts = _seed_minimal_delivery("minshawy_mujawwad")
        repo_releases.insert_per_recitation_release(
            track="hf", slug=ts, version="t1", produced_at=now, produced_by="a"
        )
        repo_releases.stamp_stale(cat, at=now, reason=StaleReason.CATALOG_EDIT)
        repo_releases.stamp_stale(ts, at=now, reason=StaleReason.TS_REGEN)
        cleared = repo_releases.clear_catalog_stale_hf()
    assert cleared == 1
    cat_hf = repo_releases.current_release("hf", cat)
    ts_hf = repo_releases.current_release("hf", ts)
    assert cat_hf is not None
    assert ts_hf is not None
    assert cat_hf["stale_since"] is None
    assert cat_hf["stale_reason"] is None
    assert ts_hf["stale_since"] is not None


def test_stamp_stale_for_reciter_fans_out_to_all_deliveries(fresh_db):
    """A reciter-level catalog edit stamps every published delivery of that
    reciter (the public field appears in each delivery's catalog projection)."""
    now = _now()
    with db.transaction():
        a = _seed_minimal_delivery("minshawy_murattal")
        b = _seed_minimal_delivery("minshawy_mujawwad")  # same reciter_id 'minshawy'
        for slug in (a, b):
            repo_releases.insert_per_recitation_release(
                track="hf", slug=slug, version=f"v-{slug}", produced_at=now, produced_by="a"
            )
        n = repo_releases.stamp_stale_for_reciter(
            "minshawy", at=now, reason=StaleReason.CATALOG_EDIT
        )
    assert n == 2
    a_hf = repo_releases.current_release("hf", a)
    b_hf = repo_releases.current_release("hf", b)
    assert a_hf is not None
    assert b_hf is not None
    assert a_hf["stale_reason"] == "catalog_edit"
    assert b_hf["stale_reason"] == "catalog_edit"


def test_insert_persists_regen_provenance(fresh_db):
    """``affected_chapters`` (JSON list) + ``prior_ts_version`` round-trip on the
    ts row — the inputs a future granular changelog reads."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version="g2",
            produced_at=now,
            produced_by="a",
            affected_chapters=[5, 12],
            prior_ts_version="g1",
        )
    row = repo_releases.current_release("ts", slug)
    assert row is not None
    assert json.loads(row["affected_chapters"]) == [5, 12]
    assert row["prior_ts_version"] == "g1"


def test_insert_without_provenance_leaves_nulls(fresh_db):
    """A first publish (no prior) stores NULLs, not empty strings."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="ts", slug=slug, version="g1", produced_at=now, produced_by="a"
        )
    row = repo_releases.current_release("ts", slug)
    assert row is not None
    assert row["affected_chapters"] is None
    assert row["prior_ts_version"] is None


def _stub_affected(monkeypatch, chapters: list[int]) -> None:
    """Make ``_ts_regen_provenance``'s edit-history read return ``chapters``."""
    monkeypatch.setattr(
        ts_staleness,
        "ts_stale_info",
        lambda _s, *, produced_at: {
            "stale_since": "x",
            "edits_since": len(chapters),
            "affected_chapters": list(chapters),
        },
    )


def test_regen_provenance_chains_across_multiple_regens(fresh_db, monkeypatch):
    """Multiple regens before a re-release: each ts row records the delta since
    the PRIOR gen + chains via prior_ts_version; superseded rows keep their delta
    so a changelog can union the chapters changed since the last release. A
    re-publish (hf row) between regens must not disturb the ts chain."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    t4 = datetime(2026, 1, 3, tzinfo=UTC)

    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="ts", slug=slug, version="gen0", produced_at=t0, produced_by="a"
        )

    # regen1 folds in chapter 5 (computed off gen0's produced_at).
    _stub_affected(monkeypatch, [5])
    prior_v, affected = _ts_regen_provenance(slug)
    assert prior_v == "gen0"
    assert affected == [5]
    with db.transaction():
        repo_releases.supersede_current("ts", slug, except_id=-1, at=t2)
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version="gen1",
            produced_at=t2,
            produced_by="a",
            affected_chapters=affected,
            prior_ts_version=prior_v,
        )

    # A re-publish between regens — the ts chain must be unaffected.
    with db.transaction():
        repo_releases.insert_per_recitation_release(
            track="hf", slug=slug, version="hf1", produced_at=t2, produced_by="a"
        )

    # regen2 folds in chapter 12 (computed off gen1, the now-current ts row).
    _stub_affected(monkeypatch, [12])
    prior_v, affected = _ts_regen_provenance(slug)
    assert prior_v == "gen1"
    assert affected == [12]
    with db.transaction():
        repo_releases.supersede_current("ts", slug, except_id=-1, at=t4)
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version="gen2",
            produced_at=t4,
            produced_by="a",
            affected_chapters=affected,
            prior_ts_version=prior_v,
        )

    chain = repo_releases.all_releases_for_track_slug("ts", slug)
    assert [(r["version"], r["prior_ts_version"]) for r in chain] == [
        ("gen0", None),
        ("gen1", "gen0"),
        ("gen2", "gen1"),
    ]
    # Superseded gen1 keeps its delta — history not lost.
    deltas = [json.loads(r["affected_chapters"]) for r in chain if r["affected_chapters"]]
    assert deltas == [[5], [12]]
    # Changelog aggregate since the last release (gen0) = union of the deltas.
    assert sorted({c for d in deltas for c in d}) == [5, 12]


def test_regen_provenance_same_chapter_twice_dedups_in_union(fresh_db, monkeypatch):
    """Editing + regenerating the SAME chapter twice records it on each gen; the
    changelog union dedups to one chapter."""
    t0 = datetime(2026, 2, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 2, tzinfo=UTC)
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="ts", slug=slug, version="g0", produced_at=t0, produced_by="a"
        )
    _stub_affected(monkeypatch, [7])
    prior_v, affected = _ts_regen_provenance(slug)
    with db.transaction():
        repo_releases.supersede_current("ts", slug, except_id=-1, at=t2)
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version="g1",
            produced_at=t2,
            produced_by="a",
            affected_chapters=affected,
            prior_ts_version=prior_v,
        )
    chain = repo_releases.all_releases_for_track_slug("ts", slug)
    deltas = [json.loads(r["affected_chapters"]) for r in chain if r["affected_chapters"]]
    assert sorted({c for d in deltas for c in d}) == [7]


def test_all_releases_for_track_slug_returns_all_ordered(fresh_db):
    """Returns current + superseded rows for (track, slug) ascending by
    produced_at — the generation-history timeline for the tier view."""
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    with db.transaction():
        slug = _seed_minimal_delivery()
        repo_releases.insert_per_recitation_release(
            track="ts", slug=slug, version="g1", produced_at=t1, produced_by="a"
        )
        repo_releases.supersede_current("ts", slug, except_id=-1, at=t2)
        repo_releases.insert_per_recitation_release(
            track="ts", slug=slug, version="g2", produced_at=t2, produced_by="a"
        )
    rows = repo_releases.all_releases_for_track_slug("ts", slug)
    assert [r["version"] for r in rows] == ["g1", "g2"]
    # other tracks excluded
    assert repo_releases.all_releases_for_track_slug("hf", slug) == []


def test_gh_release_insert_and_membership(fresh_db):
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        rel_id = repo_releases.insert_gh_release(
            version="v0.1.0",
            produced_at=now,
            produced_by="alice",
        )
        repo_releases.insert_gh_release_recitation(
            release_id=rel_id,
            slug=slug,
            catalog_snapshot={"slug": slug},
            zip_sha256="abc",
            zip_bytes=1000,
            coverage_ayahs=6236,
            content_hash="def",
            ts_version="1",
            change_kind="added",
        )
    assert rel_id > 0
    members = repo_releases.gh_release_recitations(rel_id)
    assert len(members) == 1
    assert members[0]["slug"] == slug
    assert members[0]["change_kind"] == "added"

    latest = repo_releases.latest_gh_release_member(slug)
    assert latest is not None
    assert latest["release_id"] == rel_id


def test_gh_release_supersede(fresh_db):
    now = _now()
    with db.transaction():
        repo_releases.insert_gh_release(version="v0.1.0", produced_at=now, produced_by="a")
        id2 = repo_releases.insert_gh_release(version="v0.2.0", produced_at=now, produced_by="a")
        n = repo_releases.supersede_prior_gh_releases(except_id=id2, at=now)
    assert n == 1
    latest = repo_releases.latest_gh_release()
    assert latest is not None
    assert latest["id"] == id2


def test_release_by_version_finds_superseded(fresh_db):
    """``release_by_version`` returns ANY row matching (track, slug, version),
    including superseded ones. Idempotency guard for webhook retries after a
    later publish has superseded the prior row."""
    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        id1 = repo_releases.insert_per_recitation_release(
            track="hf",
            slug=slug,
            version="rev-A",
            produced_at=now,
            produced_by="a",
        )
        repo_releases.supersede_current("hf", slug, except_id=-1, at=now)
        repo_releases.insert_per_recitation_release(
            track="hf",
            slug=slug,
            version="rev-B",
            produced_at=now,
            produced_by="a",
        )
    found = repo_releases.release_by_version("hf", slug, "rev-A")
    assert found is not None
    assert found["id"] == id1
    assert found["superseded_at"] is not None
    # Non-existent version → None.
    assert repo_releases.release_by_version("hf", slug, "rev-X") is None


def test_gh_release_by_version_finds_superseded(fresh_db):
    """Same idempotency guard for gh_releases."""
    now = _now()
    with db.transaction():
        id1 = repo_releases.insert_gh_release(version="v0.1.0", produced_at=now, produced_by="a")
        repo_releases.supersede_prior_gh_releases(except_id=-1, at=now)
        repo_releases.insert_gh_release(version="v0.2.0", produced_at=now, produced_by="a")
    found = repo_releases.gh_release_by_version("v0.1.0")
    assert found is not None
    assert found["id"] == id1
    assert found["superseded_at"] is not None
    assert repo_releases.gh_release_by_version("v9.9.9") is None


def test_content_hash_constraints(fresh_db):
    """zip_bytes must be > 0 and coverage_ayahs must be in [0, 6236]."""
    import sqlite3

    now = _now()
    with db.transaction():
        slug = _seed_minimal_delivery()
        rel_id = repo_releases.insert_gh_release(version="v0.1.0", produced_at=now, produced_by="a")
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction():
            repo_releases.insert_gh_release_recitation(
                release_id=rel_id,
                slug=slug,
                catalog_snapshot={"slug": slug},
                zip_sha256="abc",
                zip_bytes=0,
                coverage_ayahs=6236,
                content_hash="def",
                ts_version="1",
                change_kind="added",
            )
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction():
            repo_releases.insert_gh_release_recitation(
                release_id=rel_id,
                slug=slug,
                catalog_snapshot={"slug": slug},
                zip_sha256="abc",
                zip_bytes=1000,
                coverage_ayahs=7000,
                content_hash="def",
                ts_version="1",
                change_kind="added",
            )
