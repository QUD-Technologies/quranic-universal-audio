"""Per-reciter review signals for the Segments reciter picker.

How many open **Low Confidence** items (confidence under the delivery's riwayah threshold —
the accordion's default slider position and the mark-ready gate) and **Low Confidence v2**
items (neural sidecar failures) each delivery has, so the picker can badge and filter rows.

Cheap by design: a reciter the editor already validated answers from the cached validate
payload; any other is a transient scan of ``detailed.json``, the sidecar and the edit history's
resolved-by-edit index that never enters the segment caches (a fleet sweep through ``validate_reciter_segments`` would evict every
active editor's LRU entries and hold the single worker for minutes). The scan applies the
classifier's own gates (``is_ignored_for`` / ``is_suppressed_for``), and
``tests/validation/test_review_signals.py`` holds it to the validator's counts.

Counts are cached per slug (``cache.get/set_review_signals``) and dropped by
``invalidate_seg_caches`` on every save, undo or discard. A single daemon thread, started by the
first request, fills the slugs that are missing.
"""

from __future__ import annotations

import logging
import threading
import time

from adapters.detailed_json import load_entries_from_bytes
from config import LOW_CONFIDENCE_THRESHOLDS
from qua_shared.riwayat import resolve_sdk_slug
from services.activity.history_query import build_resolved_by_edit_index
from services.reference.delivery_edition import inspector_riwayah_for
from services.storage import cache, data_dir
from services.validation.classifier import is_ignored_for, is_suppressed_for

log = logging.getLogger(__name__)

#: Pause between reciters in the warm sweep, so editor requests keep the GIL.
WARM_PAUSE_S = 0.2

_warm_lock = threading.Lock()
_warm_running = False


def counts_from_validate(payload: dict, threshold: float) -> dict[str, int]:
    """The two counts from a validate payload (``/api/seg/validate`` shape)."""
    low = payload.get("low_confidence") or []
    return {
        "low_confidence": sum(1 for it in low if (it.get("confidence") or 0.0) < threshold),
        "low_confidence_v2": len(payload.get("low_confidence_v2") or []),
    }


def scan_counts(entries: list[dict], threshold: float, probe_failed: set[str]) -> dict[str, int]:
    """The two counts over ``entries`` (mirrors ``classifier.classify_flags``)."""
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
    return {"low_confidence": low, "low_confidence_v2": low_v2}


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


def compute(slug: str) -> dict[str, int] | None:
    """Fresh counts for ``slug``; ``None`` when it has no segments or an unknown riwayah."""
    threshold = _threshold(slug)
    if threshold is None:
        return None
    cached = cache.get_seg_validate_cache(slug)
    if cached is not None:
        return counts_from_validate(cached, threshold)
    raw = data_dir.read_detailed_bytes(slug)
    if raw is None:
        return None
    _, entries = load_entries_from_bytes(raw)
    doc = data_dir.read_low_confidence_doc(slug) or {}
    failed = {u for u in doc.get("failures") or [] if isinstance(u, str)}
    return scan_counts(_resolved_entries(slug, entries), threshold, failed)


def snapshot(slugs: list[str]) -> tuple[dict[str, dict[str, int]], int]:
    """``(counts by slug, slugs not computed yet)`` from the cache, without computing."""
    out: dict[str, dict[str, int]] = {}
    pending = 0
    for slug in slugs:
        hit = cache.get_review_signals(slug)
        if hit is None:
            pending += 1
        elif hit:
            out[slug] = hit
    return out, pending


def warm(slugs: list[str]) -> None:
    """Fill missing slugs in a background daemon thread (one sweep at a time)."""
    global _warm_running
    with _warm_lock:
        if _warm_running:
            return
        _warm_running = True
    threading.Thread(target=_sweep, args=(list(slugs),), name="review-signals", daemon=True).start()


def _sweep(slugs: list[str]) -> None:
    global _warm_running
    try:
        for slug in slugs:
            if cache.get_review_signals(slug) is not None:
                continue
            try:
                counts = compute(slug)
            except Exception:
                log.exception("review signals: %s failed", slug)
                counts = None
            # An empty dict caches "nothing to show" so the sweep does not retry it.
            cache.set_review_signals(slug, counts or {})
            time.sleep(WARM_PAUSE_S)
    finally:
        with _warm_lock:
            _warm_running = False
