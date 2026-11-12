"""Per-reciter review signals for the Segments reciter picker.

How many open **Low Confidence** items (confidence under the delivery's riwayah threshold —
the accordion's default slider position and the mark-ready gate) and **Low Confidence v2**
items (neural sidecar failures) each delivery has, plus its open **flags** (segments carrying
a flag), so the picker can badge, filter and sort rows.

The counts live in one small bucket file, ``catalog/review_signals.json``
(``ReviewSignalsDoc``). The Space never scans reciters for them:

- **Read:** ``current()`` loads the file on first use and re-reads it at most every
  ``REFRESH_S``, so an offline update shows up without a restart.
- **Write:** the validate route calls ``record()`` after computing a fresh payload; the file
  is rewritten only when that reciter's counts changed, read-modify-write under a lock so an
  offline update is never overwritten. Deliveries with nothing open are left out.
- **Fill:** ``scripts/backfills/review_signals.py`` runs ``compute()`` offline — the cached
  validate payload when there is one, else a transient scan of ``detailed.json``, the
  sidecar and the edit history's resolved-by-edit index that never enters the segment
  caches. ``tests/services/test_review_signals.py`` holds the scan to the validator.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

from adapters.detailed_json import load_entries_from_bytes
from config import LOW_CONFIDENCE_THRESHOLDS
from qua_shared.riwayat import resolve_sdk_slug
from qua_shared.schemas.bucket.review_signals import ReviewSignalsDoc
from services.activity.history_query import build_resolved_by_edit_index
from services.reference.delivery_edition import inspector_riwayah_for
from services.segments.flags import count_flagged
from services.storage import cache, data_dir
from services.storage.data_loader import load_detailed
from services.validation.classifier import is_ignored_for, is_suppressed_for

log = logging.getLogger(__name__)

#: Longest a loaded copy of the file is served before it is read again.
REFRESH_S = 600.0
_ZERO = {"low_confidence": 0, "low_confidence_v2": 0, "flagged": 0}

_lock = threading.Lock()


def counts_from_validate(payload: dict, threshold: float, flagged: int) -> dict[str, int]:
    """The counts from a validate payload (``/api/seg/validate`` shape); flags are not in the
    payload, so the caller counts them from the entries."""
    low = payload.get("low_confidence") or []
    return {
        "low_confidence": sum(1 for it in low if (it.get("confidence") or 0.0) < threshold),
        "low_confidence_v2": len(payload.get("low_confidence_v2") or []),
        "flagged": flagged,
    }


def scan_counts(entries: list[dict], threshold: float, probe_failed: set[str]) -> dict[str, int]:
    """The counts over ``entries`` (low confidence mirrors ``classifier.classify_flags``)."""
    low = low_v2 = 0
    for entry in entries:
        for seg in entry.get("segments") or []:
            ref = seg.get("matched_ref")
            if not ref:
                continue
            below = seg.get("confidence", 0.0) < threshold
            if len(ref.split("-")) != 2:
                # ``detail._build_detail_lists``' malformed-ref fallback: Low Confidence
                # without the ignore gate, never Low Confidence v2.
                low += below
                continue
            if below and not is_ignored_for(seg, "low_confidence"):
                low += 1
            uid = seg.get("segment_uid")
            if uid and uid in probe_failed and not is_suppressed_for(seg, "low_confidence_v2"):
                low_v2 += 1
    return {"low_confidence": low, "low_confidence_v2": low_v2, "flagged": count_flagged(entries)}


def _threshold(slug: str) -> float | None:
    try:
        return LOW_CONFIDENCE_THRESHOLDS[resolve_sdk_slug(inspector_riwayah_for(slug))]
    except (KeyError, ValueError):
        return None


def _resolved_entries(slug: str, entries: list[dict]) -> list[dict]:
    """Mark segments resolved-by-edit (edited from a card) as the validator does. The editor's
    cached index when there is one, else built from the edit history and dropped."""
    resolved = cache.get_seg_resolved_by_edit(slug)
    if resolved is None:
        had_history = cache.get_seg_edit_history(slug) is not None
        had_batches = cache.get_seg_history_batches(slug) is not None
        resolved = build_resolved_by_edit_index(slug)
        cache.pop_transient_history(slug, edit_history=not had_history, batches=not had_batches)
    if not resolved:
        return entries
    for entry in entries:
        for seg in entry.get("segments") or []:
            uid = seg.get("segment_uid")
            if uid and uid in resolved:
                seg["_resolved_by_edit"] = resolved[uid]
    return entries


def compute(slug: str, *, offline: bool = False) -> dict[str, int] | None:
    """Fresh counts for ``slug``; ``None`` when it has no segments or an unknown riwayah.

    ``offline`` (the backfill, which has no catalog or editor caches) takes the riwayah from
    ``detailed.json``'s ``_meta`` (absent = Hafs, as promote writes it) and always scans."""
    threshold = None if offline else _threshold(slug)
    if not offline:
        if threshold is None:
            return None
        cached = cache.get_seg_validate_cache(slug)
        if cached is not None:
            return counts_from_validate(cached, threshold, count_flagged(load_detailed(slug)))
    raw = data_dir.read_detailed_bytes(slug)
    if raw is None:
        return None
    meta, entries = load_entries_from_bytes(raw)
    if threshold is None:
        try:
            threshold = LOW_CONFIDENCE_THRESHOLDS[resolve_sdk_slug(meta.get("riwayah") or "hafs")]
        except (KeyError, ValueError):
            return None
    doc = data_dir.read_low_confidence_doc(slug) or {}
    failed = {u for u in doc.get("failures") or [] if isinstance(u, str)}
    return scan_counts(_resolved_entries(slug, entries), threshold, failed)


