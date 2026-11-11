"""Flask glue for browser-only ``/api/ts/*`` access (``services/auth/ts_access.py``).

``guard(bp)`` attaches the browser check, the edge-secret check, the ban check
and the bot-notice headers to a timestamps blueprint. The chapter and verse
routes additionally call ``admit()``: the access token, then the per-client
limits (``services/auth/ts_rate_limit.py``).
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

import config
from services import auth as auth_service
from services.auth import capabilities, ts_access, ts_rate_limit

logger = logging.getLogger("inspector")

TOKEN_HEADER = "X-QUA-Access"
EDGE_HEADER = "X-QUA-Edge"
UNLIMITED_CAPABILITY = "timestamps.unlimited_access"

MODE_ENFORCE = "enforce"
MODE_OFF = "off"

limiter = ts_rate_limit.TsRateLimiter(
    ts_rate_limit.Limits(
        chapters_per_minute=config.TS_LIMIT_CHAPTERS_PER_MINUTE,
        chapters_per_hour=config.TS_LIMIT_CHAPTERS_PER_HOUR,
        requests_per_minute=config.TS_LIMIT_REQUESTS_PER_MINUTE,
        tokens_per_hour=config.TS_LIMIT_TOKENS_PER_HOUR,
    )
)

# Machine-readable "no automated use" signals on every /api/ts response.
_NOTICE_HEADERS = {
    "X-Robots-Tag": "noindex, noai, noimageai",
    "tdm-reservation": "1",
    "Link": '</llms.txt>; rel="terms-of-service"',
}


def _secret() -> str:
    return str(current_app.secret_key)


def _client() -> str:
    """Who is asking. ``remote_addr`` is the proxy-appended hop (ProxyFix), which
    a client cannot forge; the raw ``X-Forwarded-For`` header it can."""
    return ts_rate_limit.client_key(request.remote_addr)


def _unlimited() -> bool:
    return capabilities.can(auth_service.current_user(), UNLIMITED_CAPABILITY)


def _refuse(reason: str, status: int = 403):
    logger.warning(
        "ts access refused (%s): %s %s from %s ua=%r",
        reason,
        request.method,
        request.path,
        _client(),
        request.headers.get("User-Agent", ""),
    )
    return jsonify(ts_access.notice_payload(reason)), status


def _refuse_banned(seconds: int):
    resp, status = _refuse("too_many_requests", 429)
    resp.headers["Retry-After"] = str(seconds)
    return resp, status


def _browser_check():
    ok = ts_access.is_browser_request(
        sec_fetch_site=request.headers.get("Sec-Fetch-Site"),
        origin=request.headers.get("Origin"),
        referer=request.headers.get("Referer"),
        request_host=request.host,
    )
    if not ok:
        return _refuse("not_a_browser_page")
    return None


def _edge_check():
    """Refuse anonymous requests that did not come through the Cloudflare zone."""
    secret = config.TS_EDGE_SECRET
    if not secret or ts_access.edge_secret_matches(secret, request.headers.get(EDGE_HEADER)):
        return None
    if _unlimited():
        return None
    return _refuse("direct_origin_access")


def _ban_check():
    if config.TS_RATE_LIMIT_MODE != MODE_ENFORCE:
        return None
    seconds = limiter.ban_remaining(_client())
    if not seconds or _unlimited():
        return None
    return _refuse_banned(seconds)


def _over_limit(broken: str | None):
    """Log a broken limit; refuse when limits are enforced."""
    if broken is None:
        return None
    client = _client()
    logger.warning(
        "ts rate limit (%s, mode=%s): %s banned for %ss ua=%r",
        broken,
        config.TS_RATE_LIMIT_MODE,
        client,
        limiter.ban_remaining(client),
        request.headers.get("User-Agent", ""),
    )
    if config.TS_RATE_LIMIT_MODE != MODE_ENFORCE:
        return None
    return _refuse_banned(limiter.ban_remaining(client))


def _counted() -> bool:
    return config.TS_RATE_LIMIT_MODE != MODE_OFF and not _unlimited()


def _notice_headers(resp):
    for key, value in _NOTICE_HEADERS.items():
        resp.headers.setdefault(key, value)
    return resp


def guard(bp: Blueprint) -> None:
    bp.before_request(_browser_check)
    bp.before_request(_edge_check)
    bp.before_request(_ban_check)
    bp.after_request(_notice_headers)


def require_token():
    """Return a refusal response when the access token is missing, expired or
    was issued to another client, else ``None``."""
    if ts_access.token_valid(_secret(), request.headers.get(TOKEN_HEADER), _client()):
        return None
    return _refuse("access_token_missing_or_expired", 401)


def admit(reciter: str, chapter: int):
    """Gate one chapter or verse read: the access token, then this client's limits.

    Returns a refusal response, else ``None``.
    """
    refused = require_token()
    if refused is not None:
        return refused
    if not _counted():
        return None
    return _over_limit(limiter.record_chapter(_client(), f"{reciter}/{chapter}"))


def issue_token_response():
    if _counted():
        refused = _over_limit(limiter.record_token(_client()))
        if refused is not None:
            return refused
    token, expires_at = ts_access.issue_token(_secret(), _client())
    resp = jsonify({"token": token, "expires_at": expires_at, "header": TOKEN_HEADER})
    resp.headers["Cache-Control"] = "no-store"
    return resp
