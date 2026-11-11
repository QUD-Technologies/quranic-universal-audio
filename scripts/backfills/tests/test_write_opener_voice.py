"""``write_opener_voice`` — a binary verdict per audio source."""

from __future__ import annotations

from write_opener_voice import decide_reciter, person, stock_clips

_STOCK = [1.0] * 19


def _own(i: int) -> list[float]:
    return [5.0 + 20 * i] * 19  # every live recording differs


def _clip(dur: int, mfcc: list[float], z: float | None, kind: str = "Basmala") -> dict:
    return {"type": kind, "dur_ms": dur, "mfcc": mfcc, "z": z}


def _verdicts(out: dict) -> dict[int, str]:
    return {ch: by_start["0"]["verdict"] for ch, by_start in out.items()}


def test_a_reused_clip_gets_one_verdict_from_its_median_z():
    doc = {
        "openers": {
            str(ch): {"0": _clip(2900 + ch, _STOCK, z)} for ch, z in ((2, 1.5), (3, 1.9), (4, 2.6))
        }
    }
    assert _verdicts(decide_reciter(doc, (None, None), set())) == {2: "same", 3: "same", 4: "same"}


def test_a_reused_clip_matching_the_fatiha_basmala_takes_its_answer():
    doc = {
        "fatiha": _clip(2900, _STOCK, 0.4),
        "openers": {"2": {"0": _clip(2910, _STOCK, 0.9)}, "3": {"0": _clip(2890, _STOCK, 1.0)}},
    }
    assert _verdicts(decide_reciter(doc, (False, 0.4), set())) == {2: "different", 3: "different"}


def test_a_live_opener_leans_same_unless_clearly_different():
    doc = {
        "openers": {
            "2": {"0": _clip(4000, _own(1), 2.2)},
            "3": {"0": _clip(4300, _own(2), 3.1)},
            "4": {"0": _clip(4600, _own(3), None)},
        }
    }
    assert _verdicts(decide_reciter(doc, (None, None), set())) == {
        2: "same",
        3: "different",
        4: "same",
    }


def test_the_fatiha_answer_moves_the_cut():
    doc = {"openers": {str(ch): {"0": _clip(2900 + 200 * ch, _own(ch), 2.1)} for ch in (2, 3)}}
    assert _verdicts(decide_reciter(doc, (True, 2.0), set())) == {2: "same", 3: "same"}
    reused = {"openers": {str(ch): {"0": _clip(2900 + ch, _STOCK, 1.9)} for ch in (2, 3)}}
    assert _verdicts(decide_reciter(reused, (False, 1.8), set())) == {
        2: "different",
        3: "different",
    }


def test_a_clip_shared_with_another_reciter_is_different():
    shared = _clip(3000, _STOCK, 0.5)
    prints = {
        "ahmed_al_swailem_mp3quran": {"openers": {"2": {"0": shared}}},
        "raad_al_kurdi_yt": {"openers": {"5": {"0": dict(shared)}}},
        "saber_abdulhakam_yt": {"openers": {"2": {"0": _clip(4000, _own(1), 0.5)}}},
        "saber_abdulhakam_warsh_way2quran": {"openers": {"2": {"0": _clip(4000, _own(1), 0.5)}}},
    }
    stock = stock_clips(prints)
    assert set(stock) == {"ahmed_al_swailem_mp3quran", "raad_al_kurdi_yt"}
    doc = prints["ahmed_al_swailem_mp3quran"]
    assert _verdicts(decide_reciter(doc, (None, None), stock["ahmed_al_swailem_mp3quran"])) == {
        2: "different"
    }


def test_person_keeps_the_family_name_after_a_generic_token():
    assert person("saber_abdulhakam_warsh_way2quran") == person("saber_abdulhakam_yt")
    assert person("mohammed_al_tablawi_qdc") != person("mohammed_al_luhaidan_mp3quran")


def test_a_live_opener_leans_foreign_in_a_mostly_foreign_reciter():
    stock = {str(ch): {"0": _clip(2900 + ch, _STOCK, 3.5)} for ch in (2, 3, 4)}
    doc = {"openers": {**stock, "27": {"0": _clip(2340, _own(1), 1.81)}}}
    assert _verdicts(decide_reciter(doc, (False, 2.3), set()))[27] == "different"
    own = {str(ch): {"0": _clip(4000 + 300 * ch, _own(ch), 0.8)} for ch in (2, 3, 4)}
    doc = {"openers": {**own, "27": {"0": _clip(2340, _own(9), 1.81)}}}
    assert _verdicts(decide_reciter(doc, (False, 2.3), set()))[27] == "same"
