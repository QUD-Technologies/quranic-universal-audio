from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "frontend/public/generated/shaped-glyphs-v13"
_DK = Path(__file__).resolve().parents[3] / "data/digital_khatt_v2_script.json"
_STOP_MARKS = {chr(cp) for cp in (0x06D6, 0x06D7, 0x06D8, 0x06DA, 0x06DB)}
_INERT_GLYPHS = {"rubelhizb", "placeofsajdah"}
_SILENT_COMPANION = "silent_companion"


@lru_cache(maxsize=1)
def _words() -> dict[str, dict]:
    out: dict[str, dict] = {}
    fixtures = sorted(
        (path for path in _FIXTURE_DIR.glob("*.json") if path.stem.isdigit()),
        key=lambda path: int(path.stem),
    )
    assert [int(path.stem) for path in fixtures] == list(range(1, 115))
    for fixture in fixtures:
        chapter_words = json.loads(fixture.read_text(encoding="utf-8"))["words"]
        for text, word in chapter_words.items():
            assert out.get(text, word) == word, (fixture, text)
            out.setdefault(text, word)
    return out


@lru_cache(maxsize=1)
def _dk() -> dict[str, dict]:
    return json.loads(_DK.read_text(encoding="utf-8"))


def _word(location: str) -> dict:
    return _words()[_dk()[location]["text"]]


@lru_cache(maxsize=1)
def _paths() -> dict[str, str]:
    fixture = json.loads((_FIXTURE_DIR / "paths.json").read_text(encoding="utf-8"))
    assert fixture["upem"] == 1000
    return fixture["paths"]


def test_shaped_fixture_covers_every_rendered_quran_word():
    expected = {row["text"] for row in _dk().values() if not row["text"].startswith("۝")}
    actual = set(_words())
    assert actual == expected, (
        f"missing={list(expected - actual)[:10]} unexpected={list(actual - expected)[:10]}"
    )


def test_every_shaped_glyph_has_exactly_one_word_local_paint_owner():
    for text, word in _words().items():
        placements = word["placements"]
        assert len(placements) == len({tuple(row[:3]) for row in placements}), text
        for glyph, _, _, owner, role in placements:
            assert glyph in _paths(), (text, glyph)
            assert (
                role in {"waqf", _SILENT_COMPANION}
                or glyph in _INERT_GLYPHS
                or isinstance(owner, int)
            ), (
                text,
                glyph,
            )
            if owner is not None:
                assert 0 <= owner < word["tokenCount"], (text, glyph, owner)


def test_shared_base_text_only_removes_independently_painted_stop_marks():
    for text, word in _words().items():
        expected = "".join(char for char in text if char not in _STOP_MARKS)
        assert word["baseText"] == expected, text


def test_independent_marks_and_combined_letters_keep_their_shaped_ink_contract():
    seated_hamza = _word("2:3:2")
    hamza_owner = next(row[3] for row in seated_hamza["placements"] if row[0] == "hamzaabove")
    assert {row[0] for row in seated_hamza["placements"] if row[3] == hamza_owner} >= {
        "hamzaabove",
        "waw.fina",
    }

    salah = _word("2:3:5")
    dagger_owner = next(row[3] for row in salah["placements"] if row[0] == "smallalef.replacement")
    carrier_owner = next(row[3] for row in salah["placements"] if row[0] == "waw.fina")
    assert dagger_owner is not None
    assert carrier_owner is not None
    assert dagger_owner != carrier_owner

    assert (
        next(row[3] for row in _word("21:88:7")["placements"] if row[0] == "smallhighnoon")
        is not None
    )
    assert (
        next(row[3] for row in _word("2:61:52")["placements"] if row[0] == "smallhighyeh")
        is not None
    )

    # Regression: these ordinary early-Quran words were outside the old
    # hand-picked fixture. They fell back to native combining-mark runs, so the
    # dagger shifted/disappeared when the word entered its active state even
    # though the v13 token itself was correctly timed.
    for location in ("2:5:1", "2:9:1"):
        dagger_owner = next(
            row[3] for row in _word(location)["placements"] if row[0].startswith("smallalef")
        )
        assert dagger_owner is not None

    mini_seen = _word("2:245:14")
    seen_owner = next(row[3] for row in mini_seen["placements"] if row[0] == "smallhighseen")
    assert (
        next(row[3] for row in mini_seen["placements"] if row[0].startswith("sad.medi"))
        != seen_owner
    )

    low_seen = _word("52:37:7")
    seen_owner = next(row[3] for row in low_seen["placements"] if row[0] == "smalllowseen")
    assert (
        next(row[3] for row in low_seen["placements"] if row[0].startswith("sad.medi"))
        != seen_owner
    )


def test_every_noon_iqlab_mark_can_paint_without_its_silent_noon_host():
    occurrences = 0
    for location, row in _dk().items():
        if "نۢ" not in row["text"]:
            continue
        word = _word(location)
        for meem in (item for item in word["placements"] if item[0] == "meemiqlab"):
            occurrences += 1
            owner = meem[3]
            owned = [item for item in word["placements"] if item[3] == owner]
            assert meem[4] is None, (location, row["text"])
            assert {item[0] for item in owned if item[4] == _SILENT_COMPANION} == {
                item[0] for item in owned if item[0] != "meemiqlab"
            }, (location, row["text"], owned)

    # Corpus guard: a source/update must not silently shrink the audited Hafs
    # DigitalKhatt noon+iqlab-mark population.
    assert occurrences == 270


#: Isti'adha then Basmala, as the generator's ``OPENER_SOURCE_WORDS`` (its font deps stay out of CI).
_OPENER_SOURCES = [
    "2:67:15",
    "16:98:5",
    "16:98:6",
    "16:98:7",
    "16:98:8",
    "1:1:1",
    "1:1:2",
    "1:1:3",
    "1:1:4",
]


def test_opener_words_are_their_source_verse_words():
    openers = json.loads((_FIXTURE_DIR / "openers.json").read_text(encoding="utf-8"))
    assert openers["upem"] == 1000
    sources = _OPENER_SOURCES
    assert list(openers["words"]) == [_dk()[ref]["text"] for ref in sources]
    for ref in sources:
        assert openers["words"][_dk()[ref]["text"]] == _word(ref), ref
