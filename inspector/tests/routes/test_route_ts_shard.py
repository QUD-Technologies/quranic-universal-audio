"""Timestamps shard route — byte pass-through + cache policy."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _skip_token(monkeypatch):
    """The access token has its own tests (test_route_ts_access.py)."""
    from routes.timestamps import access

    monkeypatch.setattr(access, "require_token", lambda: None)


def test_ts_shard_passes_bytes_through_revalidated(flask_client, monkeypatch):
    """A shard mutates in place at a stable URL (re-stamp / edit), so the browser
    must revalidate on every use (`no-cache`) — the long max-age this replaced
    silently pinned pre-re-stamp cells. The ETag lets an unchanged shard come
    back as a bodyless 304."""
    from routes.timestamps import timestamps as ts_routes

    body = b"brotli-shard-bytes"
    monkeypatch.setattr(ts_routes.ts_serve, "shard_bytes", lambda *a, **k: body)

    res = flask_client.get("/api/ts/shard/reciter_a/2")

    assert res.status_code == 200
    assert res.data == body
    assert res.headers["Cache-Control"] == "private, no-cache"
    assert res.headers["Content-Encoding"] == "br"
    assert res.mimetype == "application/json"
    assert res.headers["ETag"]


def test_ts_shard_matching_etag_is_304(flask_client, monkeypatch):
    from routes.timestamps import timestamps as ts_routes

    state = {"body": b"v1"}
    monkeypatch.setattr(ts_routes.ts_serve, "shard_bytes", lambda *a, **k: state["body"])

    etag = flask_client.get("/api/ts/shard/reciter_a/2").headers["ETag"]
    same = flask_client.get("/api/ts/shard/reciter_a/2", headers={"If-None-Match": etag})
    assert same.status_code == 304
    assert same.data == b""

    state["body"] = b"v2-restamped"
    changed = flask_client.get("/api/ts/shard/reciter_a/2", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.data == b"v2-restamped"


def test_ts_shard_missing_is_404(flask_client, monkeypatch):
    from routes.timestamps import timestamps as ts_routes

    monkeypatch.setattr(ts_routes.ts_serve, "shard_bytes", lambda *a, **k: None)

    res = flask_client.get("/api/ts/shard/reciter_a/2")

    assert res.status_code == 404
