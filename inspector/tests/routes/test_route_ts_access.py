"""Browser-only /api/ts/* access: Fetch Metadata gate, access token, limits, edge secret."""

from __future__ import annotations

import pytest
from routes.timestamps import access
from routes.timestamps import timestamps as ts_routes

from services.auth import ts_access, ts_rate_limit, turnstile
from services.db import repo_ts_bans

SHARD = "/api/ts/shard/reciter_a/2"
VERSE = "/api/ts/verse/reciter_a/2"
CONFIG = "/api/ts/config"
CLIENT = "127.0.0.1"
OTHER_CLIENT = "203.0.113.7"
EDGE_SECRET = "edge-secret-value"
SAME_ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def page_without_token(flask_client):
    """The site's own page before it has fetched an access token."""
    flask_client.environ_base.pop("HTTP_X_QUA_ACCESS", None)
    return flask_client


@pytest.fixture
def raw_client(page_without_token):
    """Test client with NO browser headers — what a script sends."""
    page_without_token.environ_base.pop("HTTP_SEC_FETCH_SITE", None)
    return page_without_token


@pytest.fixture
def shard_body(monkeypatch):
    """Every chapter serves these bytes."""
    body = b"brotli-shard-bytes"
    monkeypatch.setattr(ts_routes.ts_serve, "shard_bytes", lambda *a, **k: body)
    return body


def _token(client) -> dict:
    return client.get("/api/ts/access-token").get_json()


@pytest.mark.parametrize(
    "path", [SHARD, "/api/ts/config", "/api/ts/manifest", "/api/ts/verse/reciter_a/2"]
)
def test_script_without_browser_headers_is_refused_with_notice(raw_client, path):
    res = raw_client.get(path, headers={"User-Agent": "ZakirunAudioArchiver/3.0"})

    assert res.status_code == 403
    body = res.get_json()
    assert body["error"] == "unlicensed_access"
    assert "not licensed and not allowed" in body["notice"]
    assert body["releases"] == list(ts_access.DATA_RELEASES)
    assert res.headers["tdm-reservation"] == "1"


@pytest.mark.parametrize("site", ["cross-site", "none"])
def test_cross_site_and_address_bar_fetches_are_refused(raw_client, site):
    res = raw_client.get(CONFIG, headers={"Sec-Fetch-Site": site})
    assert res.status_code == 403


def test_same_site_qud_dev_page_is_allowed(raw_client):
    res = raw_client.get(CONFIG, headers={"Sec-Fetch-Site": "same-site"})
    assert res.status_code == 200
    assert res.headers["X-Robots-Tag"] == "noindex, noai, noimageai"


@pytest.mark.parametrize(
    ("headers", "ok"),
    [
        ({"Origin": "http://localhost"}, True),
        ({"Referer": "https://qud.dev/audio"}, True),
        ({"Origin": "https://evil.example"}, False),
        ({"Referer": "https://notqud.dev/x"}, False),
    ],
)
def test_old_browser_falls_back_to_origin_or_referer(raw_client, headers, ok):
    res = raw_client.get(CONFIG, headers=headers)
    assert (res.status_code == 200) is ok


@pytest.mark.parametrize("path", [SHARD, VERSE])
def test_chapter_and_verse_need_access_token(page_without_token, shard_body, path):
    res = page_without_token.get(path)

    assert res.status_code == 401
    assert res.get_json()["reason"] == "access_token_missing_or_expired"


def test_shard_with_token_serves_bytes(page_without_token, shard_body):
    tok = _token(page_without_token)

    res = page_without_token.get(SHARD, headers={tok["header"]: tok["token"]})

    assert res.status_code == 200
    assert res.data == shard_body


def test_token_issued_to_another_client_is_refused(page_without_token, shard_body):
    tok = _token(page_without_token)

    res = page_without_token.get(
        SHARD,
        headers={tok["header"]: tok["token"]},
        environ_base={"REMOTE_ADDR": OTHER_CLIENT},
    )

    assert res.status_code == 401


def test_access_token_endpoint_is_browser_gated(raw_client):
    assert raw_client.get("/api/ts/access-token").status_code == 403


