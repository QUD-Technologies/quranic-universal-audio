"""Tests for the batch-Space timestamps launcher.

``launch`` signs + POSTs the run to the batch timing Space; ``job_status`` reads
the bucket run-log record the Space writes (no HF Job to inspect) and fires the
idempotent completion / failure fallbacks on a terminal record.
"""

from __future__ import annotations

from datetime import UTC

from services.admin import timestamps_jobs


def test_timestamp_space_defaults_to_production(monkeypatch):
    from services.admin import ts_space_client

    monkeypatch.delenv("INSPECTOR_TS_SPACE_URL", raising=False)
    assert ts_space_client.space_url() == "https://hetchyy-qua-batch-timing-prod.hf.space"


def test_job_status_reads_record_and_fires_success(monkeypatch):
    rec = {
        "job_id": "j1",
        "slug": "r",
        "type": "ts",
        "status": "succeeded",
        "logs": ["a", "b"],
        "url": None,
    }
    monkeypatch.setattr(timestamps_jobs, "read_job_record", lambda slug, jid: dict(rec))
    completed = []
    monkeypatch.setattr(
        timestamps_jobs, "complete_timestamps_job", lambda s, j: completed.append((s, j))
    )

    out = timestamps_jobs.job_status("r", "j1")

    assert out["status"] == "succeeded"
    assert out["logs"] == ["a", "b"]
    assert completed == [("r", "j1")]  # success fires the publish/regen path


def test_job_status_failure_notes_failed(monkeypatch):
    rec = {"job_id": "j1", "slug": "r", "type": "ts", "status": "failed", "logs": []}
    monkeypatch.setattr(timestamps_jobs, "read_job_record", lambda slug, jid: dict(rec))
    noted = []
    monkeypatch.setattr(timestamps_jobs, "note_timestamps_job_failed", lambda s: noted.append(s))

    out = timestamps_jobs.job_status("r", "j1")

    assert out["status"] == "failed"
    assert noted == ["r"]


def test_job_status_unknown_when_no_record(monkeypatch):
    monkeypatch.setattr(timestamps_jobs, "read_job_record", lambda slug, jid: None)
    out = timestamps_jobs.job_status("r", "missing")
    assert out["status"] == "unknown"
    assert out["logs"] == []


def test_launch_posts_space_and_links_run(monkeypatch):
    from qua_shared.schemas import TsJobSettings
    from services.admin import ts_space_client
    from services.state import state as state_service
    from services.storage import cache as _cache
    from services.storage import data_loader

    monkeypatch.setattr(state_service, "get_row", lambda slug: object())
    posted = {}

    def _fake_start(slug, *, chapters=None, beams=None, riwayah="hafs"):
        posted.update(slug=slug, chapters=chapters, beams=beams, riwayah=riwayah)
        return "run-xyz"

    monkeypatch.setattr(ts_space_client, "start_run", _fake_start)
    linked = []
    monkeypatch.setattr(state_service, "record_timestamps_job", lambda s, j: linked.append((s, j)))
    monkeypatch.setattr(_cache, "invalidate_in_flight_jobs_cache", lambda: None)
    # The launch resolves the delivery's edition, which cross-checks the catalog
    # row against detailed.json's `_meta` — reading it off the bucket for a slug
    # this test never wrote. Answer "no meta", which is what a fixture has.
    monkeypatch.setattr(data_loader, "seg_meta", lambda slug: {})

    out = timestamps_jobs.launch("r", settings=TsJobSettings(beams=[50, 5], chapters=[108]))

    assert out == {"job_id": "run-xyz", "url": None}
    assert posted == {
        "slug": "r",
        "chapters": [108],
        "beams": [50, 5],
        "riwayah": "hafs",
    }
    assert linked == [("r", "run-xyz")]


def test_terminal_success_runs_reads_latest_linked_record(monkeypatch):
    class Row:
        slug = "r"
        timestamps_job_ids = ["old", "latest"]

    monkeypatch.setattr(timestamps_jobs.state_service, "all_rows", lambda: [Row()])
    monkeypatch.setattr(
        timestamps_jobs,
        "read_job_record",
        lambda slug, jid: {"status": "succeeded"} if jid == "latest" else {"status": "failed"},
    )

    assert timestamps_jobs.terminal_success_runs() == [("r", "latest")]


# ---------------------------------------------------------------------------
# complete_timestamps_job — auto-release orchestrator
# ---------------------------------------------------------------------------


def _seed_marked_ready(slug: str = "rec_a") -> None:
    """Seed an under_review reciter with an open, marked-ready claim."""
    from tests.conftest import _seed_state

    _seed_state(slug, state="under_review", assignee_hf_id="u-rev", marked_ready=True)


def test_complete_publishes_marked_ready(monkeypatch):
    from services.state import state as state_service

    _seed_marked_ready("rec_a")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: True)

    out = timestamps_jobs.complete_timestamps_job("rec_a", "job-1")

    assert out["released"] is True
    assert out["state"] == "released"
    row = state_service.get_row("rec_a")
    assert row is not None
    assert row.state.value == "released"
    assert row.assignee_hf_id is None
    assert row.timestamps_job_ids == ["job-1"]


