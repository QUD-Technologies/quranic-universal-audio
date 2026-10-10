"""Tests for the "missed Basmala" augmentation in ``_build_detail_lists``.

The alignment pipeline (strip_specials post-pass) deletes Basmala segments
from chapters where it detected one. The validation accordion flags the first
segment of every OTHER chapter (≠1, ≠9) as a "possibly missed Basmala" — but
only once the pipeline has stripped at least ``MISSED_BASMALA_FLAG_MIN_DELETED``
basmalas. Reciters who don't recite an inter-chapter basmala produce a
deletion set near zero, and below the threshold the augmentation is suppressed
so the accordion isn't flooded with false positives.
"""

from __future__ import annotations

from config import MISSED_BASMALA_FLAG_MIN_DELETED


def _entry(ref: str, segs: list[dict]) -> dict:
    return {"ref": ref, "segments": segs}


def _seg(matched_ref: str, *, uid: str, t0: int = 0, t1: int = 1000, **extra) -> dict:
    return {
        "segment_uid": uid,
        "matched_ref": matched_ref,
        "time_start": t0,
        "time_end": t1,
        "confidence": 1.0,
        **extra,
    }


def _busy_deleted(extra: set[int] | None = None) -> set[int]:
    """Deletion set whose size clears ``MISSED_BASMALA_FLAG_MIN_DELETED``.

    Uses chapters far above the ones referenced by the fixtures so callers can
    mix in their own chapters without colliding with chapters under test.
    """
    base = {ch for ch in range(50, 50 + MISSED_BASMALA_FLAG_MIN_DELETED)}
    if extra:
        base.update(extra)
    return base


def _call(entries, deleted: set[int] | None):
    """Tiny adapter that fills in the boilerplate args for _build_detail_lists."""
    from services.validation.detail import _build_detail_lists

    word_counts = {(s, a): 5 for s in (1, 2, 3, 5, 9, 17) for a in range(1, 8)}
    return _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts=word_counts,
        probe_failed_uids=None,
        deleted_basmala_chapters=deleted,
    )


def _items_by_chapter(items: list[dict]) -> dict[int, dict]:
    return {it["chapter"]: it for it in items}


def test_missed_basmala_flags_chapters_without_pipeline_deletion_above_threshold():
    """Above the threshold, chapters not in the deletion set are flagged."""
    entries = [
        _entry("2", [_seg("2:1:1-2:1:5", uid="seg-2-0")]),
        _entry("3", [_seg("3:1:1-3:1:5", uid="seg-3-0")]),
    ]
    out = _call(entries, deleted=_busy_deleted(extra={3}))
    by_ch = _items_by_chapter(out["basmala_amin"])
    assert 2 in by_ch
    assert by_ch[2]["seg_index"] == 0
    assert by_ch[2]["segment_uid"] == "seg-2-0"
    assert by_ch[2].get("missed_basmala") is True
    # Chapter 3 was deleted, so it must NOT be flagged.
    assert 3 not in by_ch


def test_missed_basmala_suppressed_below_threshold():
    """Below the threshold the augmentation is silenced entirely."""
    entries = [
        _entry("2", [_seg("2:1:1-2:1:5", uid="seg-2-0")]),
        _entry("3", [_seg("3:1:1-3:1:5", uid="seg-3-0")]),
    ]
    # ``deleted`` is non-empty but its size is well under the threshold.
    out = _call(entries, deleted={5})
    by_ch = _items_by_chapter(out["basmala_amin"])
    assert 2 not in by_ch
    assert 3 not in by_ch


def test_missed_basmala_excludes_chapters_1_and_9():
    """Chapter 1 (Al-Fatiha) and 9 (At-Tawbah) are always excluded."""
    entries = [
        _entry("1", [_seg("1:1:1-1:1:4", uid="seg-1-0")]),
        _entry("9", [_seg("9:1:1-9:1:5", uid="seg-9-0")]),
    ]
    out = _call(entries, deleted=_busy_deleted())
    by_ch = _items_by_chapter(out["basmala_amin"])
    # Chapter 9 is never a missed-Basmala candidate (no Basmala there).
    assert 9 not in by_ch
    # Chapter 1 has the canonical 1:1 entry already, but NOT a missed-Basmala
    # entry — its presence in basmala_amin (if any) must NOT carry the marker.
    ch1 = by_ch.get(1)
    if ch1 is not None:
        assert not ch1.get("missed_basmala")


