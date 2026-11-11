"""Per-client limits and escalating bans for the timestamps data routes."""

from __future__ import annotations

import pytest

from services.auth import ts_rate_limit
from services.auth.ts_rate_limit import BAN_STEPS_S, HOUR_S, MINUTE_S, STRIKE_MEMORY_S

CLIENT = "198.51.100.4"
OTHER_CLIENT = "198.51.100.5"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def limiter(clock):
    limits = ts_rate_limit.Limits(
        chapters_per_minute=3, chapters_per_hour=5, requests_per_minute=10, tokens_per_hour=2
    )
    return ts_rate_limit.TsRateLimiter(limits, clock=clock)


def _open(limiter, count: int, *, start: int = 1) -> list[str | None]:
    return [limiter.record_chapter(CLIENT, f"r/{ch}") for ch in range(start, start + count)]


def test_distinct_chapters_in_a_minute_break_the_limit(limiter):
    assert _open(limiter, 4) == [None, None, None, "chapters_per_minute"]
    assert limiter.ban_remaining(CLIENT) == BAN_STEPS_S[0]


def test_a_slow_sweep_breaks_the_hourly_limit(limiter, clock):
    broken = []
    for ch in range(1, 7):
        broken.append(limiter.record_chapter(CLIENT, f"r/{ch}"))
        clock.now += MINUTE_S

    assert broken == [None] * 5 + ["chapters_per_hour"]


def test_chapters_older_than_an_hour_no_longer_count(limiter, clock):
    for ch in range(1, 6):
        limiter.record_chapter(CLIENT, f"r/{ch}")
        clock.now += MINUTE_S
    clock.now += HOUR_S

    assert limiter.record_chapter(CLIENT, "r/6") is None


def test_one_chapter_read_again_and_again_only_hits_the_request_limit(limiter):
    broken = [limiter.record_chapter(CLIENT, "r/1") for _ in range(11)]

    assert broken == [None] * 10 + ["requests_per_minute"]


def test_token_mints_are_limited(limiter):
    assert [limiter.record_token(CLIENT) for _ in range(3)] == [None, None, "tokens_per_hour"]


def test_bans_grow_with_each_offence_and_cap(limiter, clock):
    lengths = []
    for _ in range(len(BAN_STEPS_S) + 1):
        _open(limiter, 4)
        lengths.append(limiter.ban_remaining(CLIENT))
        clock.now += lengths[-1]

    assert lengths == [*BAN_STEPS_S, BAN_STEPS_S[-1]]


def test_an_old_offence_is_forgotten(limiter, clock):
    _open(limiter, 4)
    clock.now += STRIKE_MEMORY_S + 1

    _open(limiter, 4)

    assert limiter.ban_remaining(CLIENT) == BAN_STEPS_S[0]


def test_a_banned_client_is_not_counted_again(limiter):
    _open(limiter, 4)

    assert _open(limiter, 4, start=10) == [None] * 4
    assert limiter.ban_remaining(CLIENT) == BAN_STEPS_S[0]


def test_clients_are_counted_apart(limiter):
    _open(limiter, 4)

    assert limiter.ban_remaining(OTHER_CLIENT) == 0
    assert limiter.record_chapter(OTHER_CLIENT, "r/1") is None


@pytest.mark.parametrize(
    ("addr", "key"),
    [
        ("198.51.100.4", "198.51.100.4"),
        ("::ffff:198.51.100.4", "198.51.100.4"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("not-an-address", "unknown"),
        (None, "unknown"),
    ],
)
def test_client_key_groups_an_ipv6_subscriber(addr, key):
    assert ts_rate_limit.client_key(addr) == key
