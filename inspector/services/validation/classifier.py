"""Unified per-segment classifier.

The single source of truth for "which validation categories does this segment
trigger?" — consumed by:

- ``services.validation.detail._build_detail_lists`` (response building).
- ``services.validation.snapshot_classifier`` (history snapshots).

Public surface
--------------

``is_ignored_for(seg, category)``
    Honors ``ignored_categories`` (incl. the legacy ``"_all"`` marker and the
    pre-categories ``ignored=True`` boolean).

``classify_flags(seg, ...) -> dict[str, bool|str|None]``
    Internal flag dict; matches the historical detail-builder contract.

``classify_segment(seg, ...) -> list[str]``
    Category list in registry-declared accordion order. ``detail=True`` adds
    ``"low_confidence_detail"`` (the ``< 1.0`` tier) for callers that want
    the detail-tier flag surfaced as a category.

``classify_segment_full(seg, ...) -> dict``
    Full result dict with ``categories``, ``qalqala_letter``,
    ``low_confidence_detail``, and ``end_of_verse``. Used by callers that
    want the auxiliary fields alongside the category list.

``classify_entry(entry, ..., canonical=None) -> dict``
    Walks every segment in an entry, returning
    ``{segment_uid: {"categories": [...], "qalqala_letter": str|None}}``.
    Segments without a ``segment_uid`` get a synthesized index-based key
    (``f"_idx:{i}"``) so the result is always a complete map.

Tie-breakers (B-1 / B-2 / B-3)
------------------------------

- ``repetitions``: ``wrap_word_ranges`` only — ``has_repeated_words`` alone
  does not classify.
- ``boundary_adj``: structural rule first; phoneme-tail mismatch is an
  optional second signal when ``canonical`` is provided.
- ``audio_bleeding``: ``seg_belongs_to_entry`` against the parsed entry-ref
  structure. Audio-URL comparisons are not part of the rule.
"""

from __future__ import annotations

from collections.abc import Container
from typing import Any

from config import LOW_CONFIDENCE_DETAIL_THRESHOLD, LOW_CONFIDENCE_THRESHOLDS
from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from services.reference import edition_tables
from services.reference.quran_refs import dk_text_for_ref
from services.validation.registry import PER_SEGMENT_CATEGORIES
from utils.arabic_text import strip_quran_deco
from utils.references import seg_belongs_to_entry

# ---------------------------------------------------------------------------
# Ignore filter
# ---------------------------------------------------------------------------


def is_ignored_for(seg: dict, category: str) -> bool:
    """Return True when a segment opts out of being classified as ``category``.

    Reads ``seg["ignored_categories"]`` first; the legacy ``"_all"`` marker
    means "ignore every per-segment category". Falls back to the pre-categories
    ``seg["ignored"]`` boolean for fixtures that predate the array shape.
    """
    ic = seg.get("ignored_categories")
    if ic:
        return "_all" in ic or category in ic
    return bool(seg.get("ignored"))


def is_resolved_by_edit(seg: dict, category: str) -> bool:
    """Return True when the user has edited this segment from a card of
    ``category`` and the validator should drop the flag without writing to
    ``ignored_categories``.

    The validate route injects ``seg["_resolved_by_edit"]`` (set or list) from
    the edit-history index built by
    ``services.history_query.build_resolved_by_edit_index``. Save and CLI
    paths that don't inject the field treat it as empty -- the helper is a
    no-op there, so non-validate flows are unaffected.
    """
    rbe = seg.get("_resolved_by_edit")
    if not rbe:
        return False
    return category in rbe


def is_suppressed_for(seg: dict, category: str) -> bool:
    """Combined gate: the segment opts out of ``category`` either via an
    explicit Ignore (``ignored_categories``) or via a prior edit dispatched
    from that card (resolved-by-edit history).
    """
    return is_ignored_for(seg, category) or is_resolved_by_edit(seg, category)


# ---------------------------------------------------------------------------
# Boundary-adjustment rule
# ---------------------------------------------------------------------------


