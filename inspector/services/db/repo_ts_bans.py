"""``ts_bans`` read/write — clients banned from the timestamps data routes.

One row per client (IPv4 address or IPv6 /64), times in Unix seconds. The
caller owns the transaction (``services/auth/ts_bans.py`` wraps the write in
``sync.durable_transaction``).
"""

from __future__ import annotations

from .connection import get_conn


def upsert(
    client: str, *, banned_until: float, strikes: int, last_strike: float, reason: str
) -> None:
    get_conn().execute(
        "INSERT INTO ts_bans(client, banned_until, strikes, last_strike, reason) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(client) DO UPDATE SET "
        "  banned_until = excluded.banned_until, "
        "  strikes      = excluded.strikes, "
        "  last_strike  = excluded.last_strike, "
        "  reason       = excluded.reason",
        (client, banned_until, strikes, last_strike, reason),
    )


def remembered(*, now: float, struck_after: float) -> list[dict]:
    """Every ban still running at ``now``, plus every expired one whose offence
    still counts (``last_strike`` after ``struck_after``)."""
    rows = (
        get_conn()
        .execute(
            "SELECT client, banned_until, strikes, last_strike, reason "
            "FROM ts_bans WHERE banned_until > ? OR last_strike > ? ORDER BY client",
            (now, struck_after),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def delete(client: str) -> bool:
    """Lift ``client``'s ban and forget its strikes. True when a row was removed."""
    return get_conn().execute("DELETE FROM ts_bans WHERE client = ?", (client,)).rowcount > 0
