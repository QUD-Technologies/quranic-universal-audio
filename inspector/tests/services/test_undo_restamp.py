"""Undo is a write, so it owes the same backend-owned stamps a save does.

The frontend's before-snapshot (``snapshotSeg``) carries only the fields the
editor can change, and ``source_ref`` / ``projection_support`` are not among
them — they are not even on the wire model the frontend sees. So restoring a
snapshot verbatim drops them, and for a projected delivery that is not
cosmetic: the timestamps engine refuses a run whose projected segments have no
``source_ref``, which would leave one undo blocking every later alignment.
"""

from __future__ import annotations

import pytest

from domain.command import apply_inverse_patch
from services.reference import editions
from services.segments.undo import _restamp

has_editions = pytest.mark.skipif(
    not editions.available(), reason="qua-domain not installed (Hafs-only runtime)"
)


@pytest.fixture(autouse=True)
def _clear():
    yield
    editions.clear_caches()


def _entries(matched_ref: str) -> list[dict]:
    return [
        {
            "ref": "2",
            "segments": [
                {
                    "segment_uid": "u1",
                    "index_at_save": 0,
                    "time_start": 0,
                    "time_end": 1000,
                    "matched_ref": matched_ref,
                    "confidence": 0.9,
                    "source_ref": "2:4:1-2:4:12",
                    "projection_support": "full",
                    "qalqala_letter": None,
                }
            ],
        }
    ]


def _patch(before_ref: str) -> dict:
    """The shape the frontend sends: a snapshot with no backend-owned fields."""
    return {
        "before": [
            {
                "segment_uid": "u1",
                "index_at_save": 0,
                "time_start": 0,
                "time_end": 1000,
                "matched_ref": before_ref,
                "confidence": 0.9,
                "entry_ref": "2",
                "chapter": 2,
            }
        ],
        "after": [],
        "removedIds": [],
        "insertedIds": [],
        "affectedChapterIds": [2],
    }


@has_editions
def test_restoring_a_snapshot_strips_the_provenance_the_frontend_never_had():
    """Not a bug in the patch path — a fact about it that ``_restamp`` answers."""
    entries = _entries("2:3:1-2:3:12")
    apply_inverse_patch(entries, _patch("2:2:1-2:2:8"), "qalun")

    seg = entries[0]["segments"][0]
    assert seg["matched_ref"] == "2:2:1-2:2:8"
    assert "source_ref" not in seg
    assert "projection_support" not in seg


@has_editions
def test_an_undone_ref_edit_gets_its_hafs_span_back_from_the_new_ref():
    entries = _entries("2:3:1-2:3:12")
    apply_inverse_patch(entries, _patch("2:2:1-2:2:8"), "qalun")

    _restamp(entries, {2}, "qalun")

    seg = entries[0]["segments"][0]
    # Qalun's al-Baqarah 2:2 IS Hafs 2:3, so the span cannot be read off the ref
    # — it has to come from the projection. A stale 2:4 span would have timed
    # the wrong verse's audio; an absent one would have refused the whole run.
    assert seg["source_ref"] == "2:3:1-2:3:8"
    assert seg["projection_support"] == "full"


@has_editions
def test_a_chapter_the_undo_did_not_touch_is_left_alone():
    entries = _entries("2:3:1-2:3:12")
    entries.append({"ref": "113", "segments": [{"segment_uid": "u2", "matched_ref": "113:9:9"}]})

    _restamp(entries, {2}, "qalun")

    # An out-of-edition ref in an untouched chapter would raise if it were
    # stamped, which is what makes this a real scope check and not a tautology.
    assert entries[1]["segments"][0] == {"segment_uid": "u2", "matched_ref": "113:9:9"}


def test_hafs_undo_sheds_provenance_rather_than_deriving_it():
    entries = _entries("2:3:1-2:3:12")

    _restamp(entries, {2}, "hafs")

    seg = entries[0]["segments"][0]
    assert "source_ref" not in seg
    assert "projection_support" not in seg