def compute_is_boundary_adj(
    seg: dict,
    surah: int,
    s_ayah: int,
    s_word: int,
    e_word: int,
    single_word_verses: set,
    canonical: dict | None = None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
) -> bool:
    """Raw boundary-adjustment computation — NO suppression check.

    Used by every writer that persists the field (save.py, backfill script,
    extraction pipeline) AND by ``_check_boundary_adj`` as the fall-through
    path. Splitting the suppression check out lets us persist a value that
    matches across all writers regardless of runtime ignore state.

    Rule: one-word segment outside the muqattaʼat / single-word-verse /
    standalone-ref / standalone-word allow-list.

    ``canonical`` is accepted for back-compat but ignored — the phonemic
    side was retired in Migration #5 along with ``phonemes_asr``, so
    structural-only is now the canonical signal. Kept in the signature so
    callers (save, backfill, extraction outputs.py) don't need touching.

    Every table is read in ``riwayah``'s own coordinates. The muqattaat
    exemption stays VERSE-keyed: it covers words far past the opening letters
    (13:1 runs on for eight more), and narrowing it to the opening word would
    newly flag one-word segments across the published Hafs corpus.
    """
    _ = canonical  # retired; see docstring
    if (surah, s_ayah) in edition_tables.muqattaat_verses(riwayah):
        return False
    if (surah, s_ayah) in single_word_verses:
        return False

    if s_word == e_word and (surah, s_ayah, s_word) not in edition_tables.standalone_refs(riwayah):
        text = dk_text_for_ref(seg.get("matched_ref"), riwayah)
        if strip_quran_deco(text) not in edition_tables.standalone_words(riwayah):
            return True

    return False


def _check_boundary_adj(
    seg: dict,
    surah: int,
    s_ayah: int,
    s_word: int,
    e_word: int,
    single_word_verses: set,
    canonical: dict | None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
) -> bool:
    """Apply boundary-adjustment, honoring runtime suppression.

    Reads the persisted ``is_boundary_adj`` field when present — stamped at
    save / backfill / extraction time via ``compute_is_boundary_adj``.
    Legacy segs without the field fall through to a fresh computation. The
    suppression check is applied here (the persisted value is the raw
    rule output; suppression layers on top).
    """
    if is_suppressed_for(seg, "boundary_adj"):
        return False
    if "is_boundary_adj" in seg:
        return bool(seg["is_boundary_adj"])
    return compute_is_boundary_adj(
        seg,
        surah,
        s_ayah,
        s_word,
        e_word,
        single_word_verses,
        canonical,
        riwayah,
    )


# ---------------------------------------------------------------------------
# matched_ref parsing
# ---------------------------------------------------------------------------


def _parse_matched_ref(matched_ref: str) -> tuple[int, int, int, int, int] | None:
    """Parse ``"surah:s_ayah:s_word-surah:e_ayah:e_word"`` into a 5-tuple.

    Returns ``None`` for malformed refs; callers treat malformed as
    "no segment-level position" — failed segs short-circuit to the
    ``failed`` category before reaching this helper.
    """
    parts = matched_ref.split("-")
    if len(parts) != 2:
        return None
    sp = parts[0].split(":")
    ep = parts[1].split(":")
    if len(sp) != 3 or len(ep) != 3:
        return None
    try:
        return int(sp[0]), int(sp[1]), int(sp[2]), int(ep[1]), int(ep[2])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Flags-style classifier (internal contract for detail / counts builders)
# ---------------------------------------------------------------------------


