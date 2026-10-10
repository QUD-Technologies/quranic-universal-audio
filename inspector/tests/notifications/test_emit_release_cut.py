"""Release-cut owner alerts: publication, excluded chapters and compatible callbacks."""

from __future__ import annotations

import pytest

from services.db import repo_notifications
from services.notifications import emit

DROPPED = {"husary_mujawwad": ["ch62 recording (465s → 321s)"]}


def test_a_clean_cut_tells_owners_it_was_published(seed_role):
    seed_role("owner-1", role="owner")

    emit.notify_owners_release_cut(job_id="j1", version="v3.2.0", recitation_count=70)

    (row,) = repo_notifications.list_active("owner-1")
    assert row["event"] == "release.cut"
    assert row["title"] == "GitHub release v3.2.0 published · 70 recitations"
    assert row["body"] is None


def test_dropped_chapters_are_listed_with_their_recitations(seed_role):
    seed_role("owner-1", role="owner")

    emit.notify_owners_release_cut(
        job_id="j2", version="v3.2.0", recitation_count=70, dropped=DROPPED
    )

    (row,) = repo_notifications.list_active("owner-1")
    assert row["title"] == "GitHub release v3.2.0 published · 1 chapter(s) left out"
    assert "husary_mujawwad" in row["body"] and "ch62 recording (465s → 321s)" in row["body"]
    assert "left out of the release because their upstream audio changed" in row["body"]
    assert row["payload"]["dropped_upstream_chapters"] == DROPPED


def test_a_failed_cut_notifies_once_per_job(seed_role):
    seed_role("owner-1", role="owner")

    for _ in range(2):
        emit.notify_owners_release_cut(job_id="j3", version=None, dropped=DROPPED, failed=True)

    (row,) = repo_notifications.list_active("owner-1")
    assert row["event"] == "release.failed"
    assert row["title"] == "GitHub release cut failed"


def test_published_cut_counts_chapters_and_deduplicates_by_job(seed_role):
    seed_role("owner-1", role="owner")
    dropped = {"rec-a": ["ch1 changed", "ch2 changed"], "rec-b": ["ch62 changed"]}
    for _ in range(2):
        emit.notify_owners_release_cut(job_id="j4", version="v3.2.0", dropped=dropped)
    (row,) = repo_notifications.list_active("owner-1")
    assert row["title"] == "GitHub release v3.2.0 published · 3 chapter(s) left out"
    assert "rec-a" in row["body"] and "rec-b" in row["body"]
    assert "ch1 changed, ch2 changed" in row["body"]


@pytest.mark.parametrize("status", ["succeeded", "failed"])
@pytest.mark.parametrize(
    "summary",
    [
        {"dropped_upstream_chapters": DROPPED},
        {"held_upstream_changes": DROPPED},
    ],
)
def test_callback_accepts_both_summary_keys(flask_client, seed_role, monkeypatch, status, summary):
    from services.admin.jobs import records
    from services.email import emit as email_emit

    seed_role("owner-1", role="owner")
    monkeypatch.setenv("INSPECTOR_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(records, "record_terminal", lambda *a, **kw: None)
    monkeypatch.setattr(email_emit, "emit_github_release", lambda **kw: None)
    for _ in range(2):
        response = flask_client.post(
            "/api/webhooks/release-cut-complete",
            json={
                "job_id": "callback-1",
                "status": status,
                "version": "v3.2.0",
                "members": [],
                "validation_summary": summary,
            },
            headers={"X-Inspector-Job-Secret": "test-secret"},
        )
        assert response.status_code == 200
    (row,) = repo_notifications.list_active("owner-1")
    assert row["payload"]["dropped_upstream_chapters"] == DROPPED
    assert "husary_mujawwad" in row["body"] and "ch62" in row["body"]
    assert row["event"] == ("release.cut" if status == "succeeded" else "release.failed")


def test_empty_dropped_summary_takes_precedence_over_old_key(seed_role, monkeypatch):
    from services.admin.jobs import cut_release, records
    from services.email import emit as email_emit

    seed_role("owner-1", role="owner")
    monkeypatch.setattr(records, "record_terminal", lambda *a, **kw: None)
    monkeypatch.setattr(email_emit, "emit_github_release", lambda **kw: None)
    cut_release.complete(
        None,
        "clean-callback",
        version="v3.2.0",
        validation_summary={"dropped_upstream_chapters": {}, "held_upstream_changes": DROPPED},
    )
    (row,) = repo_notifications.list_active("owner-1")
    assert row["title"] == "GitHub release v3.2.0 published · 0 recitations"
    assert row["body"] is None
