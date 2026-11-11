-- Inspector SQLite substrate — migration 0034.
--
-- Clients banned from the timestamps data routes (`/api/ts/*`) for pulling in
-- bulk (`services/auth/ts_rate_limit.py`). One row per client: an IPv4 address
-- or an IPv6 /64. Bans last a day to a month, so they have to outlive a
-- restart; the request counters that lead to one stay in memory.
--
-- Times are Unix seconds (compared against the wall clock on every request).
-- `strikes` is how many times the client has been banned inside the strike
-- memory; it picks the length of the next ban.

CREATE TABLE ts_bans (
    client       TEXT PRIMARY KEY,
    banned_until REAL NOT NULL,
    strikes      INTEGER NOT NULL,
    last_strike  REAL NOT NULL,
    reason       TEXT NOT NULL
);
