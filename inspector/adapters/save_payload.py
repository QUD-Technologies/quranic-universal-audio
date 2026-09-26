"""Adapter: incoming save payload -> canonical segment dicts.

Provides ``make_seg`` — extracted from ``services/save.py:_make_seg`` — as the
single lookup+merge logic for building a canonical segment from a payload
segment dict plus existing on-disk segments.  ``services/save.py`` calls this
adapter internally; the route shape is unchanged (MUST-1).
"""

from __future__ import annotations

from services.validation.registry import filter_persistent_ignores
from utils.references import normalize_ref
from utils.repetitions import is_wrap_consistent


def make_seg(
    s: dict,
    existing_by_time: dict,
    existing_by_uid: dict,
    word_counts: dict,
) -> dict:
    """Build a canonical segment dict, preserving fields from an existing match.

    Lookup priority for the existing segment:
    1. Time-key match ``(time_start, time_end)``.
    2. UID match (``segment_uid`` from the payload).

    MUST-7 semantics for ``ignored_categories``:
    - Key present in payload (including ``[]``) → apply ``filter_persistent_ignores``.
    - Key absent → preserve the existing entry-side value.
    - Legacy ``ignored=true`` (no ``ignored_categories``) → emit ``["_all"]``.
    """
    existing = existing_by_time.get((s.get("time_start", 0), s.get("time_end", 0)), {})
    if not existing:
        uid = s.get("segment_uid", "")
        if uid:
            existing = existing_by_uid.get(uid, {})

    seg_uid = s.get("segment_uid", "") or existing.get("segment_uid", "")
    matched_ref = normalize_ref(s.get("matched_ref", ""), word_counts)

    # Migration #5: matched_text + phonemes_asr are no longer persisted in
    # detailed.json. matched_text is derivable via dk_text_for_ref; phonemes_asr
    # was retired entirely (DetailedSegment pre-validator strips it on read).
    # The FE save payload also omits them — see execute.ts SaveSegmentPayloadFull.
    result: dict = {
        "segment_uid": seg_uid,
        "time_start": s.get("time_start", 0),
        "time_end": s.get("time_end", 0),
        "matched_ref": matched_ref,
        "confidence": s.get("confidence", 0.0),
    }

    # Repetition metadata is FE-authoritative on full_replace: the FE knows
    # whether a seg currently represents a multi-pass reading (split resolves
    # repetitions into independent children, ref-edits can change the picture
    # entirely). Falling back to ``existing`` here let the parent's wrap leak
    # into split children, re-tagging them as repetitions and feeding wrong
    # refs to MFA on Auto Split. Trust the payload — if the FE omits the
    # field, drop it.
    #
    # Defense-in-depth geometry check: if a (current or future) client sends
    # a wrap that doesn't fit the matched_ref (stale wrap from inheritance,
    # or corrupted jump_to/from/end ordering), drop it rather than persist
    # bad data. The check uses the same predicate as the cleanup script so
    # behaviour stays consistent.
    wrap = s.get("wrap_word_ranges")
    if wrap and is_wrap_consistent(matched_ref, wrap, word_counts):
        result["wrap_word_ranges"] = wrap
        # Migration #5: has_repeated_words is dropped — it was a tautology of
        # ``bool(wrap_word_ranges)`` and the classifier never read it (only
        # wrap_word_ranges drives the ``repetitions`` category).

    if "ignored_categories" in s:
        ic = filter_persistent_ignores(s.get("ignored_categories") or [])
        result["ignored_categories"] = list(ic)
    else:
        ic = filter_persistent_ignores(existing.get("ignored_categories") or [])
        if ic:
            result["ignored_categories"] = ic

    if (
        "ignored_categories" not in result
        and "ignored_categories" not in s
        and (s.get("ignored") or existing.get("ignored"))
    ):
        result["ignored_categories"] = ["_all"]

    # is_wasl boundary annotation. Payload-authoritative when set (the FE
    # sends it on every seg); otherwise inherit from the existing entry-side
    # value. Omit from serialization when False so the field stays absent on
    # untouched segs.
    if s.get("is_wasl") is not None:
        if bool(s.get("is_wasl")):
            result["is_wasl"] = True
    elif bool(existing.get("is_wasl")):
        result["is_wasl"] = True

    # Flag thread is never carried in the save payload — it is mutated only
    # via flag ops. Inherit any existing flag so a structural full_replace
    # doesn't wipe it.
    existing_flag = existing.get("flag")
    if existing_flag:
        result["flag"] = existing_flag

    # Historical review samples may carry word-level boundaries. They are
    # still valid when a chapter-wide save rebuilds an otherwise untouched
    # row, but a trim or reference edit invalidates their geometry/content.
    if (
        existing.get("word_timings")
        and s.get("time_start", 0) == existing.get("time_start")
        and s.get("time_end", 0) == existing.get("time_end")
        and matched_ref == existing.get("matched_ref")
    ):
        result["word_timings"] = existing["word_timings"]

    # Coordinate provenance (multi-riwayah) is deliberately NOT carried here.
    # The FE never sends it — the editor works in the delivery edition's
    # coordinates and knows nothing about the Hafs source span — and inheriting
    # it would keep a value the new ``matched_ref`` has invalidated. It is
    # re-derived from the ref by ``services.segments.stamping.stamp_segment``,
    # which every save path already runs and which knows the riwayah.

    return result


def build_seg_lookups(matching: list[dict]) -> tuple[dict, dict]:
    """Build ``(by_time, by_uid)`` lookups over matching entry segments.

    Used by the full-replace and patch save paths to locate existing
    segments for field preservation.
    """
    existing_by_time: dict = {}
    existing_by_uid: dict = {}
    for e in matching:
        for seg in e.get("segments", []):
            key = (seg.get("time_start", 0), seg.get("time_end", 0))
            existing_by_time[key] = seg
            uid = seg.get("segment_uid", "")
            if uid:
                existing_by_uid[uid] = seg
    return existing_by_time, existing_by_uid
