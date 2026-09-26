"""Wasl re-check — reviewer-settled verse joins whose WASL / WAQF answer is re-asked.

The offline ``wasl_recheck_v1.json`` sidecar lists the LEFT piece uid of each
doubtful boundary. A uid stays open until an effective edit-history op touches
it — ``set_is_wasl`` on it, or a split / merge naming it in ``targets_before``
/ ``targets_after`` — in a batch saved after the sidecar's ``_meta.created_at``.
The FE renders open boundaries as unset; nothing here changes segment data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from services.activity.history_query import load_edit_history
from services.storage.data_loader import load_wasl_recheck

logger = logging.getLogger(__name__)

_ANSWERING_OPS = frozenset({"set_is_wasl", "split_segment", "merge_segments"})


def _parse_utc(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` or offset suffix) to an aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _op_uids(op: dict) -> set[str]:
    uids: set[str] = set()
    for key in ("targets_before", "targets_after"):
        for snap in op.get(key) or []:
            uid = snap.get("segment_uid") if isinstance(snap, dict) else None
            if isinstance(uid, str) and uid:
                uids.add(uid)
    return uids


def _answered_uids(reciter: str, since: datetime | None) -> set[str]:
    """Uids touched by an answering op in a batch saved after ``since``."""
    out: set[str] = set()
    for batch in load_edit_history(reciter).get("batches") or []:
        saved_at = _parse_utc(batch.get("saved_at_utc"))
        if since is not None and (saved_at is None or saved_at <= since):
            continue
        for op in batch.get("operations") or []:
            if op.get("op_type") in _ANSWERING_OPS or op.get("kind") in _ANSWERING_OPS:
                out |= _op_uids(op)
    return out


def open_wasl_recheck_uids(reciter: str) -> list[str]:
    """Sidecar uids not yet answered, in sidecar order. Empty when absent."""
    by_uid, meta = load_wasl_recheck(reciter)
    if not by_uid:
        return []
    since = _parse_utc((meta or {}).get("created_at"))
    if since is None:
        logger.warning(
            "wasl_recheck_v1.json for %s has no valid _meta.created_at; every op counts as an answer",
            reciter,
        )
    answered = _answered_uids(reciter, since)
    return [uid for uid in by_uid if uid not in answered]
