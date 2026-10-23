"""Compute whether a reciter's generated timestamps are behind its segments.

The generated timestamps go stale the moment a timestamp-affecting segment edit
(trim/split/merge/delete/edit_reference/auto_fix_missing_word — see
``qua_shared.segment_edit_ops``) is saved after the last TS generation. This is
*computed* at read time — never stamped — so it stays accurate as edits land and
auto-clears when a regeneration moves ``produced_at`` past every edit. Keeping it
out of the save path leaves the segments editor (the hottest write path)
SQLite-free.

Surfaced on the Releases-tab TS chip via ``routes/admin/releases.py``; the
matching per-reciter history "tier" view lives in ``history_tiers.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from qua_shared.segment_edit_ops import batch_affects_timestamps
from services.activity import history_query
from services.storage import data_dir
from services.storage.hf_bucket import get_backend

logger = logging.getLogger(__name__)


def _provably_not_stale_by_mtime(slug: str, produced_at: str) -> bool:
    """True iff the edit-history file's mtime proves nothing changed since gen.

    Cheap mounted-path short-circuit: a stale TS-affecting edit has
    ``saved_at_utc > produced_at`` and the file's mtime is ``>=`` that append,
    so ``mtime(edit_history.jsonl) <= produced_at`` means no edit landed after
    generation — the reciter is provably not stale and the full JSONL parse can
    be skipped. Returns False (→ caller parses) when the mount isn't available
    (local dev), the file is missing, or the timestamps can't be compared.
    """
    lp = get_backend().local_path(data_dir.edit_history_path(slug))
    if lp is None:
        return False
    try:
        mtime = datetime.fromtimestamp(lp.stat().st_mtime, tz=UTC)
        produced = datetime.fromisoformat(produced_at.replace("Z", "+00:00"))
        return mtime <= produced
    except (OSError, ValueError, TypeError):
        return False


def _affects_timestamps(batch: dict, by_id: dict[str, dict]) -> bool:
    """Whether ``batch`` changed the timestamps; an undo carries no ops of its own, so
    it does when the ops it reverted did."""
    reverted = by_id.get(batch.get("reverts_batch_id") or "")
    if reverted is None:
        return batch_affects_timestamps(batch)
    op_ids = set(batch.get("reverts_op_ids") or [])
    ops = [op for op in reverted.get("operations") or [] if not op_ids or op.get("op_id") in op_ids]
    return batch_affects_timestamps({"operations": ops})


def ts_stale_info(slug: str, *, produced_at: str) -> dict | None:
    """Return staleness for ``slug``'s generated timestamps, or ``None``.

    ``produced_at`` is the current ``per_recitation_releases(track='ts')`` row's
    generation time (ISO-8601 UTC). Walks the reciter's edit-history batches and
    collects those saved after it that carry a timestamp-affecting op. Returns
    ``{"stale_since": <earliest such saved_at_utc>, "last_edit_at": <latest such
    saved_at_utc>, "edits_since": <count>, "affected_chapters": <sorted chapter
    list>}`` when any exist, else ``None`` (nothing generated, or only annotation
    edits since). ``last_edit_at`` lets the auto-regen automation debounce until
    a burst of consecutive edits settles.

    ``affected_chapters`` is the set of chapters touched by those edits — the
    union of each batch's ``chapter`` (Inspector save) / ``chapters`` (pipeline
    multi-chapter). It drives the Releases display, the regen scope default, and
    the persisted regen provenance.

    Best-effort: a failed edit-history read (e.g. bucket unavailable) yields
    ``None`` rather than propagating — this feeds the releases-status grid for
    every reciter, so one unreadable history must not 500 the whole page.

    Fast path: on the mounted bucket, an mtime check on the edit-history file
    skips the JSONL read entirely for the (common) not-edited-since case — the
    releases-status grid calls this per reciter, so the avoided cold reads are
    the bulk of that endpoint's latency.
    """
    if _provably_not_stale_by_mtime(slug, produced_at):
        return None
    try:
        batches = history_query.parse_history_for_reciter(slug)
    except Exception:  # noqa: BLE001 — best-effort; bucket/read failure → not stale
        logger.warning("ts_stale_info(%s): edit-history read failed; treating as not stale", slug)
        return None
    by_id = {b["batch_id"]: b for b in batches if b.get("batch_id")}
    stale_since: str | None = None
    last_edit_at: str | None = None
    count = 0
    affected: set[int] = set()
    for batch in batches:
        saved_at = batch.get("saved_at_utc")
        if not saved_at or saved_at <= produced_at:
            continue
        if not _affects_timestamps(batch, by_id):
            continue
        count += 1
        if stale_since is None or saved_at < stale_since:
            stale_since = saved_at
        if last_edit_at is None or saved_at > last_edit_at:
            last_edit_at = saved_at
        ch = batch.get("chapter")
        if isinstance(ch, int):
            affected.add(ch)
        for c in batch.get("chapters") or []:
            if isinstance(c, int):
                affected.add(c)
    if count == 0:
        return None
    return {
        "stale_since": stale_since,
        "last_edit_at": last_edit_at,
        "edits_since": count,
        "affected_chapters": sorted(affected),
    }
