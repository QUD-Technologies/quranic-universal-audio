"""The shard-integrity watchdog — the daily sweep evaluator.

Unlike the six release automations this one **takes no action and has no config
toggle**: it only looks and reports. There is nothing to tune and no job cost, so
making it opt-in would only mean the safety net is off exactly when it matters.
The single knob is the ``notifications.receive_integrity_alerts`` capability —
revoke it from the Permissions tab and the cards stop.

It also cannot live in the GH cut: that runs on demand, covers only
release-eligible deliveries, and reports a lost chapter as an ordinary "missing
coverage" line, so a gap can sit unnoticed until someone reads a changelog.

Cadence is daily rather than per-tick because the sweep lists two directories per
delivery; at the 60 s tick that would be thousands of pointless listings an hour.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from qua_shared.schemas import AutomationConfig
from services.admin.jobs import base as jobs_base
from services.db import _serde, get_conn, repo_align_runs, repo_automation
from services.db.sync import durable_transaction
from services.notifications import emit as notifications_emit
from services.storage import shard_integrity

logger = logging.getLogger(__name__)

SHARD_INTEGRITY = "shard_integrity"

#: One sweep per day. The failure it catches is rare and never urgent-to-the-hour
#: (the data is already gone; the orphan copy does not expire).
SWEEP_INTERVAL = timedelta(hours=24)


def _delivery_slugs() -> list[str]:
    rows = get_conn().execute("SELECT slug FROM deliveries ORDER BY slug").fetchall()
    return [r[0] for r in rows]


def _busy_slugs() -> set[str]:
    """Deliveries whose shards are still being written: an align run in progress
    or a timestamps run on the Space. Mid-run, audio exists for every chapter but
    shards for only some — progress, not a lost shard."""
    running_ts = {
        j["slug"] for j in jobs_base.list_in_flight_jobs(("timestamps",)) if j.get("slug")
    }
    return running_ts | repo_align_runs.active_slugs()


def _due(now: datetime) -> bool:
    st = repo_automation.get_state(SHARD_INTEGRITY)
    last_run = _serde.from_iso(st["last_run_at"]) if st else None
    return last_run is None or (now - last_run) >= SWEEP_INTERVAL


def eval_shard_integrity(_cfg: AutomationConfig, now: datetime) -> None:
    """Sweep every idle delivery for vanished timestamps shards; notify on new
    findings and archive the cards of shards that have come back.

    Always enabled — see the module docstring. Records the run (advancing the
    cadence) whether or not anything was found, so a clean sweep still shows a
    fresh ``last_run_at`` in the Automation card.
    """
    if not _due(now):
        return

    busy = _busy_slugs()
    slugs = [s for s in _delivery_slugs() if s not in busy]
    findings, unreadable = shard_integrity.scan(slugs)
    notifications_emit.notify_owners_shard_integrity(findings)
    notifications_emit.resolve_shard_integrity(
        scanned=[s for s in slugs if s not in unreadable], findings=findings
    )

    if findings:
        recoverable = sum(1 for f in findings if f.kind == "orphan_temp")
        detail = (
            f"{len(findings)} missing shard(s) across "
            f"{len({f.slug for f in findings})} delivery(ies); "
            f"{recoverable} recoverable"
        )
        status = "acted"
        logger.warning(
            "shard_integrity: %s — %s",
            detail,
            ", ".join(f"{f.slug}:{f.chapter}({f.kind})" for f in findings[:10]),
        )
    else:
        detail = "no missing shards"
        status = "skipped"
    if unreadable:
        # Surfaced in the run detail, never as findings — "could not look" is not
        # "the data is gone".
        detail += f"; {len(unreadable)} delivery(ies) unreadable"

    with durable_transaction() as _:
        repo_automation.record_run(
            SHARD_INTEGRITY, status=status, detail=detail[:200], last_run_at=now, now=now
        )
