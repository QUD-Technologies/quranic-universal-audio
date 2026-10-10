"""Per-category classifier tests, parametrized over fixtures.

Each test loads a fixture, runs the unified backend classifier, and
compares per-segment categories to the baseline in
``expected/<fixture>.classify.json``.
"""

from __future__ import annotations

import pytest

from services.validation.classifier import classify_segment

FIXTURES = ["112-ikhlas", "113-falaq", "synthetic-structural", "synthetic-classifier"]


def _classify(seg: dict, **ctx) -> list[str]:
    return classify_segment(seg, **ctx)


@pytest.mark.parametrize("fixture_name", FIXTURES, ids=FIXTURES)
def test_each_category_classified_in_expected_segments(fixture_name, load_fixture, load_expected):
    fixture = load_fixture(fixture_name)
    expected = load_expected(fixture_name, "classify")
    by_uid = expected["by_segment_uid"]

    from services.validation.classifier import classify_entry  # type: ignore

    for entry in fixture["entries"]:
        results = classify_entry(entry)
        for seg in entry["segments"]:
            uid = seg.get("segment_uid")
            if uid not in by_uid:
                continue
            actual = sorted(results.get(uid, {}).get("categories", []))
            want = sorted(by_uid[uid].get("categories", []))
            assert actual == want, (
                f"{fixture_name}::{uid}: expected categories {want!r}, got {actual!r}"
            )


def test_low_confidence_threshold_is_0_80():
    """Segments with confidence ∈ [0.79, 0.81] flip on/off correctly around 0.80."""
    base = {
        "matched_ref": "1:1:1-1:1:1",
    }
    below = _classify({**base, "confidence": 0.79}, entry_ref="1", is_by_ayah=False)
    on_threshold = _classify({**base, "confidence": 0.80}, entry_ref="1", is_by_ayah=False)
    above = _classify({**base, "confidence": 0.81}, entry_ref="1", is_by_ayah=False)

    assert "low_confidence" in below
    assert "low_confidence" not in on_threshold
    assert "low_confidence" not in above


def test_low_confidence_detail_threshold_is_1_00():
    """Segments with confidence < 1.00 fall in the detail tier; confidence == 1.00 do not."""
    base = {
        "matched_ref": "1:1:1-1:1:1",
    }
    just_below = _classify(
        {**base, "confidence": 0.999}, entry_ref="1", is_by_ayah=False, detail=True
    )
    perfect = _classify({**base, "confidence": 1.00}, entry_ref="1", is_by_ayah=False, detail=True)

    assert "low_confidence_detail" in just_below
    assert "low_confidence_detail" not in perfect


def test_audio_bleeding_uses_seg_belongs_to_entry():
    """Fixture segment with entry_ref 1:1 + matched_ref 1:2:1 flips audio_bleeding=true."""
    seg = {
        "matched_ref": "1:2:1-1:2:1",
        "confidence": 1.0,
    }
    result = _classify(seg, entry_ref="1:1", is_by_ayah=True)
    assert "audio_bleeding" in result


def test_repetitions_only_wrap_word_ranges():
    """Segment with has_repeated_words=true but no wrap_word_ranges does NOT classify as repetitions (tie-breaker B-1)."""
    seg = {
        "matched_ref": "1:1:1-1:1:1",
        "confidence": 1.0,
        "has_repeated_words": True,
    }
    result = _classify(seg, entry_ref="1", is_by_ayah=False)
    assert "repetitions" not in result, (
        "has_repeated_words alone should not classify as repetitions; wrap_word_ranges is required (see bug-log B-1)"
    )

    seg_with_wrap = dict(seg, wrap_word_ranges=[[1, 1]])
    result2 = _classify(seg_with_wrap, entry_ref="1", is_by_ayah=False)
    assert "repetitions" in result2


