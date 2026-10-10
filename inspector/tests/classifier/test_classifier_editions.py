"""The classifier under a non-Hafs edition.

Every table it reads moves between editions. These cases are the ones where a
Hafs-keyed table gives a DIFFERENT answer, so each is a bug the edition
parameter exists to prevent — not a restatement of the Hafs behaviour.
"""

from __future__ import annotations

import pytest

from services.reference import editions
from services.validation.classifier import classify_segment

pytestmark = pytest.mark.skipif(
    not editions.available(), reason="qua-domain not installed (Hafs-only runtime)"
)


@pytest.fixture(autouse=True)
def _clear_edition_caches():
    yield
    editions.clear_caches()


def seg(ref: str, confidence: float = 1.0) -> dict:
    return {"segment_uid": "u1", "matched_ref": ref, "confidence": confidence}


# ---------------------------------------------------------------------------
# muqattaat — Warsh merges Hafs 42:1 + 42:2 into one two-word verse
# ---------------------------------------------------------------------------


def test_the_second_opening_of_the_merged_shura_verse_is_still_muqattaat():
    # Hafs 42:2 (`ayn-sin-qaf`) is Warsh 42:1:2. A Hafs-keyed rule fires only on
    # word 1, so this opening would be classified as a one-word fragment.
    assert "muqattaat" in classify_segment(seg("42:1:2-42:1:2"), riwayah="warsh")


def test_the_same_ref_is_not_muqattaat_under_hafs():
    # Guards the test above against passing for the wrong reason: 42:1:2 does
    # not exist as an opening in Hafs, where 42:1 is one word.
    assert "muqattaat" not in classify_segment(seg("42:1:2-42:1:2"), riwayah="hafs")


def test_a_hafs_muqattaat_ref_is_not_one_in_warsh():
    # Hafs 42:2:1 is not a Warsh coordinate at all.
    assert "muqattaat" in classify_segment(seg("42:2:1-42:2:1"), riwayah="hafs")
    assert "muqattaat" not in classify_segment(seg("42:2:1-42:2:1"), riwayah="warsh")


# ---------------------------------------------------------------------------
# confidence cutoff
# ---------------------------------------------------------------------------


def test_the_low_confidence_cutoff_is_read_per_edition(monkeypatch):
    from config import LOW_CONFIDENCE_THRESHOLDS

    monkeypatch.setitem(LOW_CONFIDENCE_THRESHOLDS, "warsh", 0.5)
    lowish = seg("2:2:1-2:2:3", confidence=0.6)
    assert "low_confidence" in classify_segment(lowish, riwayah="hafs")
    assert "low_confidence" not in classify_segment(lowish, riwayah="warsh")
