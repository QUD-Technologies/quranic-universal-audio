"""``qua_shared.quran_text`` rebuilds the retired bundled Hafs documents from the pack.

Exact equality with the retired files is proven by
``scripts/diagnostics/quran_data_integrity.py``; these pin the shape and the legacy quirks.
"""

from __future__ import annotations

import json

import pytest

from qua_shared import quran_text

HAFS_WORDS = 77_433
HAFS_AYAHS = 6_236


def test_surah_info_counts_and_names():
    info = quran_text.surah_info()

    assert list(info) == [str(n) for n in range(1, 115)]
    assert sum(s["num_verses"] for s in info.values()) == HAFS_AYAHS
    assert sum(v["num_words"] for s in info.values() for v in s["verses"]) == HAFS_WORDS
    assert info["1"]["name_en"] == "Al-Faatiha"
    assert info["2"]["verses"][254] == {"verse": 255, "num_words": 50}


def test_surah_info_bytes_are_the_retired_file_format():
    body = quran_text.surah_info_bytes()

    assert body.startswith(b'{\n  "1": {\n    "num_verses": 7,')
    assert not body.endswith(b"\n")
    assert json.loads(body) == quran_text.surah_info()


def test_legacy_name_spelling():
    """Shadda before the short vowel and alif + madda decomposed, as the bundled names were."""
    assert quran_text._legacy_name("زُّ") == "زُّ"
    assert quran_text._legacy_name("آل") == "آل"


def test_digital_khatt_script_words_markers_and_ids():
    script = quran_text.digital_khatt_script()
    markers = [e for e in script.values() if e["text"].startswith(quran_text.AYAH_END)]

    assert len(script) == HAFS_WORDS + HAFS_AYAHS
    assert len(markers) == HAFS_AYAHS
    assert script["1:1:1"] == {
        "id": 1,
        "surah": "1",
        "ayah": "1",
        "word": "1",
        "location": "1:1:1",
        "text": script["1:1:1"]["text"],
    }
    assert script["2:255:51"]["text"] == "۝٢٥٥"
    # QUL's DK export shares one id across 37:130's two written words.
    assert script["37:130:3"]["id"] == script["37:130:4"]["id"] == 61958
    assert script["37:130:5"]["id"] == 61959
    assert max(e["id"] for e in script.values()) == HAFS_WORDS + HAFS_AYAHS - 1


def test_missing_pack_raises(monkeypatch, tmp_path):
    monkeypatch.setenv(quran_text.PACK_ENV, str(tmp_path / "absent.qref"))
    quran_text.clear_cache()
    try:
        assert not quran_text.available()
        with pytest.raises(quran_text.QuranDataUnavailable, match="install_quran_data"):
            quran_text.surah_info()
    finally:
        monkeypatch.delenv(quran_text.PACK_ENV)
        quran_text.clear_cache()


#: Every Quranic waqf mark, U+06D6..U+06DB.
WAQF_MARKS = {chr(cp) for cp in range(0x06D6, 0x06DC)}


def _without_waqf(text: str) -> str:
    return "".join(ch for ch in text if ch not in WAQF_MARKS)


def test_layout_stops_names_the_1405_edition_only():
    assert quran_text.layout_stops() == {"madani-1405": "madani-1405"}


def test_stop_edition_script_differs_only_in_waqf_marks():
    """1405's script: the same keys, ids and markers; its words differ only in stop signs."""
    default = quran_text.digital_khatt_script()
    s1405 = quran_text.digital_khatt_script("madani-1405")

    assert list(s1405) == list(default)
    assert all({**s1405[k], "text": ""} == {**default[k], "text": ""} for k in default)
    changed = [k for k in default if s1405[k]["text"] != default[k]["text"]]
    assert changed
    assert all(
        _without_waqf(s1405[k]["text"]) == _without_waqf(default[k]["text"]) for k in changed
    )
    assert json.loads(quran_text.digital_khatt_script_bytes("madani-1405")) == s1405
