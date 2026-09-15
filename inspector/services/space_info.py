"""Identity of the Hugging Face Space this container runs on.

Exists for the frontend's Hugging Face mini header. `@huggingface/space-header`
normally fetches `huggingface.co/api/spaces/<id>` from the browser, but that
call is unauthenticated: it 401s for a private or protected Space and the
package then crashes reading a field off the undefined response. Serving the
same three fields ourselves lets the FE hand them straight to `init(space)`,
which skips the browser fetch entirely and works at any visibility.

``likes`` is the only field that needs the Hub. It moves slowly and is purely
decorative, so a miss degrades to 0 rather than failing the request.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# The like count is cosmetic; an hour-stale value is fine and keeps the Hub
# round trip off the request path for all but the first caller per hour.
_LIKES_TTL_SECONDS = 3600

_likes_cache: tuple[float, int] | None = None


def space_id() -> str | None:
    """``owner/name`` of the deployed Space, or None when running off-Space."""
    return os.environ.get("SPACE_ID") or None


def _fetch_likes(sid: str) -> int:
    """Like count from the Hub. 0 on any failure — never raises."""
    try:
        from huggingface_hub import HfApi

        info = HfApi(token=os.environ.get("HF_TOKEN")).space_info(sid)
        return int(getattr(info, "likes", 0) or 0)
    except Exception:  # noqa: BLE001
        logger.warning("space_info: like count lookup failed for %s", sid, exc_info=True)
        return 0


def _likes(sid: str) -> int:
    global _likes_cache
    now = time.monotonic()
    if _likes_cache is not None and now - _likes_cache[0] < _LIKES_TTL_SECONDS:
        return _likes_cache[1]
    value = _fetch_likes(sid)
    _likes_cache = (now, value)
    return value


def describe() -> dict[str, object]:
    """The `{id, author, likes}` shape `@huggingface/space-header` accepts.

    ``space_id`` is None off-Space, which the FE reads as "draw no header".
    """
    sid = space_id()
    if sid is None:
        return {"space_id": None, "author": None, "likes": 0}
    return {"space_id": sid, "author": sid.split("/")[0], "likes": _likes(sid)}
