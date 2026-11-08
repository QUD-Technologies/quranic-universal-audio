"""Browser-only /api/ts/* access: Fetch Metadata gate, shard token, notice headers."""

from __future__ import annotations

import pytest

from services.auth import ts_access

SHARD = "/api/ts/shard/reciter_a/2"


@pytest.fixture
def raw_client(flask_client):
    """Test client with NO browser headers — what a script sends."""
    flask_client.environ_base.pop("HTTP_SEC_FETCH_SITE", None)
    return flask_client


@pytest.fixture
def shard_body(monkeypatch):
    from routes.timestamps import timestamps as ts_routes

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
    res = raw_client.get("/api/ts/config", headers={"Sec-Fetch-Site": site})
    assert res.status_code == 403


def test_same_site_qud_dev_page_is_allowed(raw_client):
    res = raw_client.get("/api/ts/config", headers={"Sec-Fetch-Site": "same-site"})
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
    res = raw_client.get("/api/ts/config", headers=headers)
    assert (res.status_code == 200) is ok


def test_shard_needs_access_token(flask_client, shard_body):
    res = flask_client.get(SHARD)

    assert res.status_code == 401
    assert res.get_json()["reason"] == "access_token_missing_or_expired"


def test_shard_with_token_serves_bytes(flask_client, shard_body):
    tok = _token(flask_client)

    res = flask_client.get(SHARD, headers={tok["header"]: tok["token"]})

    assert res.status_code == 200
    assert res.data == shard_body


def test_access_token_endpoint_is_browser_gated(raw_client):
    assert raw_client.get("/api/ts/access-token").status_code == 403


def test_token_expires_and_rejects_tampering():
    secret = "s" * 40
    token, expires_at = ts_access.issue_token(secret, now=1000)

    assert expires_at == 1000 + ts_access.TOKEN_TTL_SECONDS
    assert ts_access.token_valid(secret, token, now=1000)
    assert not ts_access.token_valid(secret, token, now=expires_at)
    assert not ts_access.token_valid("other-secret" * 4, token, now=1000)
    forged_head = ts_access._b64(str(expires_at + 10_000).encode())
    assert not ts_access.token_valid(secret, f"{forged_head}.{token.split('.')[1]}", now=1000)
    assert not ts_access.token_valid(secret, "garbage", now=1000)
    assert not ts_access.token_valid(secret, None, now=1000)
