"""Best-effort callbacks from out-of-band tooling back to the Inspector.

Manual backfills, schema-bump re-stamps, and local TS regens upload shards
straight to the bucket, bypassing the HF-job completion webhook — so the
Inspector never advances the reciter's ``ts`` watermark, recomputes staleness,
or audits the change. ``notify_ts_refreshed`` closes that gap: after a
successful upload the caller POSTs to ``/api/admin/internal/ts-refreshed`` so
the refresh is recorded instead of silent (see
``inspector/routes/admin/internal.py`` + ``repo_releases.mark_ts_refreshed``).

Shared by both ``qua_jobs`` and ``scripts/`` so the callback contract has one
home. Every call is best-effort: a missing URL/secret, an HTTP error, or a
non-2xx response is logged and swallowed — notifying the Inspector must never
fail the backfill that just succeeded.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("inspector_notify")

_TS_REFRESHED_PATH = "/api/admin/internal/ts-refreshed"

# Cloudflare (in front of the custom domain) rejects the stock Python-urllib
# user agent with 403 / error 1010, so every callback to the Inspector names itself.
WEBHOOK_USER_AGENT = "quranic-universal-audio-job/1.0"


def notify_ts_refreshed(
    inspector_url: str | None,
    slug: str,
    *,
    chapters: list[int] | None = None,
    reason: str = "manual",
    secret: str | None = None,
    timeout: float = 30.0,
) -> bool:
    """POST a TS-refresh callback for ``slug``. Best-effort; never raises.

    ``inspector_url`` is the Inspector root (e.g. ``https://…hf.space``); falls
    back to ``INSPECTOR_URL`` env. ``secret`` is the shared
    ``X-Inspector-Job-Secret``; falls back to ``INSPECTOR_WEBHOOK_SECRET`` env.
    When either is absent the call is skipped (returns ``False``) — a local
    backfill against a Space with no secret configured simply stays silent, as
    before. ``chapters`` (touched surahs) and ``reason`` (provenance tag) ride
    in the body. Returns ``True`` only on a 2xx response.
    """
    url = (inspector_url or os.environ.get("INSPECTOR_URL", "")).strip()
    secret = (secret or os.environ.get("INSPECTOR_WEBHOOK_SECRET", "")).strip()
    if not url or not secret:
        log.info("notify_ts_refreshed(%s) skipped — no inspector URL/secret", slug)
        return False
    endpoint = url.rstrip("/") + _TS_REFRESHED_PATH
    body: dict = {"slug": slug, "reason": reason}
    if chapters:
        body["chapters"] = sorted(chapters)
    try:
        import requests

        resp = requests.post(
            endpoint,
            json=body,
            headers={"X-Inspector-Job-Secret": secret, "User-Agent": WEBHOOK_USER_AGENT},
            timeout=timeout,
        )
        ok = 200 <= resp.status_code < 300
        log.info("notify_ts_refreshed(%s) → HTTP %s", slug, resp.status_code)
        return ok
    except Exception as exc:  # noqa: BLE001 — best-effort; never fail the backfill
        log.warning("notify_ts_refreshed(%s) failed: %s", slug, exc)
        return False
