"""``regen_timestamps_offline`` — a run that succeeds is reported; any other outcome is not."""

from __future__ import annotations

from types import SimpleNamespace

from regen_timestamps_offline import regen_one


def _deps(status: str, notified: list, accepted: bool = True) -> dict:
    def run(record, _riwayah, _full):
        record.status = status

    return {
        "new_record": lambda slug, chs: SimpleNamespace(slug=slug, status="running", error=None),
        "run": run,
        "riwayah": lambda slug: "hafs",
        "notify": lambda slug, chs: notified.append((slug, chs)) or accepted,
    }


def test_a_successful_run_is_reported_with_its_chapters():
    notified: list = []
    assert regen_one("r", [1], False, _deps("succeeded", notified)) == "succeeded"
    assert notified == [("r", [1])]


def test_a_failed_run_is_not_reported():
    notified: list = []
    assert regen_one("r", None, False, _deps("failed", notified)) == "failed"
    assert notified == []


def test_a_refused_report_is_its_own_outcome():
    assert regen_one("r", None, False, _deps("succeeded", [], accepted=False)) == "unreported"
