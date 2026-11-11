"""Frozen title strings for per-user notifications.

Recitation notifications name their reciter; release titles name the version
and count the published recitations or excluded chapters. Titles are computed
at emit time and stored verbatim on the row. Kept in one module so the wording
is easy to audit and unit-test.
"""

from __future__ import annotations


def request_sent_back(name: str) -> str:
    return f"Your request for {name} was sent back"


def request_discarded(name: str) -> str:
    return f"Your request for {name} was discarded"


def ready_for_review(name: str) -> str:
    return f"{name} is ready for review"


def assigned(name: str) -> str:
    return f"You've been assigned to {name}"


def force_released(name: str) -> str:
    return f"Your review of {name} was released — it hadn't been active for a while"


def submission_sent_back(name: str) -> str:
    return f"Your submission for {name} was sent back"


def submission_discarded(name: str) -> str:
    return f"Your submission for {name} was discarded"


def flag_reply(name: str) -> str:
    return f"New reply on a segment you flagged in {name}"


# --- Owner-facing review alerts (gated by notifications.receive_review_alerts) ---


def request_received(name: str) -> str:
    return f"New request · {name}"


def marked_ready_with_notes(name: str) -> str:
    return f"{name} marked ready — reviewer left notes"


def flag_created(name: str) -> str:
    return f"New flag on {name}"


def flag_replied(name: str) -> str:
    return f"New reply on a flag · {name}"


def ts_report_reported(name: str) -> str:
    return f"Timestamps issue reported · {name}"


def shard_missing(name: str) -> str:
    return f"Timestamps data missing · {name}"


def release_cut(version: str, count: int) -> str:
    return f"GitHub release {version} published · {count} recitations"


def release_cut_dropped(version: str, chapters: int) -> str:
    return f"GitHub release {version} published · {chapters} chapter(s) left out"


def release_cut_failed() -> str:
    return "GitHub release cut failed"


# --- User-facing: report resolution ---


def ts_report_resolved(name: str) -> str:
    return f"Your timestamps report for {name} was resolved"


def ts_report_auto_resolved(name: str) -> str:
    return f"A timestamps report auto-resolved on regen · {name}"
