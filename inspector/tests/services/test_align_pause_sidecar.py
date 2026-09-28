"""``missed_waqf_v2`` — Low Confidence Waqf items built from the aligner's lattice pauses."""

from __future__ import annotations

from domain.identity import derive_uid
from services.admin.align_pipeline import adapt, pause_sidecar


def _row(t0, t1, ref_from, ref_to, *, pauses=None, words=None, **extra):
    return {
        "time_from": t0,
        "time_to": t1,
        "ref_from": ref_from,
        "ref_to": ref_to,
        "confidence": 0.9,
        "kind": "quran",
        **({"pauses": pauses} if pauses is not None else {}),
        **({"words": words} if words is not None else {}),
        **extra,
    }


def _pause(after_ref, gain=1.4, sep=2.0):
    return {"after_ref": after_ref, "token_pos": 17, "gain": gain, "separability": sep}


#: 2:2 read as one segment; the lattice heard a stop after «ٱلۡكِتَٰبُ» (2:2:2)
#: and one at the verse end into 2:3.
WORDS = [
    {"location": "2:2:1", "start": 0.0, "end": 0.5},
    {"location": "2:2:2", "start": 0.6, "end": 1.2},
    {"location": "2:2:3", "start": 1.8, "end": 2.1},
    {"location": "2:2:4", "start": 2.2, "end": 2.5},
    {"location": "2:2:5", "start": 2.6, "end": 3.0},
    {"location": "2:3:1", "start": 3.4, "end": 3.9},
]
SPECIAL = {"time_from": 0.0, "time_to": 1.0, "ref_from": "", "ref_to": "", "kind": "special"}


def _doc(*rows):
    return {"segments": [SPECIAL, *rows]}


def _build(doc):
    return pause_sidecar.build("rec", {2: doc}, {2: "u"}, "hafs")


def test_mid_verse_pause_becomes_a_midpoint_cursor_with_pieces():
    row = _row(10.0, 15.0, "2:2:1", "2:3:1", pauses=[_pause("2:2:2"), _pause("2:2:5")], words=WORDS)
    doc = _build(_doc(_row(1.0, 9.0, "2:1:1", "2:1:1"), row))

    uid = derive_uid(2, 1, 10000)
    item = doc["by_uid"][uid]
    assert item["cursors"] == [11500]
    assert item["refs"] == ["2:2:1-2:2:2", "2:2:3-2:3:1"]
    assert item["score"] == 1400
    cut = item["cuts"][0]
    assert cut["axes"] == ["lattice"]
    assert cut["gap_ms"] == 600
    assert cut["evidence"]["lattice"] == {
        "gain": 1.4,
        "separability": 2.0,
        "after_ref": "2:2:2",
        "next_ref": "2:2:3",
    }
    assert cut["word"]
    assert doc["_meta"]["kind"] == "missed_waqf"
    assert doc["_meta"]["verse_end"] == 1
    assert doc["_meta"]["segments"] == 1


def test_a_verse_end_pause_alone_yields_no_item():
    row = _row(10.0, 15.0, "2:2:1", "2:3:1", pauses=[_pause("2:2:5")], words=WORDS)
    assert _build(_doc(row))["by_uid"] == {}


def test_an_untimed_pause_is_skipped_and_counted():
    row = _row(10.0, 15.0, "2:2:1", "2:2:5", pauses=[_pause("2:2:2")])
    doc = _build(_doc(row))
    assert doc["by_uid"] == {}
    assert doc["_meta"]["untimed"] == 1


def test_adapt_carries_pauses_without_token_pos():
    row = _row(10.0, 15.0, "2:2:1", "2:2:5", pauses=[_pause("2:2:2")])
    candidate, _events, _basmala = adapt.adapt_chapter(2, _doc(row), source_url="u", riwayah="hafs")
    assert candidate["entries"][0]["segments"][0]["pauses"] == [
        {"after_ref": "2:2:2", "gain": 1.4, "separability": 2.0}
    ]