def test_qalqala_letter_field_populated():
    """Qalqala-classified segment carries a qalqala_letter field with the actual final letter."""
    from services.validation.classifier import classify_segment_full  # type: ignore

    seg = {
        "matched_ref": "112:1:1-112:1:4",
        "confidence": 1.0,
    }
    result = classify_segment_full(seg, entry_ref="112", is_by_ayah=False)
    assert "qalqala" in result.get("categories", [])
    assert result.get("qalqala_letter") == "د"


def test_cross_verse_classifies_regardless_of_is_wasl():
    """The is_wasl boundary annotation is positive metadata, not a suppression.
    A cross-verse segment with is_wasl=true still classifies as cross_verse —
    the wasl flag describes that the reciter intentionally recited across the
    boundary, but the segment is still cross-verse and still surfaces in the
    accordion.
    """
    base = {
        "matched_ref": "1:5:7-1:6:1",
        "confidence": 1.0,
    }
    plain = _classify(base, entry_ref="1", is_by_ayah=False)
    waslful = _classify({**base, "is_wasl": True}, entry_ref="1", is_by_ayah=False)

    assert "cross_verse" in plain
    assert "cross_verse" in waslful


def test_muqattaat_only_first_word_of_verse():
    """A muqattaat verse with s_word=2 does NOT classify; s_word=1 does."""
    base_seg = {
        "confidence": 1.0,
    }
    word1 = dict(base_seg, matched_ref="2:1:1-2:1:1")
    word2 = dict(base_seg, matched_ref="2:1:2-2:1:2")

    r1 = _classify(word1, entry_ref="2", is_by_ayah=False)
    r2 = _classify(word2, entry_ref="2", is_by_ayah=False)

    assert "muqattaat" in r1
    assert "muqattaat" not in r2


def test_basmala_amin_detail_uses_last_segment_overlapping_1_7():
    """Basmala + Amin shows 1:1 plus only the latest card overlapping 1:7."""
    from services.validation.detail import _build_detail_lists  # type: ignore

    entries = [
        {
            "ref": "1",
            "segments": [
                {
                    "segment_uid": "one-one",
                    "matched_ref": "1:1:1-1:1:1",
                    "confidence": 1.0,
                    "time_start": 0,
                    "time_end": 1000,
                },
                {
                    "segment_uid": "one-seven-a",
                    "matched_ref": "1:7:1-1:7:2",
                    "confidence": 1.0,
                    "time_start": 1000,
                    "time_end": 2000,
                },
                {
                    "segment_uid": "one-seven-b",
                    "matched_ref": "1:7:3-1:7:4",
                    "confidence": 1.0,
                    "time_start": 2000,
                    "time_end": 3000,
                },
            ],
        }
    ]

    detail = _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts={(1, ayah): (1 if ayah < 7 else 4) for ayah in range(1, 8)},
    )

    assert [item["segment_uid"] for item in detail["basmala_amin"]] == [
        "one-one",
        "one-seven-b",
    ]


def test_basmala_amin_detail_does_not_promote_when_last_1_7_suppressed():
    """Resolving the canonical last-1:7 candidate must NOT promote the
    previous 1:7-overlapping seg as the new amin flag — that's whack-a-mole.

    Reproduces the regression where, after the user edits the last seg of
    1:7 from the basmala accordion, validation re-raises the flag against
    the seg immediately before it.
    """
    from services.validation.detail import _build_detail_lists  # type: ignore

    entries = [
        {
            "ref": "1",
            "segments": [
                {
                    "segment_uid": "one-one",
                    "matched_ref": "1:1:1-1:1:1",
                    "confidence": 1.0,
                    "time_start": 0,
                    "time_end": 1000,
                },
                {
                    "segment_uid": "one-seven-a",
                    "matched_ref": "1:7:1-1:7:2",
                    "confidence": 1.0,
                    "time_start": 1000,
                    "time_end": 2000,
                },
                {
                    # Canonical last-1:7 candidate, marked resolved-by-edit:
                    "segment_uid": "one-seven-b",
                    "matched_ref": "1:7:3-1:7:4",
                    "confidence": 1.0,
                    "time_start": 2000,
                    "time_end": 3000,
                    "_resolved_by_edit": {"basmala_amin"},
                },
            ],
        }
    ]

    detail = _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts={(1, ayah): (1 if ayah < 7 else 4) for ayah in range(1, 8)},
    )

    # Only 1:1 should remain. The 1:7 slot is empty because the canonical
    # last-1:7 seg is suppressed; "one-seven-a" must NOT be promoted.
    assert [item["segment_uid"] for item in detail["basmala_amin"]] == ["one-one"]


