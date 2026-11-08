"""Flask glue for browser-only ``/api/ts/*`` access (``services/auth/ts_access.py``).

``guard(bp)`` attaches the browser check and the bot-notice headers to a
timestamps blueprint. The shard route additionally calls ``require_token()``.
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from services.auth import ts_access

logger = logging.getLogger("inspector")

TOKEN_HEADER = "X-QUA-Access"

# Machine-readable "no automated use" signals on every /api/ts response.
_NOTICE_HEADERS = {
    "X-Robots-Tag": "noindex, noai, noimageai",
    "tdm-reservation": "1",
    "Link": '</llms.txt>; rel="terms-of-service"',
}


def _secret() -> str:
    return str(current_app.secret_key)


def _refuse(reason: str, status: int = 403):
    logger.warning(
        "ts access refused (%s): %s %s from %s ua=%r",
        reason,
        request.method,
        request.path,
        request.headers.get("X-Forwarded-For", request.remote_addr),
        request.headers.get("User-Agent", ""),
    )
    return jsonify(ts_access.notice_payload(reason)), status


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


def _notice_headers(resp):
    for key, value in _NOTICE_HEADERS.items():
        resp.headers.setdefault(key, value)
    return resp


def guard(bp: Blueprint) -> None:
    bp.before_request(_browser_check)
    bp.after_request(_notice_headers)


def require_token():
    """Return a refusal response when the shard access token is missing/expired, else ``None``."""
    if ts_access.token_valid(_secret(), request.headers.get(TOKEN_HEADER)):
        return None
    return _refuse("access_token_missing_or_expired", 401)


def issue_token_response():
    token, expires_at = ts_access.issue_token(_secret())
    resp = jsonify({"token": token, "expires_at": expires_at, "header": TOKEN_HEADER})
    resp.headers["Cache-Control"] = "no-store"
    return resp
