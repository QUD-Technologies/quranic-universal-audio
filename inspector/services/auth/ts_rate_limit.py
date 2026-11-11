"""Per-client limits and temporary bans for the timestamps data routes.

A reader opens a handful of chapters; a bulk pull opens hundreds. Each client
(an IPv4 address, or an IPv6 /64) may open a bounded number of *distinct*
chapters and mint a bounded number of access tokens; going over bans the client
for a while, longer on each repeat.

State is in memory on purpose: the app is single-worker, and a SQLite write per
request would sync the whole database to the bucket. A restart forgets bans.

This slows and exposes bulk pulls; it cannot stop a scraper that spreads a slow
crawl over many addresses. Pure and Flask-free: the glue is
``routes/timestamps/access.py``.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

MINUTE_S = 60
HOUR_S = 3600

# Ban length for a client's 1st, 2nd and later offences.
BAN_STEPS_S = (15 * MINUTE_S, HOUR_S, 24 * HOUR_S)
# Offences older than this no longer lengthen the next ban.
STRIKE_MEMORY_S = 24 * HOUR_S
# Forget idle clients every this many calls.
_SWEEP_EVERY = 2000
_IPV6_CLIENT_PREFIX = 64


@dataclass(frozen=True)
class Limits:
    chapters_per_minute: int
    chapters_per_hour: int
    requests_per_minute: int
    tokens_per_hour: int


@dataclass
class _Client:
    # chapter key -> when this client first opened it inside the current hour
    chapters: dict[str, float] = field(default_factory=dict)
    requests: deque[float] = field(default_factory=deque)
    tokens: deque[float] = field(default_factory=deque)
    strikes: int = 0
    last_strike: float = 0.0
    banned_until: float = 0.0
    last_seen: float = 0.0


def client_key(remote_addr: str | None) -> str:
    """The unit limits and tokens are tied to: an IPv4 address or an IPv6 /64.

    One subscriber owns a whole IPv6 /64, so counting single addresses there
    would let a script walk the prefix.
    """
    try:
        ip = ipaddress.ip_address((remote_addr or "").strip())
    except ValueError:
        return "unknown"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network((ip, _IPV6_CLIENT_PREFIX), strict=False))
    return str(ip)


def _drop_older(times: deque[float], cutoff: float) -> None:
    while times and times[0] <= cutoff:
        times.popleft()


class TsRateLimiter:
    """Counts chapter opens and token mints per client; bans a client that goes over."""

    def __init__(self, limits: Limits, *, clock: Callable[[], float] = time.monotonic):
        self._limits = limits
        self._clock = clock
        self._lock = threading.Lock()
        self._clients: dict[str, _Client] = {}
        self._calls = 0

    def reset(self) -> None:
        with self._lock:
            self._clients.clear()

    def ban_remaining(self, client: str) -> int:
        """Seconds left on ``client``'s ban, 0 when it is not banned."""
        with self._lock:
            state = self._clients.get(client)
            if state is None:
                return 0
            return max(0, int(state.banned_until - self._clock() + 0.999))

    def record_chapter(self, client: str, chapter_key: str) -> str | None:
        """Count a chapter (or verse) read. Returns the limit it broke, else ``None``."""
        with self._lock:
            now, state = self._touch(client)
            if state.banned_until > now:
                return None
            _drop_older(state.requests, now - MINUTE_S)
            state.requests.append(now)
            for key in [k for k, t in state.chapters.items() if t <= now - HOUR_S]:
                del state.chapters[key]
            state.chapters.setdefault(chapter_key, now)
            in_minute = sum(1 for t in state.chapters.values() if t > now - MINUTE_S)
            broken = None
            if len(state.requests) > self._limits.requests_per_minute:
                broken = "requests_per_minute"
            elif in_minute > self._limits.chapters_per_minute:
                broken = "chapters_per_minute"
            elif len(state.chapters) > self._limits.chapters_per_hour:
                broken = "chapters_per_hour"
            if broken:
                self._ban(state, now)
            return broken

    def record_token(self, client: str) -> str | None:
        """Count an access-token mint. Returns the limit it broke, else ``None``."""
        with self._lock:
            now, state = self._touch(client)
            if state.banned_until > now:
                return None
            _drop_older(state.tokens, now - HOUR_S)
            state.tokens.append(now)
            if len(state.tokens) <= self._limits.tokens_per_hour:
                return None
            self._ban(state, now)
            return "tokens_per_hour"

    def _touch(self, client: str) -> tuple[float, _Client]:
        now = self._clock()
        self._calls += 1
        if self._calls % _SWEEP_EVERY == 0:
            self._sweep(now)
        state = self._clients.setdefault(client, _Client())
        state.last_seen = now
        return now, state

    def _ban(self, state: _Client, now: float) -> None:
        if now - state.last_strike > STRIKE_MEMORY_S:
            state.strikes = 0
        step = BAN_STEPS_S[min(state.strikes, len(BAN_STEPS_S) - 1)]
        state.strikes += 1
        state.last_strike = now
        state.banned_until = now + step
        # A fresh window after the ban, so one burst is one offence.
        state.chapters.clear()
        state.requests.clear()
        state.tokens.clear()

    def _sweep(self, now: float) -> None:
        idle = [
            key
            for key, state in self._clients.items()
            if state.banned_until <= now
            and now - state.last_seen > HOUR_S
            and (state.strikes == 0 or now - state.last_strike > STRIKE_MEMORY_S)
        ]
        for key in idle:
            del self._clients[key]