def _seed_released_with_ledger(
    slug: str, *, ts_version: str, hf_version: str | None = None
) -> None:
    """Seed a released reciter with a current ``ts`` (and optional ``hf``)
    per_recitation_releases row — the post-first-publish shape a regen acts on."""
    from datetime import datetime, timezone

    from services import db
    from services.db import repo_releases
    from tests.conftest import _seed_state

    _seed_state(slug, state="released")
    now = datetime.now(UTC)
    with db.transaction():
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version=ts_version,
            produced_at=now,
            produced_by="SYSTEM_ACTOR",
            produced_by_job_id=ts_version,
        )
        if hf_version is not None:
            repo_releases.insert_per_recitation_release(
                track="hf",
                slug=slug,
                version=hf_version,
                produced_at=now,
                produced_by="SYSTEM_ACTOR",
            )


def test_complete_regen_idempotent_when_ts_already_recorded(monkeypatch):
    """A poll re-fire of an already-recorded job (released + the job's ts row
    present) is a no-op — no duplicate ts row, no extra audit event."""
    from services.db import repo_releases

    _seed_released_with_ledger("rec_a", ts_version="job-1")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: True)

    out = timestamps_jobs.complete_timestamps_job("rec_a", "job-1")

    assert out["released"] is False
    assert out["reason"] == "ts already recorded"
    # Still exactly one current ts row at the original version.
    ts_current = repo_releases.current_release("ts", "rec_a")
    assert ts_current is not None
    assert ts_current["version"] == "job-1"


def test_complete_regen_on_released_row(monkeypatch):
    """A fresh TS run (new job_id) on a released reciter: no transition, new ts
    row supersedes the prior, HF release stamped stale, ts_regenerated audited."""
    from services.db import repo_releases, repo_transitions
    from services.state import state as state_service

    _seed_released_with_ledger("rec_a", ts_version="job-old", hf_version="sha-old")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: True)

    out = timestamps_jobs.complete_timestamps_job("rec_a", "job-new")

    assert out["released"] is False
    assert out["regenerated"] is True
    # State unchanged — no released → released transition.
    row = state_service.get_row("rec_a")
    assert row is not None
    assert row.state.value == "released"
    # New ts row is current; the prior one is superseded.
    ts_current = repo_releases.current_release("ts", "rec_a")
    assert ts_current is not None
    assert ts_current["version"] == "job-new"
    ts_old = repo_releases.release_by_version("ts", "rec_a", "job-old")
    assert ts_old is not None
    assert ts_old["superseded_at"] is not None
    # HF membership stamped stale so the operator is driven to re-publish.
    hf_current = repo_releases.current_release("hf", "rec_a")
    assert hf_current is not None
    assert hf_current["stale_since"] is not None
    # Audit trail carries the distinct regen event.
    events = [
        r for r in repo_transitions.for_slug("rec_a") if r["event"] == "reciter.ts_regenerated"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["job_id"] == "job-new"


def test_complete_regen_refuses_when_no_shards(monkeypatch):
    """A regen job that exits 0 without writing shards records nothing."""
    from services.db import repo_releases

    _seed_released_with_ledger("rec_a", ts_version="job-old")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: False)

    out = timestamps_jobs.complete_timestamps_job("rec_a", "job-new")

    assert out["released"] is False
    assert out["reason"] == "no shards"
    # No new ts row — the prior remains current.
    ts_current = repo_releases.current_release("ts", "rec_a")
    assert ts_current is not None
    assert ts_current["version"] == "job-old"


def test_complete_regen_stales_report_whose_content_changed(monkeypatch):
    """A regen on a released reciter flags an open report stale when the new
    shard changed its exact native column."""
    from services import db
    from services.db import repo_ts_reports
    from services.ts_reports import ts_target_snapshot

    _seed_released_with_ledger("rec_a", ts_version="job-old")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: True)

    with db.transaction():
        row, _ = repo_ts_reports.create(
            slug="rec_a",
            verse_key="2:45",
            category="tajweed",
            subtype="wrong_rule",
            target={"reading_id": "r1", "kind": "column", "target_id": "10"},
            snapshot={
                "native_schema_version": 2,
                "shard_schema_version": 12,
                "native": {"id": 10, "text": "ب", "word_id": 1, "word_ref": "2:45:1"},
                "timing": None,
            },
            comment="wrong rule",
            hf_user_id=None,
            anon_token="anon-1",
            login_at_time=None,
            role_at_time=None,
        )

    # The regenerated shard changes the exact native column fingerprint.
    changed = {
        "_meta": {"schema_version": 12},
        "readings": [
            {
                "id": "r1",
                "parts": [{"ref": "2:45", "t": [0, 20], "word_ids": [1]}],
                "analysis": {
                    "result": {
                        "words": [{"id": 1, "ref": "2:45:1"}],
                        "sounds": [],
                        "boundaries": [],
                    }
                },
                "cells": {
                    "cell_view": {
                        "words": [
                            {
                                "word_id": 1,
                                "columns": [
                                    {
                                        "id": 10,
                                        "text": "changed",
                                        "source_unit_ids": [],
                                        "owned_sound_ids": [],
                                        "presented_sound_ids": [],
                                    }
                                ],
                                "groups": [],
                                "bridges": [],
                            }
                        ],
                        "boundaries": [],
                    }
                },
                "timing": {"words": [], "sounds": [], "units": [], "boundaries": []},
            }
        ],
    }
    monkeypatch.setattr(ts_target_snapshot, "_load_shard", lambda slug, ch: changed)

    timestamps_jobs.complete_timestamps_job("rec_a", "job-new")

    got = repo_ts_reports.get(row["id"])
    assert got is not None and got["stale"] is True