def classify_flags(
    seg: dict,
    entry_ref: str,
    is_by_ayah: bool,
    surah: int,
    s_ayah: int,
    e_ayah: int,
    s_word: int,
    e_word: int,
    single_word_verses: set,
    canonical: dict | None,
    probe_failed_uids: set | None = None,
    hidden_pause_uids: Container[str] | None = None,
    missed_waqf_uids: Container[str] | None = None,
    false_split_uids: Container[str] | None = None,
    unmarked_wasl_uids: Container[str] | None = None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
) -> dict[str, Any]:
    """Return per-category boolean flags + auxiliary fields for one segment.

    Keys:
      - ``failed``, ``audio_bleeding``, ``repetitions``, ``low_confidence``,
        ``low_confidence_detail``, ``low_confidence_v2``, ``hidden_pause``,
        ``missed_waqf``, ``false_split``, ``unmarked_wasl``, ``cross_verse``, ``boundary_adj``,
        ``muqattaat``, ``qalqala``: bool.
      - ``qalqala_letter``: ``str | None`` — populated when ``qalqala`` fires.
      - ``end_of_verse``: bool — reserved (callers pass ``word_counts`` to
        compute this themselves).

    ``probe_failed_uids`` is the set of segment UIDs that failed the
    extraction-time MFA tight-beam probe (the *Low Confidence v2*
    signal). Pass ``None`` to skip the v2 check; pass an empty set when
    the sidecar exists but listed no failures.

    ``hidden_pause_uids`` / ``missed_waqf_uids`` / ``false_split_uids`` /
    ``unmarked_wasl_uids`` are
    the uid sets (or by-uid maps) from the offline boundary-review sidecars; a
    segment flags when its uid is present and the category is not suppressed.
    """
    result: dict[str, Any] = {
        "failed": False,
        "audio_bleeding": False,
        "repetitions": False,
        "low_confidence": False,
        "low_confidence_detail": False,
        "low_confidence_v2": False,
        "hidden_pause": False,
        "missed_waqf": False,
        "false_split": False,
        "unmarked_wasl": False,
        "cross_verse": False,
        "boundary_adj": False,
        "muqattaat": False,
        "qalqala": False,
        "qalqala_letter": None,
        "end_of_verse": False,
        # basmala_amin detection lives in services/validation/detail.py
        # (per-chapter scan + verse 1:1 / 1:7 overlap rule) rather than
        # here. classify_flags exposes the key as False so per-seg ignore
        # tests can iterate every CAN_IGNORE category uniformly.
        "basmala_amin": False,
    }

    matched_ref = seg.get("matched_ref", "")
    confidence = seg.get("confidence", 0.0)

    if not matched_ref:
        # Gate on is_ignored_for so that _all / ignored=True suppresses even
        # the failed flag — the _all marker means "suppress every classification
        # including errors", which is the strongest user-set suppression.
        result["failed"] = not is_ignored_for(seg, "failed")
        return result

    if is_by_ayah and ":" in entry_ref and not seg_belongs_to_entry(matched_ref, entry_ref):
        if not is_suppressed_for(seg, "audio_bleeding"):
            result["audio_bleeding"] = True

    if seg.get("wrap_word_ranges") and not is_suppressed_for(seg, "repetitions"):
        result["repetitions"] = True

    low_conf_cutoff = LOW_CONFIDENCE_THRESHOLDS[riwayah]
    if confidence < low_conf_cutoff and not is_ignored_for(seg, "low_confidence"):
        result["low_confidence"] = True
    if confidence < LOW_CONFIDENCE_DETAIL_THRESHOLD and not is_ignored_for(seg, "low_confidence"):
        result["low_confidence_detail"] = True

    if probe_failed_uids:
        seg_uid = seg.get("segment_uid", "")
        if (
            seg_uid
            and seg_uid in probe_failed_uids
            and not is_suppressed_for(seg, "low_confidence_v2")
        ):
            result["low_confidence_v2"] = True

    seg_uid = seg.get("segment_uid", "")
    if seg_uid and hidden_pause_uids and seg_uid in hidden_pause_uids:
        result["hidden_pause"] = not is_suppressed_for(seg, "hidden_pause")
    if seg_uid and missed_waqf_uids and seg_uid in missed_waqf_uids:
        result["missed_waqf"] = not is_suppressed_for(seg, "missed_waqf")
    if seg_uid and false_split_uids and seg_uid in false_split_uids:
        result["false_split"] = not is_suppressed_for(seg, "false_split")
    if seg_uid and unmarked_wasl_uids and seg_uid in unmarked_wasl_uids:
        result["unmarked_wasl"] = not is_suppressed_for(seg, "unmarked_wasl")

    if s_ayah != e_ayah:
        if not is_ignored_for(seg, "cross_verse"):
            result["cross_verse"] = True
    else:
        result["boundary_adj"] = _check_boundary_adj(
            seg, surah, s_ayah, s_word, e_word, single_word_verses, canonical, riwayah
        )

    # Word-keyed, unlike the boundary-adj exemption above: Warsh merges Hafs's
    # 42:1 and 42:2 into one verse, so BOTH of its first two words are muqattaat
    # openings. For Hafs this is exactly `s_word == 1 and verse in MUQATTAAT`.
    if (surah, s_ayah, s_word) in edition_tables.muqattaat_words(riwayah):
        if not is_ignored_for(seg, "muqattaat"):
            result["muqattaat"] = True

    # Persisted-field short-circuit: ``qalqala_letter`` is stamped at save /
    # extraction time via ``services.qalqala.compute_qalqala_letter`` (the
    # same helper this fall-through path uses for legacy segs without the
    # field — so the value is byte-equivalent either way). Persisted value
    # is a single Arabic letter (one of ``QALQALA_LETTERS``) or ``None``.
    if "qalqala_letter" in seg:
        qalqala_letter = seg["qalqala_letter"]
    else:
        from services.qalqala import (
            compute_qalqala_letter,  # local import: avoid cycle at module load
        )

        qalqala_letter = compute_qalqala_letter(seg, riwayah)
    if qalqala_letter and not is_suppressed_for(seg, "qalqala"):
        result["qalqala"] = True
        result["qalqala_letter"] = qalqala_letter

    return result


