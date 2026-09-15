"""Shard-integrity owner alerts — real notification rows, real dedup.

Gated by ``notifications.receive_integrity_alerts`` (owner-only by default),
separately from the review alerts.
"""

from __future__ import annotations

from services.db import repo_notifications
from services.notifications import emit
from services.storage.shard_integrity import FindingKind, IntegrityFinding


def _finding(chapter: int = 102, kind: FindingKind = "orphan_temp", slug: str = "r"):
    return IntegrityFinding(
        slug=slug,
        chapter=chapter,
        kind=kind,
        orphan_path=f"reciters/{slug}/timestamps/.{chapter}.json.br.abc"
        if kind == "orphan_temp"
        else None,
    )


def test_findings_land_as_a_card_for_every_owner(seed_role):
    seed_role("owner-1", role="owner")
    seed_role("owner-2", role="owner")

    emit.notify_owners_shard_integrity([_finding()])

    for oid in ("owner-1", "owner-2"):
        rows = repo_notifications.list_active(oid)
        assert len(rows) == 1, oid
        assert rows[0]["event"] == "shard.missing"
        assert rows[0]["slug"] == "r"
        assert "recoverable" in rows[0]["body"]


def test_an_unrepaired_gap_notifies_once_not_every_sweep(seed_role):
    """The whole point of the finding's stable ``source_key``."""
    seed_role("owner-1", role="owner")

    emit.notify_owners_shard_integrity([_finding()])
    emit.notify_owners_shard_integrity([_finding()])

    assert len(repo_notifications.list_active("owner-1")) == 1


def test_unrecoverable_finding_says_it_needs_a_realign(seed_role):
    seed_role("owner-1", role="owner")

    emit.notify_owners_shard_integrity([_finding(chapter=45, kind="missing_shard")])

    row = repo_notifications.list_active("owner-1")[0]
    assert "re-align" in row["body"]
    assert row["payload"]["chapter"] == 45
    assert row["payload"]["orphan_path"] is None


def test_contributors_and_maintainers_are_not_alerted(seed_role):
    """Owner-only by default — an infrastructure alarm is not review load."""
    seed_role("maintainer-1", role="maintainer")
    seed_role("contributor-1", role="contributor")

    emit.notify_owners_shard_integrity([_finding()])

    assert repo_notifications.list_active("maintainer-1") == []
    assert repo_notifications.list_active("contributor-1") == []


def test_no_findings_writes_nothing(seed_role):
    seed_role("owner-1", role="owner")

    assert emit.notify_owners_shard_integrity([]) == 0
    assert repo_notifications.list_active("owner-1") == []