def test_complete_noop_when_not_marked_ready(monkeypatch):
    from services.state import state as state_service
    from tests.conftest import _seed_state

    _seed_state("rec_a", state="under_review", assignee_hf_id="u-rev")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: True)

    out = timestamps_jobs.complete_timestamps_job("rec_a", "job-1")

    assert out["released"] is False
    # Pin the reason discriminator so a regression that takes a different
    # noop branch (e.g. ``no shards``) doesn't silently masquerade as this
    # one — the other noop tests assert their reason already.
    assert out["reason"] == "not marked-ready / wrong state"
    row = state_service.get_row("rec_a")
    assert row is not None
    assert row.state.value == "under_review"


def test_complete_refuses_when_no_shards(monkeypatch):
    from services.state import state as state_service

    _seed_marked_ready("rec_a")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: False)

    out = timestamps_jobs.complete_timestamps_job("rec_a", "job-1")

    assert out["released"] is False
    assert out["reason"] == "no shards"
    row = state_service.get_row("rec_a")
    assert row is not None
    assert row.state.value == "under_review"


def test_complete_is_idempotent_on_double_call(monkeypatch):
    from services.state import state as state_service

    _seed_marked_ready("rec_a")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: True)

    first = timestamps_jobs.complete_timestamps_job("rec_a", "job-1")
    second = timestamps_jobs.complete_timestamps_job("rec_a", "job-1")

    assert first["released"] is True
    assert second["released"] is False  # already released → no-op
    row = state_service.get_row("rec_a")
    assert row is not None
    assert row.state.value == "released"


def test_complete_unknown_slug_is_noop():
    out = timestamps_jobs.complete_timestamps_job("nope", "job-1")
    assert out["released"] is False
    assert out["reason"] == "unknown slug"
    # state must be None for unknown slugs — pin so a regression that
    # synthesizes a phantom state can't slip through.
    assert out["state"] is None


# ---------------------------------------------------------------------------
# Job completion → publish / state
# ---------------------------------------------------------------------------


def test_complete_publishes_marked_ready_reciter(monkeypatch):
    """A successful job on a marked-ready reciter auto-releases it (the
    Ready-to-generate → Publish-to-HF transition)."""
    _seed_marked_ready("rec_a")
    monkeypatch.setattr(timestamps_jobs, "_has_any_shard", lambda slug: True)

    out = timestamps_jobs.complete_timestamps_job("rec_a", "job-1")
    assert out["released"] is True


def test_failure_leaves_reciter_under_review():
    from services.state import state as state_service

    _seed_marked_ready("rec_a")
    timestamps_jobs.note_timestamps_job_failed("rec_a")
    row = state_service.get_row("rec_a")
    assert row is not None
    assert row.state.value == "under_review"  # no publish


def _running_record(started_at: str) -> dict:
    return {
        "job_id": "j9",
        "slug": "r",
        "type": "ts",
        "status": "running",
        "started_at": started_at,
    }


def _stub_newest(monkeypatch, rec: dict | None) -> None:
    monkeypatch.setattr(timestamps_jobs, "_newest_ts_record", lambda slug: rec)


def test_running_job_for_reports_a_fresh_run(monkeypatch):
    import datetime

    fresh = datetime.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _stub_newest(monkeypatch, _running_record(fresh))
    assert timestamps_jobs.running_job_for("r") == "j9"


def test_running_job_for_ignores_a_stale_run(monkeypatch):
    """A ``running`` record the Space never stamped terminal must not wedge the
    single-flight guard forever — past the ceiling it reads as dead."""
    import datetime

    old = (datetime.datetime.now(UTC) - datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _stub_newest(monkeypatch, _running_record(old))
    assert timestamps_jobs.running_job_for("r") is None


def test_stale_ceiling_is_env_overridable(monkeypatch):
    import datetime

    old = (datetime.datetime.now(UTC) - datetime.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _stub_newest(monkeypatch, _running_record(old))
    monkeypatch.setenv("INSPECTOR_TS_STALE_RUN_HOURS", "24")
    assert timestamps_jobs.running_job_for("r") == "j9"