# ---------------------------------------------------------------------------
# Public ergonomic wrappers
# ---------------------------------------------------------------------------


def _flags_to_categories(flags: dict[str, Any], *, detail: bool) -> list[str]:
    """Translate a flags dict to a category list in registry-declared order."""
    cats: list[str] = []
    for cat in PER_SEGMENT_CATEGORIES:
        if flags.get(cat):
            cats.append(cat)
    if detail and flags.get("low_confidence_detail") and "low_confidence" not in cats:
        cats.append("low_confidence_detail")
    return cats


def classify_segment(
    seg: dict,
    *,
    entry_ref: str = "",
    is_by_ayah: bool = False,
    surah: int | None = None,
    s_ayah: int | None = None,
    e_ayah: int | None = None,
    s_word: int | None = None,
    e_word: int | None = None,
    single_word_verses: set | None = None,
    canonical: dict | None = None,
    detail: bool = False,
    probe_failed_uids: set | None = None,
    hidden_pause_uids: Container[str] | None = None,
    missed_waqf_uids: Container[str] | None = None,
    false_split_uids: Container[str] | None = None,
    unmarked_wasl_uids: Container[str] | None = None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
) -> list[str]:
    """Classify one segment and return the category list.

    Numeric position parameters (``surah``, ``s_ayah``, ``e_ayah``,
    ``s_word``, ``e_word``) are derived from ``seg["matched_ref"]`` when
    omitted. ``single_word_verses`` defaults to the empty set.

    Pass ``detail=True`` to surface the 1.00 cutoff under the synthetic
    ``low_confidence_detail`` category — used by the validation API to
    distinguish counts (< 0.80) from detail-list items (< 1.00).

    ``riwayah`` is the delivery's SDK slug — every coordinate table, script
    lookup and confidence cutoff is read in that edition. It defaults to Hafs,
    so a caller that has not been threaded yet behaves exactly as before.
    """
    matched_ref = seg.get("matched_ref", "")
    if not matched_ref:
        return ["failed"] if not is_ignored_for(seg, "failed") else []

    if surah is None or s_ayah is None or e_ayah is None or s_word is None or e_word is None:
        parsed = _parse_matched_ref(matched_ref)
        if parsed is None:
            return []
        d_surah, d_s_ayah, d_s_word, d_e_ayah, d_e_word = parsed
        if surah is None:
            surah = d_surah
        if s_ayah is None:
            s_ayah = d_s_ayah
        if s_word is None:
            s_word = d_s_word
        if e_ayah is None:
            e_ayah = d_e_ayah
        if e_word is None:
            e_word = d_e_word

    flags = classify_flags(
        seg,
        entry_ref,
        is_by_ayah,
        surah,
        s_ayah,
        e_ayah,
        s_word,
        e_word,
        single_word_verses or set(),
        canonical,
        probe_failed_uids=probe_failed_uids,
        hidden_pause_uids=hidden_pause_uids,
        missed_waqf_uids=missed_waqf_uids,
        false_split_uids=false_split_uids,
        unmarked_wasl_uids=unmarked_wasl_uids,
        riwayah=riwayah,
    )
    return _flags_to_categories(flags, detail=detail)


