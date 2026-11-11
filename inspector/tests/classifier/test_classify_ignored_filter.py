"""is_ignored_for honored uniformly across per-segment categories.

These assertions describe behavior that the existing classifier already
implements; they pass at Phase 0 and continue to pass through every phase.
"""

from __future__ import annotations

import pytest

from tests.conftest import CAN_IGNORE_CATEGORIES


@pytest.mark.parametrize("category", CAN_IGNORE_CATEGORIES, ids=CAN_IGNORE_CATEGORIES)
def test_ignored_categories_excludes_from_classification(category):
    """A segment with C in ignored_categories is not classified as C."""
    from services.validation.classifier import classify_flags, is_ignored_for

    seg = {
        "matched_ref": "112:1:1-112:1:4",
        "confidence": 0.5,
        "wrap_word_ranges": [[1, 1]],
        "ignored_categories": [category],
    }
    assert is_ignored_for(seg, category) is True

    flags = classify_flags(
        seg,
        entry_ref="112",
        is_by_ayah=False,
        surah=112,
        s_ayah=1,
        e_ayah=1,
        s_word=1,
    )
    assert flags.get(category) is False, (
        f"{category}: ignored_categories filter not honored — flag={flags.get(category)!r}"
    )


def test_all_marker_excludes_all():
    """A segment with ['_all'] in ignored_categories is not classified as any per-segment category."""
    from services.validation.classifier import classify_flags

    seg = {
        "matched_ref": "112:1:1-112:1:4",
        "confidence": 0.5,
        "wrap_word_ranges": [[1, 1]],
        "ignored_categories": ["_all"],
    }
    flags = classify_flags(
        seg,
        entry_ref="112",
        is_by_ayah=False,
        surah=112,
        s_ayah=1,
        e_ayah=1,
        s_word=1,
    )
    for category in CAN_IGNORE_CATEGORIES:
        assert flags.get(category) is False, (
            f"_all marker not honored for {category}: {flags.get(category)!r}"
        )


def test_legacy_ignored_boolean_treated_as_all():
    """A segment with ignored=true (legacy form, no ignored_categories) is treated as _all."""
    from services.validation.classifier import classify_flags, is_ignored_for

    seg = {
        "matched_ref": "112:1:1-112:1:4",
        "confidence": 0.5,
        "wrap_word_ranges": [[1, 1]],
        "ignored": True,
    }
    for category in CAN_IGNORE_CATEGORIES:
        assert is_ignored_for(seg, category) is True

    flags = classify_flags(
        seg,
        entry_ref="112",
        is_by_ayah=False,
        surah=112,
        s_ayah=1,
        e_ayah=1,
        s_word=1,
    )
    for category in CAN_IGNORE_CATEGORIES:
        assert flags.get(category) is False


def test_an_unknown_ignored_category_suppresses_nothing():
    """Published segments may list ``boundary_adj``; it names no category."""
    from services.validation.classifier import classify_segment

    seg = {
        "matched_ref": "112:1:1-112:1:4",
        "confidence": 0.5,
        "is_boundary_adj": True,
        "ignored_categories": ["boundary_adj"],
    }
    assert "low_confidence" in classify_segment(seg)
