"""Cloudflare Turnstile verification for the timestamps access token.

A page gets an access token only after its browser passed the (invisible)
challenge, so a script that forges browser headers gets nothing: it has to run
a real browser, or pay a solving service, for every token.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import requests

logger = logging.getLogger("inspector")

SITEVERIFY_TIMEOUT_S = 5


def verify(
    response: str | None,
    *,
    secret: str,
    verify_url: str,
    remote_ip: str | None,
    host_allowed: Callable[[str | None], bool],
) -> bool:
    """True when ``response`` is a fresh pass solved on one of this site's pages.

    ``verify_url`` is Cloudflare's siteverify or a relay to it (a Hugging Face
    Space cannot connect to ``challenges.cloudflare.com``). Fails closed: an
    unreachable siteverify refuses the token.
    """
    if not response:
        return False
    form = {"secret": secret, "response": response}
    if remote_ip:
        form["remoteip"] = remote_ip
    try:
        result = requests.post(verify_url, data=form, timeout=SITEVERIFY_TIMEOUT_S).json()
    except (requests.RequestException, ValueError):
        logger.exception("turnstile: siteverify failed")
        return False
    if not result.get("success"):
        logger.warning("turnstile: refused (%s)", ",".join(result.get("error-codes") or []))
        return False
    if not host_allowed(result.get("hostname")):
        logger.warning("turnstile: pass solved on foreign host %r", result.get("hostname"))
        return False
    return True
