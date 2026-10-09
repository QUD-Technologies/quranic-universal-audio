"""Background re-time of the chapters a save or undo changed.

:func:`schedule` returns at once. One worker per ``(slug, chapter)`` waits
:data:`SETTLE_S` so a burst of saves is timed once, then asks the aligner for that
chapter's times (only the changed segments are timed); a save landing while it runs
queues one more pass. A failure is logged and left for the next save or timestamps run,
which time whatever is still stale.
"""

from __future__ import annotations

import logging
import threading
import time

from . import aligner_timing

log = logging.getLogger("inspector")

#: Seconds a chapter's worker waits for further saves before timing it.
SETTLE_S = 5.0

_lock = threading.Lock()
_running: set[tuple[str, int]] = set()
_dirty: set[tuple[str, int]] = set()


def schedule(slug: str, chapters) -> None:
    """Re-time ``chapters`` of ``slug`` in the background (no-op unless enabled)."""
    if not aligner_timing.enabled():
        return
    for chapter in {int(c) for c in chapters}:
        key = (slug, chapter)
        with _lock:
            if key in _running:
                _dirty.add(key)
                continue
            _running.add(key)
        threading.Thread(
            target=_work, args=(key,), name=f"retime-{slug}-{chapter}", daemon=True
        ).start()


def _work(key: tuple[str, int]) -> None:
    slug, chapter = key
    while True:
        time.sleep(SETTLE_S)
        try:
            aligner_timing.retime(slug, [chapter])
        except Exception:  # noqa: BLE001 — a later save or timestamps run times what is stale
            log.exception("[timing %s] ch%s: re-time after save failed", slug, chapter)
        with _lock:
            if key in _dirty:
                _dirty.discard(key)
                continue
            _running.discard(key)
            return
