"""Bans from the timestamps data routes, kept across restarts.

``services/auth/ts_rate_limit.py`` decides a ban in memory; this module writes
it to ``ts_bans`` and puts the remembered ones back into a limiter at boot. A
ban is rare (one write per offending client), so the full-database bucket sync
it triggers is affordable where a write per request would not be.
"""

from __future__ import annotations

import logging
import time

from services.db import repo_ts_bans
from services.db.sync import durable_transaction

from .ts_rate_limit import STRIKE_MEMORY_S, BanState, TsRateLimiter

logger = logging.getLogger("inspector")


def save(limiter: TsRateLimiter, client: str, reason: str) -> None:
    """Persist ``client``'s current ban. A failed write is logged, not raised:
    the ban still holds in memory until the next restart."""
    ban = limiter.ban_state(client)
    if ban is None:
        return
    try:
        with durable_transaction():
            repo_ts_bans.upsert(
                client,
                banned_until=ban.banned_until,
                strikes=ban.strikes,
                last_strike=ban.last_strike,
                reason=reason,
            )
    except Exception:
        logger.exception("ts ban for %s was not persisted", client)


def load_into(limiter: TsRateLimiter) -> int:
    """Restore every remembered ban into ``limiter``. Returns how many."""
    rows = repo_ts_bans.remembered(struck_after=time.time() - STRIKE_MEMORY_S)
    for row in rows:
        limiter.restore(
            row["client"], BanState(row["banned_until"], row["strikes"], row["last_strike"])
        )
    return len(rows)


def listing() -> list[dict]:
    """Every remembered ban, for the owner's view."""
    return repo_ts_bans.remembered(struck_after=time.time() - STRIKE_MEMORY_S)


def lift(limiter: TsRateLimiter, client: str) -> bool:
    """Unban ``client`` now and forget its strikes. True when it had a record."""
    with durable_transaction():
        removed = repo_ts_bans.delete(client)
    limiter.restore(client, BanState(0.0, 0, 0.0))
    return removed
