"""Hafs reference text and surah metadata, read from the Talqeen Quran data pack.

The single source of the Hafs words (Digital Khatt v2, the SDK's default stops), ayah-end
markers and surah metadata. It replaces the bundled ``surah_info.json``,
``digital_khatt_v2_script.json`` and ``qpc_hafs.json``: every reader here rebuilds one of
those documents, field for field, from ``talqeen_quran_data``. Nothing falls back to a
bundled copy; an absent SDK or pack raises :class:`QuranDataUnavailable`.

* :func:`surah_info` — the ``surah_info.json`` document (byte-identical serialisation).
* :func:`digital_khatt_script` — the ``digital_khatt_v2_script.json`` document: every word
  plus one ayah-end marker entry per ayah (``s:a:<words+1>``), in reading order.
* :func:`hafs_word_script` — the ``qpc_hafs.json`` shape (words only, its ids) carrying the
  Digital Khatt text. QPC Hafs text is retired; the key stays for the frontend.
* :func:`layout_stops` — the layouts printed with their own stop signs (``madani-1405``) and
  their stop edition. ``digital_khatt_script(stops=...)`` is the same script, ids and markers
  with that edition's stop signs, for a view that displays such a layout.

The pack is ``data/quran-core.qref`` next to this package (fetched by
``scripts/devenv/install_quran_data.py`` or the image build, never committed), or the path
in ``QUA_QURAN_PACK``. Built documents are cached for the process: the data is immutable for
a given pin.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

from qua_shared.quran_data_pin import FONT_SHA256, FONTS_DIR_NAME, PACK_NAME

PACK_ENV = "QUA_QURAN_PACK"
#: U+06DD ARABIC END OF AYAH, the first character of every ayah-end marker.
AYAH_END = "۝"
DEFAULT_PACK_PATH = Path(__file__).resolve().parents[1] / "data" / PACK_NAME

#: QUL's Digital Khatt export gives 37:130's two written words (إِلْ يَاسِينَ) one word id,
#: so this word repeats its predecessor's id there; QPC Hafs gave it the next id. Every other
#: id is shared and counts ayah-end markers. Kept so the ids stay what they were.
_DK_SHARED_ID_WORD = (37, 130, 4)

#: The bundled surah names spelt shadda before the short vowel and alif + madda decomposed;
#: the pack stores them NFC (canonically equivalent). Restored so the bytes stay the same.
_VOWEL_THEN_SHADDA = re.compile("([ً-ِ]+)(ّ)")
_ALIF_MADDA = "آ"
_ALIF_PLUS_MADDA = "آ"

T = TypeVar("T")


class QuranDataUnavailable(RuntimeError):
    """The talqeen-quran-data SDK or its pack is missing."""


def pack_path() -> Path:
    override = os.environ.get(PACK_ENV, "").strip()
    return Path(override) if override else DEFAULT_PACK_PATH


def font_path(name: str) -> Path:
    """A pinned font fetched next to the pack (``<pack dir>/fonts/<name>``); ``KeyError`` if unpinned."""
    if name not in FONT_SHA256:
        raise KeyError(name)
    return pack_path().parent / FONTS_DIR_NAME / name


_lock = threading.RLock()
_cache: dict[str, Any] = {}


def _once(key: str, build: Callable[[], T]) -> T:
    """Build a document once per process (the pack is immutable for a pin)."""
    if key in _cache:
        return _cache[key]
    with _lock:
        if key not in _cache:
            _cache[key] = build()
        return _cache[key]


def clear_cache() -> None:
    """Drop every built document and the open pack (tests, a re-pointed pack)."""
    with _lock:
        pack = _cache.pop("pack", None)
        _cache.clear()
        if pack is not None:
            pack.close()


def _open_pack():
    path = pack_path()
    try:
        from talqeen_quran_data import QuranPack
    except ImportError as exc:
        raise QuranDataUnavailable(
            "talqeen-quran-data is not installed; run scripts/devenv/install_quran_data.py"
        ) from exc
    if not path.is_file():
        raise QuranDataUnavailable(
            f"Quran data pack missing at {path}; run scripts/devenv/install_quran_data.py"
        )
    return QuranPack(path)


def pack():
    """The open ``QuranPack``."""
    return _once("pack", _open_pack)


def available() -> bool:
    try:
        pack()
    except QuranDataUnavailable:
        return False
    return True


def _legacy_name(text: str) -> str:
    return _VOWEL_THEN_SHADDA.sub(r"\2\1", text.replace(_ALIF_MADDA, _ALIF_PLUS_MADDA))


def _build_surah_info() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for surah in pack().surahs():
        names = {name["language"]: name["text"] for name in surah["names"]}
        counts = surah["ayahWordCounts"]
        out[str(surah["number"])] = {
            "num_verses": len(counts),
            "verses": [{"verse": i, "num_words": n} for i, n in enumerate(counts, 1)],
            "name_en": names["en"],
            "name_ar": _legacy_name(names["ar"]),
        }
    return out


def surah_info() -> dict[str, dict]:
    """``{surah: {num_verses, verses: [{verse, num_words}], name_en, name_ar}}``."""
    return _once("surah_info", _build_surah_info)


def surah_info_bytes() -> bytes:
    """The ``surah_info.json`` file bytes (two-space indent, no trailing newline)."""
    return _once(
        "surah_info_bytes",
        lambda: json.dumps(surah_info(), ensure_ascii=False, indent=2).encode(),
    )


def _reading_order(stops: str | None = None) -> Iterator[tuple[int, int, int, str]]:
    """``(surah, ayah, word, text)`` for every word and ayah-end marker, in reading order.

    ``stops`` names a stop edition (a layout's ``stops``); ``None`` is the pack's default."""
    p = pack()
    for surah in p.surahs():
        number = surah["number"]
        for ayah, count in enumerate(surah["ayahWordCounts"], 1):
            for word in range(1, count + 1):
                yield number, ayah, word, p.text(f"{number}:{ayah}:{word}", stops)
            yield number, ayah, count + 1, p.ayah_end(number, ayah)


def _entry(word_id: int, surah: int, ayah: int, word: int, text: str) -> dict[str, Any]:
    return {
        "id": word_id,
        "surah": str(surah),
        "ayah": str(ayah),
        "word": str(word),
        "location": f"{surah}:{ayah}:{word}",
        "text": text,
    }


def _build_scripts(stops: str | None = None) -> tuple[dict[str, dict], dict[str, dict]]:
    """The Digital Khatt script (words + markers) and the QPC-shaped word script."""
    dk: dict[str, dict] = {}
    words: dict[str, dict] = {}
    dk_id = 0
    for surah, ayah, word, text in _reading_order(stops):
        shared = (surah, ayah, word) == _DK_SHARED_ID_WORD
        dk_id += 0 if shared else 1
        entry = _entry(dk_id, surah, ayah, word, text)
        dk[entry["location"]] = entry
        if not text.startswith(AYAH_END):
            words[entry["location"]] = _entry(dk_id + shared, surah, ayah, word, text)
    return dk, words


def _scripts(stops: str | None) -> tuple[dict[str, dict], dict[str, dict]]:
    key = "scripts" if stops is None else f"scripts:{stops}"
    return _once(key, lambda: _build_scripts(stops))


def layout_stops() -> dict[str, str]:
    """``{layout id: stop edition}`` for the layouts printed with their own stop signs.

    Every other layout shows the pack's default (modern) stops."""
    return _once(
        "layout_stops",
        lambda: {layout["id"]: layout["stops"] for layout in pack().layouts() if layout["stops"]},
    )


def digital_khatt_script(stops: str | None = None) -> dict[str, dict]:
    """``{"s:a:w": {id, surah, ayah, word, location, text}}`` incl. one marker per ayah.

    ``stops`` (a value of :func:`layout_stops`) swaps in that edition's stop signs; keys,
    ids, markers and every other character are the same."""
    return _scripts(stops)[0]


def hafs_word_script() -> dict[str, dict]:
    """The words only, in the ``qpc_hafs.json`` shape and ids, with Digital Khatt text."""
    return _scripts(None)[1]


def digital_khatt_script_bytes(stops: str | None = None) -> bytes:
    """Serialised like the retired file: compact, unescaped, no trailing newline."""
    return _once(
        "dk_bytes" if stops is None else f"dk_bytes:{stops}",
        lambda: json.dumps(digital_khatt_script(stops), ensure_ascii=False).encode(),
    )


def hafs_word_script_bytes() -> bytes:
    """Serialised like the retired ``qpc_hafs.json``: two-space indent, trailing newline."""
    return _once(
        "hafs_bytes",
        lambda: (json.dumps(hafs_word_script(), ensure_ascii=False, indent=2) + "\n").encode(),
    )


__all__ = [
    "AYAH_END",
    "PACK_ENV",
    "QuranDataUnavailable",
    "available",
    "clear_cache",
    "digital_khatt_script",
    "digital_khatt_script_bytes",
    "font_path",
    "hafs_word_script",
    "hafs_word_script_bytes",
    "layout_stops",
    "pack",
    "pack_path",
    "surah_info",
    "surah_info_bytes",
]
