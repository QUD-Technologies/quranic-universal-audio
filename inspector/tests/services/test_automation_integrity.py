"""The shard-integrity watchdog evaluator — cadence, notification, run record.

The sweep itself is covered in ``test_shard_integrity.py``; here it is
monkeypatched so the tests are about *when* the evaluator runs, *who* gets told,
and *what* it records.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qua_shared.schemas import AutomationConfig
from services.admin.automation import evaluators, integrity
from services.db import repo_automation
from services.storage.shard_integrity import IntegrityFinding


def _now() -> datetime:
    return datetime(2026, 6, 9, 12, 0, tzinfo=UTC)


def _state() -> dict:
    """The watchdog's ``automation_state`` row — the durability boundary these
    tests assert on, so its absence is a failure, not a None to thread."""
    st = repo_automation.get_state(integrity.SHARD_INTEGRITY)
    assert st is not None, "the watchdog recorded no run"
    return st


@pytest.fixture
def sweep(monkeypatch):
    """Stub the sweep + the emitter; return the recorded notify calls."""
    calls: list[list] = []
    monkeypatch.setattr(integrity, "_delivery_slugs", lambda: ["r"])
    monkeypatch.setattr(
        integrity.notifications_emit,
        "notify_owners_shard_integrity",
        lambda findings: calls.append(list(findings)) or len(findings),
    )

    def _set(findings, unreadable=()):
        monkeypatch.setattr(
            integrity.shard_integrity, "scan", lambda slugs: (list(findings), list(unreadable))
        )

    _set([])
    return type("S", (), {"calls": calls, "set": staticmethod(_set)})


def test_clean_sweep_records_a_run_and_notifies_nobody(sweep):
    integrity.eval_shard_integrity(AutomationConfig(), _now())

    st = _state()
    assert st["last_status"] == "skipped"
    assert st["last_detail"] == "no missing shards"
    assert sweep.calls == [[]]


def test_findings_notify_and_are_summarised_in_the_run_detail(sweep):
    sweep.set(
        [
            IntegrityFinding(slug="r", chapter=102, kind="orphan_temp", orphan_path="p"),
            IntegrityFinding(slug="r", chapter=45, kind="missing_shard"),
        ]
    )

    integrity.eval_shard_integrity(AutomationConfig(), _now())

    assert [(f.slug, f.chapter) for f in sweep.calls[0]] == [("r", 102), ("r", 45)]
    st = _state()
    assert st["last_status"] == "acted"
    assert "2 missing shard(s)" in st["last_detail"]
    assert "1 recoverable" in st["last_detail"]


def test_unreadable_deliveries_are_noted_without_becoming_findings(sweep):
    sweep.set([], unreadable=["broken"])

    integrity.eval_shard_integrity(AutomationConfig(), _now())

    st = _state()
    assert "1 delivery(ies) unreadable" in st["last_detail"]
    assert sweep.calls == [[]]


def test_second_tick_same_day_does_not_re_sweep(sweep):
    now = _now()
    integrity.eval_shard_integrity(AutomationConfig(), now)
    sweep.calls.clear()

    integrity.eval_shard_integrity(AutomationConfig(), now + timedelta(minutes=1))

    assert sweep.calls == [], "the sweep is daily, not per-tick"


def test_sweep_runs_again_after_the_interval(sweep):
    now = _now()
    integrity.eval_shard_integrity(AutomationConfig(), now)
    sweep.calls.clear()

    integrity.eval_shard_integrity(AutomationConfig(), now + integrity.SWEEP_INTERVAL)

    assert len(sweep.calls) == 1


def test_watchdog_needs_no_config_and_is_registered(sweep):
    """It has no enable flag by design — a default (all-disabled) config still
    sweeps, and the engine actually runs it."""
    integrity.eval_shard_integrity(AutomationConfig(), _now())

    assert _state()["last_run_at"] is not None
    assert integrity.eval_shard_integrity in evaluators.EVALUATORS