def classify_segment_full(
    seg: dict,
    *,
    entry_ref: str = "",
    is_by_ayah: bool = False,
    surah: int | None = None,
    s_ayah: int | None = None,
    e_ayah: int | None = None,
    s_word: int | None = None,
    e_word: int | None = None,
    single_word_verses: set | None = None,
    canonical: dict | None = None,
    detail: bool = False,
    probe_failed_uids: set | None = None,
    hidden_pause_uids: Container[str] | None = None,
    missed_waqf_uids: Container[str] | None = None,
    false_split_uids: Container[str] | None = None,
    unmarked_wasl_uids: Container[str] | None = None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
) -> dict:
    """Like :func:`classify_segment` but returns a dict with auxiliary fields.

    Returned keys: ``categories`` (list[str]), ``qalqala_letter`` (str|None),
    ``low_confidence_detail`` (bool), ``end_of_verse`` (bool).

    ``riwayah`` is the delivery's SDK slug — every coordinate table, script
    lookup and confidence cutoff is read in that edition. It defaults to Hafs,
    so a caller that has not been threaded yet behaves exactly as before.
    """
    matched_ref = seg.get("matched_ref", "")
    if not matched_ref:
        cats = ["failed"] if not is_ignored_for(seg, "failed") else []
        return {
            "categories": cats,
            "qalqala_letter": None,
            "low_confidence_detail": False,
            "end_of_verse": False,
        }

    if surah is None or s_ayah is None or e_ayah is None or s_word is None or e_word is None:
        parsed = _parse_matched_ref(matched_ref)
        if parsed is None:
            return {
                "categories": [],
                "qalqala_letter": None,
                "low_confidence_detail": False,
                "end_of_verse": False,
            }
        d_surah, d_s_ayah, d_s_word, d_e_ayah, d_e_word = parsed
        if surah is None:
            surah = d_surah
        if s_ayah is None:
            s_ayah = d_s_ayah
        if s_word is None:
            s_word = d_s_word
        if e_ayah is None:
            e_ayah = d_e_ayah
        if e_word is None:
            e_word = d_e_word

    flags = classify_flags(
        seg,
        entry_ref,
        is_by_ayah,
        surah,
        s_ayah,
        e_ayah,
        s_word,
        e_word,
        single_word_verses or set(),
        canonical,
        probe_failed_uids=probe_failed_uids,
        hidden_pause_uids=hidden_pause_uids,
        missed_waqf_uids=missed_waqf_uids,
        false_split_uids=false_split_uids,
        unmarked_wasl_uids=unmarked_wasl_uids,
        riwayah=riwayah,
    )
    return {
        "categories": _flags_to_categories(flags, detail=detail),
        "qalqala_letter": flags.get("qalqala_letter"),
        "low_confidence_detail": bool(flags.get("low_confidence_detail")),
        "end_of_verse": bool(flags.get("end_of_verse")),
    }


def classify_entry(
    entry: dict,
    *,
    is_by_ayah: bool | None = None,
    single_word_verses: set | None = None,
    canonical: dict | None = None,
    detail: bool = False,
    probe_failed_uids: set | None = None,
    hidden_pause_uids: Container[str] | None = None,
    missed_waqf_uids: Container[str] | None = None,
    false_split_uids: Container[str] | None = None,
    unmarked_wasl_uids: Container[str] | None = None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
) -> dict[str, dict]:
    """Classify every segment in an entry.

    Returns ``{key: {"categories": [...], "qalqala_letter": str|None}}``,
    keyed by ``segment_uid`` when present and otherwise by ``"_idx:<n>"``.
    ``is_by_ayah`` is inferred from the entry-ref shape (``"S:V"`` form) when
    not supplied.
    """
    entry_ref = entry.get("ref", "")
    if is_by_ayah is None:
        is_by_ayah = ":" in entry_ref

    out: dict[str, dict] = {}
    for i, seg in enumerate(entry.get("segments", [])):
        uid = seg.get("segment_uid") or f"_idx:{i}"
        info = classify_segment_full(
            seg,
            entry_ref=entry_ref,
            is_by_ayah=is_by_ayah,
            single_word_verses=single_word_verses,
            canonical=canonical,
            detail=detail,
            probe_failed_uids=probe_failed_uids,
            hidden_pause_uids=hidden_pause_uids,
            missed_waqf_uids=missed_waqf_uids,
            false_split_uids=false_split_uids,
            unmarked_wasl_uids=unmarked_wasl_uids,
            riwayah=riwayah,
        )
        out[uid] = {
            "categories": info["categories"],
            "qalqala_letter": info["qalqala_letter"],
        }
    return out


__all__ = [
    "is_ignored_for",
    "is_resolved_by_edit",
    "is_suppressed_for",
    "classify_flags",
    "classify_segment",
    "classify_segment_full",
    "classify_entry",
    "_check_boundary_adj",
]