def test_basmala_amin_detail_drops_first_1_1_when_suppressed():
    """Suppressing the canonical first-1:1 seg leaves the slot empty too."""
    from services.validation.detail import _build_detail_lists  # type: ignore

    entries = [
        {
            "ref": "1",
            "segments": [
                {
                    "segment_uid": "one-one",
                    "matched_ref": "1:1:1-1:1:1",
                    "confidence": 1.0,
                    "time_start": 0,
                    "time_end": 1000,
                    "_resolved_by_edit": {"basmala_amin"},
                },
                {
                    "segment_uid": "one-seven",
                    "matched_ref": "1:7:1-1:7:4",
                    "confidence": 1.0,
                    "time_start": 1000,
                    "time_end": 2000,
                },
            ],
        }
    ]

    detail = _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts={(1, ayah): (1 if ayah < 7 else 4) for ayah in range(1, 8)},
    )

    assert [item["segment_uid"] for item in detail["basmala_amin"]] == ["one-seven"]


def test_basmala_amin_detail_omits_neighboring_fatiha_verses():
    """The accordion includes 1:1 and 1:7 but not 1:2 through 1:6."""
    from services.validation.detail import _build_detail_lists  # type: ignore

    entries = [
        {
            "ref": "1",
            "segments": [
                {
                    "segment_uid": f"one-{ayah}",
                    "matched_ref": f"1:{ayah}:1-1:{ayah}:1",
                    "confidence": 1.0,
                    "time_start": ayah * 1000,
                    "time_end": (ayah + 1) * 1000,
                }
                for ayah in range(1, 8)
            ],
        }
    ]

    detail = _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts={(1, ayah): 1 for ayah in range(1, 8)},
    )

    assert [item["segment_uid"] for item in detail["basmala_amin"]] == ["one-1", "one-7"]


def _missing_words_for_entries(
    entries: list[dict], word_counts: dict[tuple[int, int], int]
) -> list[dict]:
    from services.validation._missing import _build_missing_words  # type: ignore
    from services.validation.detail import _build_detail_lists  # type: ignore

    detail = _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts=word_counts,
    )
    return _build_missing_words(detail["verse_segments"], word_counts, detail["sequence_gaps"])


def test_missing_words_includes_forward_jump_even_when_words_are_covered_later():
    """A forward continuation after backtrack must not skip words."""
    entries = [
        {
            "ref": "1",
            "segments": [
                {"matched_ref": "1:1:1-1:1:10", "confidence": 1.0},
                {"matched_ref": "1:1:5-1:1:7", "confidence": 1.0},
                {"matched_ref": "1:1:11-1:1:15", "confidence": 1.0},
            ],
        }
    ]

    missing = _missing_words_for_entries(entries, {(1, 1): 15})

    assert missing == [
        {
            "verse_key": "1:1",
            "chapter": 1,
            "segment_uid": None,
            "msg": "missing words in sequence: [8, 9, 10]",
            "missing_words": [8, 9, 10],
            "seg_indices": [1, 2],
            "sequence_gap": True,
        }
    ]


def test_missing_words_includes_whole_verse_forward_jump():
    """Whole-verse skips still surface in missing_words at the jump boundary."""
    entries = [
        {
            "ref": "1",
            "segments": [
                {"matched_ref": "1:1:1-1:1:4", "confidence": 1.0},
                {"matched_ref": "1:3:1-1:3:4", "confidence": 1.0},
            ],
        }
    ]

    missing = _missing_words_for_entries(entries, {(1, 1): 4, (1, 2): 3, (1, 3): 4})

    assert len(missing) == 1
    assert missing[0]["verse_key"] == "1:2"
    assert missing[0]["missing_words"] == [1, 2, 3]
    assert missing[0]["seg_indices"] == [0, 1]
    assert missing[0]["sequence_gap"] is True


