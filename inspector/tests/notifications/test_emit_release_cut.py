"""GitHub release cut owner alerts — success, held recitations, failure."""

from __future__ import annotations

from services.db import repo_notifications
from services.notifications import emit

HELD = {"husary_mujawwad": ["ch62 recording (465s → 321s)"]}


def test_a_clean_cut_tells_owners_it_was_published(seed_role):
    seed_role("owner-1", role="owner")

    emit.notify_owners_release_cut(job_id="j1", version="v3.2.0", recitation_count=70)

    (row,) = repo_notifications.list_active("owner-1")
    assert row["event"] == "release.cut"
    assert row["title"] == "GitHub release v3.2.0 published · 70 recitations"
    assert row["body"] is None


def test_held_recitations_are_listed_with_their_chapters(seed_role):
    seed_role("owner-1", role="owner")

    emit.notify_owners_release_cut(job_id="j2", version="v3.2.0", recitation_count=69, held=HELD)

    (row,) = repo_notifications.list_active("owner-1")
    assert row["title"] == "GitHub release v3.2.0 published · 1 recitation(s) held back"
    assert "husary_mujawwad" in row["body"] and "ch62 recording (465s → 321s)" in row["body"]
    assert row["payload"]["held"] == HELD


def test_a_failed_cut_notifies_once_per_job(seed_role):
    seed_role("owner-1", role="owner")

    for _ in range(2):
        emit.notify_owners_release_cut(job_id="j3", version=None, held=HELD, failed=True)

    (row,) = repo_notifications.list_active("owner-1")
    assert row["event"] == "release.failed"
    assert row["title"] == "GitHub release cut failed"
