"""``missed_waqf_v2`` / ``verse_ends_v1`` — Low Confidence Waqf items and verse-end verdicts
from the aligner's lattice pauses and the chapter's loudness levels."""

from __future__ import annotations

import numpy as np
import pytest

from domain.identity import derive_uid
from services.admin.align_pipeline import adapt, pause_sidecar
from services.admin.align_pipeline.join_silence import ChapterLevels, Silence


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


def _build(doc, riwayah="hafs"):
    return pause_sidecar.build("rec", {2: doc}, {2: "u"}, riwayah)[0]


SPEECH_DB, FLOOR_DB = -20, -70


@pytest.fixture
def quiet_at(monkeypatch):
    """Chapter levels: speech throughout, floor-level silence over the given ``(from_ms, to_ms)`` spans."""

    def make(*spans):
        lv = np.full(1000, SPEECH_DB, dtype=np.int16)  # 20 s
        lv[:50] = FLOOR_DB  # the chapter pads set the noise floor
        for a, b in spans:
            lv[a // 20 : b // 20] = FLOOR_DB
        monkeypatch.setattr(ChapterLevels, "load", lambda slug, ch: ChapterLevels(lv))

    return make


def test_mid_verse_pause_becomes_a_midpoint_cursor_with_pieces():
    row = _row(10.0, 15.0, "2:2:1", "2:3:1", pauses=[_pause("2:2:2"), _pause("2:2:5")], words=WORDS)
    doc = _build(_doc(_row(1.0, 9.0, "2:1:1", "2:1:1"), row))

    uid = derive_uid(2, 1, 10000)
    item = doc["by_uid"][uid]
    # Without levels nothing is measured: the pause and the verse end are both asked.
    assert item["cursors"] == [11500, 13200]
    assert item["refs"] == ["2:2:1-2:2:2", "2:2:3-2:2:5", "2:3:1-2:3:1"]
    assert item["score"] == 1400
    cut = item["cuts"][0]
    assert cut["axes"] == ["lattice"]
    assert cut["gap_ms"] == 0
    assert cut["evidence"]["lattice"] == {
        "paused": True,
        "gain": 1.4,
        "separability": 2.0,
        "after_ref": "2:2:2",
        "next_ref": "2:2:3",
    }
    assert cut["word"]
    assert item["cuts"][1]["verse_end"] is True
    assert doc["_meta"]["kind"] == "missed_waqf"
    assert doc["_meta"]["verse_end"] == 1
    assert doc["_meta"]["segments"] == 1


def test_a_heard_and_silent_verse_end_is_a_waqf_verdict(quiet_at):
    quiet_at((13000, 13400))
    row = _row(10.0, 15.0, "2:2:1", "2:3:1", pauses=[_pause("2:2:5")], words=WORDS)
    missed, verse_ends = pause_sidecar.build("rec", {2: _doc(row)}, {2: "u"}, "hafs")
    assert missed["by_uid"] == {}
    entry = verse_ends["by_uid"][derive_uid(2, 0, 10000)]
    assert entry["start_ms"] == 10000 and entry["end_ms"] == 15000
    assert entry["joins"] == [
        {"after_ref": "2:2:5", "next_ref": "2:3:1", "verdict": "waqf", "cursor_ms": 13200}
    ]


def test_an_unheard_voiced_verse_end_is_a_wasl_verdict(quiet_at):
    quiet_at()
    row = _row(10.0, 15.0, "2:2:1", "2:3:1", words=WORDS)
    missed, verse_ends = pause_sidecar.build("rec", {2: _doc(row)}, {2: "u"}, "hafs")
    assert missed["by_uid"] == {}
    assert verse_ends["by_uid"][derive_uid(2, 0, 10000)]["joins"][0]["verdict"] == "wasl"


def test_disagreeing_readings_ask_the_verse_end(quiet_at):
    quiet_at((13000, 13400))
    row = _row(10.0, 15.0, "2:2:1", "2:3:1", words=WORDS)
    missed, verse_ends = pause_sidecar.build("rec", {2: _doc(row)}, {2: "u"}, "hafs")
    assert verse_ends["by_uid"] == {}
    (cut,) = missed["by_uid"][derive_uid(2, 0, 10000)]["cuts"]
    assert cut["verse_end"] is True and cut["gap_ms"] == 400


def test_non_hafs_and_low_confidence_verse_ends_are_always_asked(quiet_at):
    quiet_at()
    row = _row(10.0, 15.0, "2:2:1", "2:3:1", words=WORDS)
    assert _build(_doc(row), "warsh")["by_uid"]
    assert _build(_doc({**row, "confidence": 0.5}))["by_uid"]


def test_a_voiced_unmarked_pause_is_dropped_a_silent_one_asked(quiet_at):
    quiet_at()
    row = _row(10.0, 15.0, "2:2:1", "2:2:5", pauses=[_pause("2:2:2")], words=WORDS[:5])
    assert _build(_doc(row))["by_uid"] == {}
    quiet_at((11300, 11700))
    (cut,) = _build(_doc(row))["by_uid"][derive_uid(2, 0, 10000)]["cuts"]
    assert cut["cursor_ms"] == 11500 and cut["gap_ms"] == 400


def test_keep_stop_rule():
    quiet = Silence(floor_ms=0, silence_ms=60, dip_db=12, at_ms=0)
    assert pause_sidecar.keep_stop(None, marked=False, hafs=True)
    assert pause_sidecar.keep_stop(quiet, marked=True, hafs=True)
    assert not pause_sidecar.keep_stop(quiet, marked=False, hafs=True)
    assert pause_sidecar.keep_stop(Silence(160, 60, 12, 0), marked=False, hafs=True)
    assert not pause_sidecar.keep_stop(Silence(160, 30, 12, 0), marked=True, hafs=True)
    assert not pause_sidecar.keep_stop(quiet, marked=True, hafs=False)
    assert pause_sidecar.keep_stop(Silence(120, 60, 12, 0), marked=False, hafs=False)


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


def test_builder_skips_live_reviewed_joins_but_not_a_different_geometry():
    row = _row(10.0, 15.0, "2:2:1", "2:2:5", pauses=[_pause("2:2:2")], words=WORDS[:5])
    live = [
        {
            "ref": "2",
            "segments": [
                {
                    "time_start": 10000,
                    "time_end": 15000,
                    "matched_ref": "2:2:1-2:2:5",
                    "join_verdicts": [{"after_ref": "2:2:2", "at_ms": 11500, "verdict": "wasl"}],
                }
            ],
        }
    ]
    result = pause_sidecar.build("rec", {2: _doc(row)}, {2: "u"}, "hafs", live)[0]
    assert result["by_uid"] == {}
    assert result["_meta"]["answered"] == 1
    live[0]["segments"][0]["time_start"] = 9999
    assert pause_sidecar.build("rec", {2: _doc(row)}, {2: "u"}, "hafs", live)[0]["by_uid"]
