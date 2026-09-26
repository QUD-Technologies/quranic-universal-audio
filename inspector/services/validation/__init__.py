"""Validation engine: registry-backed segment validation + chapter counts.

No Flask imports -- all functions accept parameters and return plain dicts.

Public API (routes use ``from services.validation import X``):
- ``is_ignored_for``
- ``classify_segment``, ``classify_segment_full``, ``classify_entry``
- ``classify_snapshot``
- ``validate_reciter_segments``, ``strip_boundary_review``
- ``open_wasl_recheck_uids``
- registry symbols (re-exported)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from services.activity.history_query import (
    build_resolved_by_edit_index,
    build_split_group_index,
)
from services.reference.delivery_edition import sdk_riwayah_for
from services.storage import cache
from services.storage.data_loader import (
    get_single_word_verses,
    get_word_counts,
    load_detailed,
    load_false_split,
    load_hidden_pause,
    load_missed_waqf,
    load_pipeline_meta,
    load_probe_v2,
    load_seg_verses,
    load_unmarked_wasl,
)
from services.validation._missing import _build_missing_words
from services.validation._structural import _check_structural_errors

# The phonemic side of boundary_adj is persisted as ``is_boundary_adj`` at
# extraction / backfill time (``scripts/backfills/backfill_boundary_adj.py``);
# the classifier reads it, so canonical=None throughout the runtime path.
from services.validation.classifier import (
    _check_boundary_adj,
    classify_entry,
    classify_flags,
    classify_segment,
    classify_segment_full,
    is_ignored_for,
    is_resolved_by_edit,
    is_suppressed_for,
)
from services.validation.cross_verse_resolved import resolved_cross_verse_items
from services.validation.detail import _build_detail_lists
from services.validation.registry import (
    ALL_CATEGORIES,
    AUTO_SUPPRESS_CATEGORIES,
    CAN_IGNORE_CATEGORIES,
    OWNER_ONLY_CATEGORIES,
    PER_CHAPTER_CATEGORIES,
    PER_SEGMENT_CATEGORIES,
    PER_VERSE_CATEGORIES,
    PERSISTS_IGNORE_CATEGORIES,
    IssueDefinition,
    IssueRegistry,
    filter_persistent_ignores,
)
from services.validation.snapshot_classifier import classify_snapshot
from services.validation.wasl_recheck import open_wasl_recheck_uids
from utils.references import is_by_ayah_source

ISSUE_REGISTRY = IssueRegistry


def _load_resolved_idx_cached(reciter: str) -> dict:
    """Cache-aware wrapper for ``build_resolved_by_edit_index``.

    Hoisted so the parallel fan-out below can submit it as a single callable.
    """
    resolved = cache.get_seg_resolved_by_edit(reciter)
    if resolved is None:
        resolved = build_resolved_by_edit_index(reciter)
        cache.set_seg_resolved_by_edit(reciter, resolved)
    return resolved


def _read_deleted_basmala_chapters(reciter: str) -> set[int]:
    """Read the stripped-basmala set from the ``pipeline_meta.json`` sidecar.

    Hard-fails on missing sidecar (clear deploy signal): silent skip would
    drop basmala_amin counts to 0 without anyone noticing. Run
    ``scripts/backfills/backfill_deleted_basmala.py`` for any reciter that
    pre-dates the extraction-time write.
    """
    meta = load_pipeline_meta(reciter)
    if meta is None:
        raise RuntimeError(
            f"pipeline_meta.json missing for {reciter!r}; run scripts/backfill_deleted_basmala.py"
        )
    return set(meta.get("deleted_basmala_chapters") or [])


BOUNDARY_REVIEW_CATEGORIES: tuple[str, ...] = (
    "hidden_pause",
    "missed_waqf",
    "false_split",
    "unmarked_wasl",
)


def strip_boundary_review(result: dict) -> dict:
    """Return a copy of a validate result without the boundary-review
    categories: arrays + metas removed, counts zeroed. Used for viewers
    lacking ``segments.view_boundary_review``."""
    out = {k: v for k, v in result.items() if not k.startswith(BOUNDARY_REVIEW_CATEGORIES)}
    counts = dict(result.get("category_counts") or {})
    for cat in BOUNDARY_REVIEW_CATEGORIES:
        counts[cat] = 0
    out["category_counts"] = counts
    return out


def validate_reciter_segments(reciter: str, *, include_boundary_review: bool = True) -> dict | None:
    """Validate all chapters for a reciter, returning issues grouped by category.

    Returns a plain dict suitable for ``jsonify()``, or ``None`` when the
    reciter has no saved segments (callers surface this as a 404 / block).

    ``include_boundary_review=False`` omits the ``hidden_pause`` /
    ``missed_waqf`` / ``false_split`` / ``unmarked_wasl`` arrays and metas and zeroes their
    counts (the viewer lacks ``segments.view_boundary_review``). None of them
    is in ``BLOCKING_COUNT_KEYS``, so the mark-ready gate is unaffected
    either way.

    ``wasl_recheck`` lists the left-piece uids of cross-verse boundaries whose
    WASL / WAQF answer is re-asked (see ``services/validation/wasl_recheck.py``).
    It is not review-only and survives ``strip_boundary_review``.
    """
    # Parallel I/O fan-out: nine independent bucket reads. SSL recv releases
    # the GIL, so threads cut the wall-clock cost from sum(serial) to
    # max(slowest). Each loader caches its own result via services/cache.py,
    # so the threads don't double-fetch. ``load_seg_verses`` populates the
    # cache that ``_check_structural_errors`` later reads.
    with ThreadPoolExecutor(max_workers=9) as pool:
        f_detailed = pool.submit(load_detailed, reciter)
        f_resolved = pool.submit(_load_resolved_idx_cached, reciter)
        f_probe = pool.submit(load_probe_v2, reciter)
        f_hidden = pool.submit(load_hidden_pause, reciter)
        f_missed = pool.submit(load_missed_waqf, reciter)
        f_false = pool.submit(load_false_split, reciter)
        f_wasl = pool.submit(load_unmarked_wasl, reciter)
        f_verses = pool.submit(load_seg_verses, reciter)
        f_recheck = pool.submit(open_wasl_recheck_uids, reciter)
        entries = f_detailed.result()
        resolved_idx = f_resolved.result()
        probe_failed_uids, probe_meta = f_probe.result()
        hidden_pause_map, hidden_pause_meta = f_hidden.result()
        missed_waqf_map, missed_waqf_meta = f_missed.result()
        false_split_map, false_split_meta = f_false.result()
        unmarked_wasl_map, unmarked_wasl_meta = f_wasl.result()
        f_verses.result()  # prime the seg_verses cache before structural pass
        wasl_recheck = f_recheck.result()

    if not entries:
        return None

    # Every coordinate table, script lookup and confidence cutoff below is read
    # in the delivery's own edition. Raises rather than defaulting to Hafs: a
    # Warsh delivery validated against Hafs verse counts reports missing words
    # that are not missing and misses ones that are.
    riwayah = sdk_riwayah_for(reciter)
    word_counts = get_word_counts(riwayah)
    # canonical=None: the phonemic side of boundary_adj is captured at backfill
    # time onto each seg's ``is_boundary_adj`` field. Classifier short-circuits
    # on the persisted value; legacy segs without the field fall through to
    # compute_is_boundary_adj with canonical=None → structural side only.
    canonical = None
    single_word_verses = get_single_word_verses(riwayah)

    meta = cache.get_seg_meta(reciter)
    is_by_ayah = is_by_ayah_source(meta.get("audio_source", ""))

    # Inject resolved-by-edit categories. The transient ``_resolved_by_edit``
    # field is consulted by ``is_resolved_by_edit`` during this validate pass
    # and stripped from every seg before returning so it never reaches disk
    # via the cached entries list. Categories are limited to the soft set in
    # ``RESOLVES_BY_EDIT_CATEGORIES`` (boundary_adj / audio_bleeding /
    # repetitions / low_confidence_v2) -- this is what makes those cards
    # disappear from the accordion once the user has edited from them.
    _injected_segs: list[dict] = []
    if resolved_idx:
        for entry in entries:
            for seg in entry.get("segments", []):
                uid = seg.get("segment_uid")
                if uid and uid in resolved_idx:
                    seg["_resolved_by_edit"] = resolved_idx[uid]
                    _injected_segs.append(seg)

    deleted_basmala_chapters = _read_deleted_basmala_chapters(reciter)
    # Precomputed split-group closures (pure function of cached batch list).
    # Shipped at the response top level as ``split_group_index`` (uid → list of
    # transitive descendants) so the FE can expand accordion cards without
    # walking historyData. Keyed by ROOT uid only — items look up by their
    # own uid client-side.
    split_group_index = build_split_group_index(reciter)

    detail = _build_detail_lists(
        entries,
        is_by_ayah,
        word_counts,
        canonical,
        single_word_verses,
        probe_failed_uids=probe_failed_uids,
        deleted_basmala_chapters=deleted_basmala_chapters,
        hidden_pause_map=hidden_pause_map,
        missed_waqf_map=missed_waqf_map,
        false_split_map=false_split_map,
        unmarked_wasl_map=unmarked_wasl_map,
        riwayah=riwayah,
    )
    missing_words = _build_missing_words(
        detail["verse_segments"], word_counts, detail["sequence_gaps"], riwayah
    )
    # Resolved cross-verse items: split roots from edit history whose pieces
    # carry the WASL/WAQF picks. Shipped in the same list (``resolved: True``)
    # but excluded from the count so they never gate mark-ready.
    unresolved_cross_verse = len(detail["cross_verse"])
    detail["cross_verse"] = detail["cross_verse"] + resolved_cross_verse_items(
        reciter,
        entries,
        {it["segment_uid"] for it in detail["cross_verse"] if it.get("segment_uid")},
    )
    errors, missing_verses, stats = _check_structural_errors(reciter, entries, riwayah)

    # Aggregate counts in registry-declared accordion order. Additive on top
    # of the per-category arrays; the frontend uses it to render badge totals
    # and category-summary widgets without walking every detail array.
    category_counts = {
        "failed": len(detail["failed"]),
        "missing_verses": len(missing_verses),
        "missing_words": len(missing_words),
        "structural_errors": len(errors),
        "low_confidence": len(detail["low_confidence"]),
        "low_confidence_v2": len(detail["low_confidence_v2"]),
        "hidden_pause": len(detail["hidden_pause"]),
        "missed_waqf": sum(1 for it in detail["missed_waqf"] if not it.get("resolved")),
        "false_split": len(detail["false_split"]),
        "unmarked_wasl": len(detail["unmarked_wasl"]),
        "repetitions": len(detail["repetitions"]),
        "audio_bleeding": len(detail["audio_bleeding"]),
        "boundary_adj": len(detail["boundary_adj"]),
        "cross_verse": unresolved_cross_verse,
        "qalqala": len(detail["qalqala"]),
        "muqattaat": len(detail["muqattaat"]),
        "basmala_amin": len(detail["basmala_amin"]),
    }

    result = {
        "errors": errors,
        "structural_errors": errors,  # alias — same list; MUST-1 additive
        "missing_verses": missing_verses,
        "missing_words": missing_words,
        "failed": detail["failed"],
        "low_confidence": detail["low_confidence"],
        "low_confidence_v2": detail["low_confidence_v2"],
        "hidden_pause": detail["hidden_pause"],
        "missed_waqf": detail["missed_waqf"],
        "false_split": detail["false_split"],
        "unmarked_wasl": detail["unmarked_wasl"],
        "boundary_adj": detail["boundary_adj"],
        "cross_verse": detail["cross_verse"],
        "audio_bleeding": detail["audio_bleeding"],
        "repetitions": detail["repetitions"],
        "muqattaat": detail["muqattaat"],
        "qalqala": detail["qalqala"],
        "basmala_amin": detail["basmala_amin"],
        "category_counts": category_counts,
        "stats": stats,
        # Precomputed split-group closures keyed by root uid. The FE reads
        # this map instead of walking historyData to expand accordion cards
        # with the full descendant chain after a split.
        "split_group_index": split_group_index,
        "wasl_recheck": wasl_recheck,
    }
    if probe_meta is not None:
        result["low_confidence_v2_meta"] = probe_meta
    if hidden_pause_meta is not None:
        result["hidden_pause_meta"] = hidden_pause_meta
    if missed_waqf_meta is not None:
        result["missed_waqf_meta"] = missed_waqf_meta
    if false_split_meta is not None:
        result["false_split_meta"] = false_split_meta
    if unmarked_wasl_meta is not None:
        result["unmarked_wasl_meta"] = unmarked_wasl_meta
    if not include_boundary_review:
        result = strip_boundary_review(result)

    # Strip the transient injection so the cached entries dict stays clean
    # for the save flow (which serializes ``entries`` directly to disk).
    for seg in _injected_segs:
        seg.pop("_resolved_by_edit", None)

    return result


__all__ = [
    "is_ignored_for",
    "is_resolved_by_edit",
    "is_suppressed_for",
    "classify_flags",
    "classify_segment",
    "classify_segment_full",
    "classify_entry",
    "classify_snapshot",
    "validate_reciter_segments",
    "strip_boundary_review",
    "open_wasl_recheck_uids",
    "BOUNDARY_REVIEW_CATEGORIES",
    "_build_detail_lists",
    "IssueDefinition",
    "IssueRegistry",
    "ISSUE_REGISTRY",
    "ALL_CATEGORIES",
    "PER_SEGMENT_CATEGORIES",
    "PER_VERSE_CATEGORIES",
    "PER_CHAPTER_CATEGORIES",
    "CAN_IGNORE_CATEGORIES",
    "AUTO_SUPPRESS_CATEGORIES",
    "OWNER_ONLY_CATEGORIES",
    "PERSISTS_IGNORE_CATEGORIES",
    "filter_persistent_ignores",
]