def test_token_expires_and_rejects_tampering():
    secret = "s" * 40
    token, expires_at = ts_access.issue_token(secret, CLIENT, now=1000)

    assert expires_at == 1000 + ts_access.TOKEN_TTL_SECONDS
    assert ts_access.token_valid(secret, token, CLIENT, now=1000)
    assert not ts_access.token_valid(secret, token, OTHER_CLIENT, now=1000)
    assert not ts_access.token_valid(secret, token, CLIENT, now=expires_at)
    assert not ts_access.token_valid("other-secret" * 4, token, CLIENT, now=1000)
    forged_head = ts_access._b64(str(expires_at + 10_000).encode())
    forged = f"{forged_head}.{token.split('.')[1]}"
    assert not ts_access.token_valid(secret, forged, CLIENT, now=1000)
    assert not ts_access.token_valid(secret, "garbage", CLIENT, now=1000)
    assert not ts_access.token_valid(secret, None, CLIENT, now=1000)


# ── Per-client limits ─────────────────────────────────────────────────────────


@pytest.fixture
def tight_limits(monkeypatch):
    """Enforced limits small enough to break: three chapters a minute, two tokens an hour."""
    limits = ts_rate_limit.Limits(
        chapters_per_minute=3,
        chapters_per_hour=100,
        chapters_per_day=100,
        requests_per_minute=100,
        tokens_per_hour=2,
    )
    monkeypatch.setattr(access, "limiter", ts_rate_limit.TsRateLimiter(limits))
    monkeypatch.setattr(access.config, "TS_RATE_LIMIT_MODE", "enforce")


@pytest.fixture
def unlimited(monkeypatch):
    """Every caller holds ``timestamps.unlimited_access``."""
    monkeypatch.setattr(
        access.capabilities, "can", lambda user, cap: cap == access.UNLIMITED_CAPABILITY
    )


def _sweep(client, count: int) -> list[int]:
    return [client.get(f"/api/ts/shard/reciter_a/{ch}").status_code for ch in range(1, count + 1)]


def test_a_chapter_sweep_gets_the_client_banned(flask_client, shard_body, tight_limits):
    assert _sweep(flask_client, 4) == [200, 200, 200, 429]

    res = flask_client.get(CONFIG)

    assert res.status_code == 429
    assert res.get_json()["reason"] == "too_many_requests"
    assert int(res.headers["Retry-After"]) > 0


def test_a_ban_is_persisted_and_survives_a_restart(flask_client, shard_body, tight_limits):
    _sweep(flask_client, 4)

    rows = repo_ts_bans.remembered(now=0, struck_after=0)
    assert [(r["client"], r["strikes"], r["reason"]) for r in rows] == [
        (CLIENT, 1, "chapters_per_minute")
    ]

    access.limiter.reset()  # what a restart does to the in-memory state
    access._bans_restored = False

    assert flask_client.get(CONFIG).status_code == 429


def test_holder_of_the_capability_can_list_and_lift_a_ban(
    flask_client, shard_body, tight_limits, signed_in_client
):
    owner, _ = signed_in_client(role="owner")
    _sweep(flask_client, 4)

    assert [b["client"] for b in owner.get("/api/ts/bans").get_json()["bans"]] == [CLIENT]
    lifted = owner.post("/api/ts/bans/lift", json={"client": CLIENT}, headers=SAME_ORIGIN)

    assert lifted.status_code == 200
    assert repo_ts_bans.remembered(now=0, struck_after=0) == []
    assert flask_client.get(CONFIG).status_code == 200


def test_anonymous_cannot_read_or_lift_bans(flask_client):
    assert flask_client.get("/api/ts/bans").status_code == 401
    lifted = flask_client.post("/api/ts/bans/lift", json={"client": CLIENT}, headers=SAME_ORIGIN)
    assert lifted.status_code == 401


def test_rereading_one_chapter_is_not_a_sweep(flask_client, shard_body, tight_limits):
    statuses = [flask_client.get(SHARD).status_code for _ in range(8)]

    assert statuses == [200] * 8


def test_a_ban_does_not_reach_another_client(flask_client, shard_body, tight_limits):
    _sweep(flask_client, 4)

    res = flask_client.get(CONFIG, environ_base={"REMOTE_ADDR": OTHER_CLIENT})

    assert res.status_code == 200


