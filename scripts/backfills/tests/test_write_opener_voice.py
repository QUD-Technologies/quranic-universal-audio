"""``write_opener_voice`` — one stock clip reused across chapters gets one verdict."""

from __future__ import annotations

from write_opener_voice import consensus

_STOCK = [1.0] * 19
_OWN = [5.0] * 19


def _clip(dur: int, mfcc: list[float], kind: str = "Basmala") -> dict:
    return {"type": kind, "dur_ms": dur, "mfcc": mfcc}


def test_a_reused_clip_shares_the_median_verdict_and_own_clips_keep_theirs():
    verdicts = {
        "2": {"0": {"z": 1.5}},  # stock clip, scored "same" against this chapter
        "3": {"0": {"z": 2.0}},  # stock clip, unsure here
        "4": {"0": {"z": 2.6}},  # stock clip, "different" here
        "5": {"0": {"z": 0.6}},  # the reciter's own Basmala
    }
    prints = {
        "openers": {
            "2": {"0": _clip(2900, _STOCK)},
            "3": {"0": _clip(2920, _STOCK)},
            "4": {"0": _clip(2890, _STOCK)},
            "5": {"0": _clip(4300, _OWN)},
        }
    }
    out = consensus(verdicts, prints)
    assert {ch: out[ch]["0"]["verdict"] for ch in (2, 3, 4)} == {
        2: "needs_confirmation",
        3: "needs_confirmation",
        4: "needs_confirmation",
    }
    assert out[5]["0"] == {"verdict": "same", "z": 0.6}


def test_same_length_but_different_audio_is_not_grouped():
    verdicts = {"2": {"0": {"z": 0.5}}, "3": {"0": {"z": 3.0}}}
    prints = {"openers": {"2": {"0": _clip(2900, _OWN)}, "3": {"0": _clip(2900, _STOCK)}}}
    out = consensus(verdicts, prints)
    assert (out[2]["0"]["verdict"], out[3]["0"]["verdict"]) == ("same", "different")