def test_missed_basmala_inert_when_param_is_none():
    """Passing ``None`` (legacy callers) suppresses the augmentation."""
    entries = [
        _entry("2", [_seg("2:1:1-2:1:5", uid="seg-2-0")]),
        _entry("17", [_seg("17:1:1-17:1:5", uid="seg-17-0")]),
    ]
    out = _call(entries, deleted=None)
    # The legacy 1:1 / 1:7 detection isn't triggered (no chapter 1 entries),
    # so basmala_amin should be empty rather than carrying chapter 2 + 17.
    assert out["basmala_amin"] == []


def test_missed_basmala_captures_first_seg_even_when_alignment_failed():
    """First seg with empty matched_ref still flags the chapter."""
    entries = [
        _entry(
            "5",
            [
                _seg("", uid="seg-5-0", t0=0, t1=2000),
                _seg("5:1:1-5:1:5", uid="seg-5-1", t0=2000, t1=4000),
            ],
        ),
    ]
    out = _call(entries, deleted=_busy_deleted())
    by_ch = _items_by_chapter(out["basmala_amin"])
    assert 5 in by_ch
    assert by_ch[5]["segment_uid"] == "seg-5-0"  # the failed first seg, not 5-1
    assert by_ch[5]["seg_index"] == 0


def test_basmala_amin_order_is_first11_last17_rest11_then_missed():
    """Order: first 1:1 seg → last 1:7 seg → remaining 1:1 segs → missed basmalas."""
    entries = [
        _entry(
            "1",
            [
                # Two distinct 1:1 segs (first half / second half of the Basmala).
                _seg("1:1:1-1:1:2", uid="seg-1-11a", t0=0, t1=1000),
                _seg("1:1:3-1:1:4", uid="seg-1-11b", t0=1000, t1=2000),
                # Two distinct 1:7 segs — only the LAST one should appear up front.
                _seg("1:7:1-1:7:3", uid="seg-1-17a", t0=10_000, t1=11_000),
                _seg("1:7:4-1:7:5", uid="seg-1-17b", t0=11_000, t1=12_000),
            ],
        ),
        _entry("2", [_seg("2:1:1-2:1:5", uid="seg-2-0")]),
        _entry("3", [_seg("3:1:1-3:1:5", uid="seg-3-0")]),
    ]
    out = _call(entries, deleted=_busy_deleted())
    uids = [it["segment_uid"] for it in out["basmala_amin"]]
    assert uids == [
        "seg-1-11a",  # first 1:1 seg
        "seg-1-17b",  # last 1:7 seg
        "seg-1-11b",  # remaining 1:1 segs
        "seg-2-0",  # missed basmalas
        "seg-3-0",
    ]


def test_basmala_amin_respects_ignored_category_on_11_and_17_segs():
    """Segs carrying ``ignored_categories=["basmala_amin"]`` drop out of the list."""
    entries = [
        _entry(
            "1",
            [
                _seg(
                    "1:1:1-1:1:4",
                    uid="seg-1-11",
                    t0=0,
                    t1=1000,
                    ignored_categories=["basmala_amin"],
                ),
                _seg(
                    "1:7:1-1:7:5",
                    uid="seg-1-17",
                    t0=2000,
                    t1=3000,
                    ignored_categories=["basmala_amin"],
                ),
            ],
        ),
    ]
    out = _call(entries, deleted=None)
    assert out["basmala_amin"] == []


def test_basmala_amin_respects_ignored_category_on_missed_chapter_first_seg():
    """A chapter's first seg with the category ignored is skipped from missed list."""
    entries = [
        _entry(
            "2",
            [
                _seg(
                    "2:1:1-2:1:5",
                    uid="seg-2-0",
                    ignored_categories=["basmala_amin"],
                ),
            ],
        ),
        _entry("3", [_seg("3:1:1-3:1:5", uid="seg-3-0")]),
    ]
    out = _call(entries, deleted=_busy_deleted())
    by_ch = _items_by_chapter(out["basmala_amin"])
    assert 2 not in by_ch
    assert 3 in by_ch  # control: chapter 3's first seg isn't ignored