def test_minting_tokens_in_bulk_gets_the_client_banned(flask_client, tight_limits):
    statuses = [flask_client.get("/api/ts/access-token").status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


def test_log_mode_counts_but_never_refuses(flask_client, shard_body, tight_limits, monkeypatch):
    monkeypatch.setattr(access.config, "TS_RATE_LIMIT_MODE", "log")

    assert _sweep(flask_client, 6) == [200] * 6


def test_unlimited_capability_is_not_counted(flask_client, shard_body, tight_limits, unlimited):
    assert _sweep(flask_client, 6) == [200] * 6


# ── Turnstile challenge ───────────────────────────────────────────────────────

TOKEN_URL = "/api/ts/access-token"
SITE_KEY = "site-key"


@pytest.fixture
def challenge(monkeypatch):
    """Turnstile is configured; siteverify passes only the response ``"good"``, solved on qud.dev."""
    monkeypatch.setattr(access.config, "TS_TURNSTILE_SECRET", "turnstile-secret")
    monkeypatch.setattr(access.config, "TS_TURNSTILE_SITE_KEY", SITE_KEY)
    seen = {"hostname": "qud.dev"}

    class _Reply:
        def __init__(self, response: str):
            self._response = response

        def json(self) -> dict:
            return {"success": self._response == "good", "hostname": seen["hostname"]}

    def _post(url, data, timeout):
        return _Reply(data["response"])

    monkeypatch.setattr(turnstile.requests, "post", _post)
    return seen


def test_token_request_without_a_pass_is_told_the_site_key(flask_client, challenge):
    res = flask_client.post(TOKEN_URL, json={})

    assert res.status_code == 403
    assert res.get_json()["reason"] == "challenge_required"
    assert res.get_json()["turnstile_site_key"] == SITE_KEY


def test_get_cannot_skip_the_challenge(flask_client, challenge):
    assert flask_client.get(TOKEN_URL).get_json()["reason"] == "challenge_required"


def test_token_is_issued_for_a_passed_challenge(flask_client, challenge):
    res = flask_client.post(TOKEN_URL, json={"turnstile": "good"})

    assert res.status_code == 200
    assert ts_access.token_valid(
        str(flask_client.application.secret_key), res.get_json()["token"], CLIENT
    )


def test_failed_challenge_gets_no_token(flask_client, challenge):
    res = flask_client.post(TOKEN_URL, json={"turnstile": "bad"})

    assert res.status_code == 403
    assert res.get_json()["reason"] == "challenge_failed"


def test_pass_solved_on_a_foreign_site_gets_no_token(flask_client, challenge):
    challenge["hostname"] = "evil.example"

    res = flask_client.post(TOKEN_URL, json={"turnstile": "good"})

    assert res.get_json()["reason"] == "challenge_failed"


def test_post_without_challenge_configured_issues_a_token(flask_client):
    assert flask_client.post(TOKEN_URL, json={}).status_code == 200


# ── Edge secret ───────────────────────────────────────────────────────────────


@pytest.fixture
def edge_secret(monkeypatch):
    """The Space is configured to accept only requests the Cloudflare zone tagged."""
    monkeypatch.setattr(access.config, "TS_EDGE_SECRET", EDGE_SECRET)


def test_direct_origin_request_is_refused_when_edge_secret_is_set(flask_client, edge_secret):
    res = flask_client.get(CONFIG)

    assert res.status_code == 403
    assert res.get_json()["reason"] == "direct_origin_access"


def test_request_through_the_edge_is_served(flask_client, edge_secret):
    res = flask_client.get(CONFIG, headers={access.EDGE_HEADER: EDGE_SECRET})

    assert res.status_code == 200


def test_wrong_edge_secret_is_refused(flask_client, edge_secret):
    res = flask_client.get(CONFIG, headers={access.EDGE_HEADER: "guess"})

    assert res.status_code == 403


def test_unlimited_capability_may_use_the_direct_origin(flask_client, edge_secret, unlimited):
    assert flask_client.get(CONFIG).status_code == 200