def _read_file() -> dict[str, dict[str, int]]:
    doc = data_dir.read_review_signals_doc() or {}
    out: dict[str, dict[str, int]] = {}
    for slug, counts in (doc.get("signals") or {}).items():
        if isinstance(counts, dict):
            out[slug] = {k: int(counts.get(k) or 0) for k in _ZERO}
    return out


def current() -> dict[str, dict[str, int]]:
    """Counts by slug from the file, re-read at most every ``REFRESH_S``."""
    signals, read_at = cache.get_review_signals_snapshot()
    if read_at is not None and time.monotonic() - read_at < REFRESH_S:
        return signals
    with _lock:
        signals, read_at = cache.get_review_signals_snapshot()
        if read_at is not None and time.monotonic() - read_at < REFRESH_S:
            return signals
        try:
            signals = _read_file()
        except Exception:
            # Keep what we had; try again after REFRESH_S rather than on every request.
            log.exception("review signals: reading %s failed", "catalog/review_signals.json")
        cache.set_review_signals_snapshot(signals, time.monotonic())
        return signals


def signals_for(slugs: list[str]) -> dict[str, dict[str, int]]:
    """Counts for the ``slugs`` that have something open."""
    signals = current()
    return {s: signals[s] for s in slugs if s in signals and any(signals[s].values())}


def write_counts(updates: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Merge ``updates`` into the file (read-modify-write) and the cache; all-zero counts
    drop the slug. Returns the merged counts."""
    with _lock:
        merged = _read_file()
        for slug, counts in updates.items():
            if any(counts.values()):
                merged[slug] = dict(counts)
            else:
                merged.pop(slug, None)
        doc = ReviewSignalsDoc.model_validate(
            {"updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "signals": merged}
        )
        data_dir.write_review_signals_doc(doc.model_dump(mode="json"))
        cache.set_review_signals_snapshot(merged, time.monotonic())
        return merged


def record(slug: str, payload: dict) -> None:
    """Keep the file current from a fresh validate payload; writes only on a change and
    never raises (the validate response must not depend on it)."""
    try:
        threshold = _threshold(slug)
        if threshold is None:
            return
        # The validator just loaded the entries, so this is a cache hit.
        counts = counts_from_validate(payload, threshold, count_flagged(load_detailed(slug)))
        if current().get(slug, _ZERO) == counts:
            return
        write_counts({slug: counts})
    except Exception:
        log.exception("review signals: recording %s failed", slug)