def test_missing_words_allows_backward_repetition_without_forward_skip():
    """Backtracking is valid unless a later segment jumps over unseen words."""
    entries = [
        {
            "ref": "1",
            "segments": [
                {"matched_ref": "1:1:1-1:1:5", "confidence": 1.0},
                {"matched_ref": "1:1:1-1:1:5", "confidence": 1.0},
                {"matched_ref": "1:1:6-1:1:10", "confidence": 1.0},
            ],
        }
    ]

    assert _missing_words_for_entries(entries, {(1, 1): 10}) == []


def test_missing_words_dedupes_coverage_and_sequence_gap_for_same_range():
    """Coverage missing words and order-gap missing words share one card when identical."""
    entries = [
        {
            "ref": "1",
            "segments": [
                {"matched_ref": "1:1:1-1:1:1", "confidence": 1.0},
                {"matched_ref": "1:1:5-1:1:7", "confidence": 1.0},
            ],
        }
    ]

    missing = _missing_words_for_entries(entries, {(1, 1): 7})

    assert len(missing) == 1
    assert missing[0]["msg"] == "missing words: [2, 3, 4]"
    assert missing[0]["missing_words"] == [2, 3, 4]
    assert "sequence_gap" not in missing[0]


def test_missing_words_splits_cross_verse_partial_forward_jump():
    """Cross-verse order gaps are split into per-verse missing-word cards."""
    entries = [
        {
            "ref": "1",
            "segments": [
                {"matched_ref": "1:1:1-1:1:2", "confidence": 1.0},
                {"matched_ref": "1:3:3-1:3:4", "confidence": 1.0},
                {"matched_ref": "1:1:3-1:1:4", "confidence": 1.0},
                {"matched_ref": "1:2:1-1:2:3", "confidence": 1.0},
                {"matched_ref": "1:3:1-1:3:2", "confidence": 1.0},
            ],
        }
    ]

    missing = _missing_words_for_entries(entries, {(1, 1): 4, (1, 2): 3, (1, 3): 4})

    sequence = [item for item in missing if item.get("sequence_gap")]
    assert sequence == [
        {
            "verse_key": "1:1",
            "chapter": 1,
            "segment_uid": None,
            "msg": "missing words in sequence: [3, 4]",
            "missing_words": [3, 4],
            "seg_indices": [0, 1],
            "sequence_gap": True,
        },
        {
            "verse_key": "1:2",
            "chapter": 1,
            "segment_uid": None,
            "msg": "missing words in sequence: [1, 2, 3]",
            "missing_words": [1, 2, 3],
            "seg_indices": [0, 1],
            "sequence_gap": True,
        },
        {
            "verse_key": "1:3",
            "chapter": 1,
            "segment_uid": None,
            "msg": "missing words in sequence: [1, 2]",
            "missing_words": [1, 2],
            "seg_indices": [0, 1],
            "sequence_gap": True,
        },
    ]


def test_missing_words_orders_sequence_gaps_by_quran_position():
    entries = [
        {
            "ref": "1",
            "segments": [
                {"matched_ref": "1:1:1-1:1:1", "confidence": 1.0},
                {"matched_ref": "1:3:2-1:3:2", "confidence": 1.0},
                {"matched_ref": "1:2:1-1:2:1", "confidence": 1.0},
                {"matched_ref": "1:3:1-1:3:1", "confidence": 1.0},
            ],
        }
    ]

    missing = _missing_words_for_entries(entries, {(1, 1): 1, (1, 2): 1, (1, 3): 2})

    assert [(item["verse_key"], item["missing_words"]) for item in missing] == [
        ("1:2", [1]),
        ("1:3", [1]),
    ]
