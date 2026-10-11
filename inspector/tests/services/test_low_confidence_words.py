"""Low Confidence v2 flagged words: ``load_probe_v2`` reads them off the
sidecar's ``by_uid`` block and ``_build_detail_lists`` ships them on each v2
item as ``words``."""

from __future__ import annotations

import pytest

from qua_shared.schemas.wire.seg import SegValLowConfidenceV2Item
from services.storage import cache, data_dir
from services.storage.data_loader import load_probe_v2
from services.validation.detail import _build_detail_lists

SLUG = "lc-words-fixture"


@pytest.fixture
def sidecar(monkeypatch):
    """Serve ``doc`` as the reciter's sidecar and count the reads."""
    reads: list[str] = []
    state: dict = {"doc": None}

    def read(slug: str):
        reads.append(slug)
        return state["doc"]

    monkeypatch.setattr(data_dir, "read_low_confidence_doc", read)
    cache._seg_probe_v2.pop(SLUG)
    yield state, reads
    cache._seg_probe_v2.pop(SLUG)


def test_load_probe_v2_normalises_words_and_caches(sidecar):
    state, reads = sidecar
    state["doc"] = {
        "_meta": {"model": "neural"},
        "failures": ["u1", "u2", "u3", "u4"],
        "by_uid": {
            "u1": {
                "reason": "word",
                "words": [
                    {"ref": "2:5:3", "fit": -6.1, "sound_fit": -9},
                    {"ref": "2:5:4", "fit": True, "sound_fit": "bad"},
                    {"fit": -3.0},
                ],
            },
            "u2": {"reason": "word", "words": [{"ref": "2:6:1"}]},
            "u3": {"reason": "no path", "words": []},
            "u4": {"reason": "fit"},
        },
    }

    failed, meta, words = load_probe_v2(SLUG)

    assert failed == {"u1", "u2", "u3", "u4"}
    assert meta == {"model": "neural"}
    assert words == {
        "u1": [{"ref": "2:5:3", "fit": -6.1, "sound_fit": -9.0}, {"ref": "2:5:4"}],
        "u2": [{"ref": "2:6:1"}],
    }
    assert load_probe_v2(SLUG) == (failed, meta, words)
    assert reads == [SLUG]


def test_load_probe_v2_absent_sidecar_is_cached_empty(sidecar):
    _, reads = sidecar
    assert load_probe_v2(SLUG) == (set(), None, {})
    assert load_probe_v2(SLUG) == (set(), None, {})
    assert reads == [SLUG]


def _seg(ref: str, uid: str) -> dict:
    return {
        "segment_uid": uid,
        "matched_ref": ref,
        "time_start": 0,
        "time_end": 1000,
        "confidence": 1.0,
    }


def test_low_confidence_v2_items_carry_their_words():
    entries = [{"ref": "2", "segments": [_seg("2:5:1-2:5:6", "u1"), _seg("2:6:1-2:6:4", "u2")]}]
    words = {"u1": [{"ref": "2:5:3", "fit": -6.1, "sound_fit": -9.0}]}

    out = _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts={(2, a): 6 for a in range(1, 8)},
        probe_failed_uids={"u1", "u2"},
        probe_words=words,
    )

    by_uid = {it["segment_uid"]: it for it in out["low_confidence_v2"]}
    assert by_uid["u1"]["words"] == words["u1"]
    assert by_uid["u2"]["words"] == []
    for item in by_uid.values():
        SegValLowConfidenceV2Item.model_validate(item)
