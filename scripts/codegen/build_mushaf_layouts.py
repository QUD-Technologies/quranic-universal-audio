"""Build the compact Madani mushaf layouts for the Timestamps Mushaf view.

Reads the QUL layout SQLite files (1405H / 1421H / 1441H) plus the juz/hizb
metadata from a ``quranic-universal-mushaf`` checkout and writes one small
JSON per print year into the frontend, where the view lazy-imports it:

    inspector/frontend/src/tabs/timestamps/mushaf/data/layout-<year>.json

Shape (all ints, positional to keep the file small)::

    {"year": "1421", "pages": [[line, ...], ...604], "juz": [..604], "hizb": [..604]}

    line = [0, centered, firstWordId, lastWordId]   # ayah line
         | [1, 1, surah, 0]                          # surah header
         | [2, 1, 0, 0]                              # basmallah

Word ids index the Digital Khatt v2 script (``data/digital_khatt_v2_script.json``,
verse-end markers included), the same ids the frontend's ``loadDk()`` carries,
so no text ships here. Deterministic: re-running on the same inputs is a no-op.

Usage::

    python scripts/codegen/build_mushaf_layouts.py --src ../quranic-universal-mushaf
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DK_SCRIPT = _REPO / "data" / "digital_khatt_v2_script.json"
_OUT_DIR = _REPO / "inspector" / "frontend" / "src" / "tabs" / "timestamps" / "mushaf" / "data"

YEARS = ("1405", "1421", "1441")
PAGE_COUNT = 604
LINE_AYAH, LINE_SURAH, LINE_BASMALLAH = 0, 1, 2


def _verse_of_word(dk_path: Path) -> dict[int, tuple[int, int]]:
    """Global DK word id -> (surah, ayah)."""
    raw = json.loads(dk_path.read_text(encoding="utf-8"))
    return {int(w["id"]): (int(w["surah"]), int(w["ayah"])) for w in raw.values()}


def _section_starts(meta_path: Path, number_key: str) -> list[tuple[tuple[int, int], int]]:
    """[(first verse (surah, ayah), section number)] ascending."""
    rows = json.loads(meta_path.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        s, a = (int(x) for x in r["first_verse_key"].split(":"))
        out.append(((s, a), int(r[number_key])))
    return sorted(out)


def _section_at(starts: list[tuple[tuple[int, int], int]], verse: tuple[int, int]) -> int:
    """Number of the last section starting at or before ``verse``."""
    num = starts[0][1]
    for first, n in starts:
        if first <= verse:
            num = n
        else:
            break
    return num


def _encode_line(row: sqlite3.Row) -> list[int]:
    kind = row["line_type"]
    if kind == "ayah":
        return [
            LINE_AYAH,
            int(row["is_centered"] or 0),
            int(row["first_word_id"]),
            int(row["last_word_id"]),
        ]
    if kind == "surah_name":
        return [LINE_SURAH, 1, int(row["surah_number"]), 0]
    if kind == "basmallah":
        return [LINE_BASMALLAH, 1, 0, 0]
    raise ValueError(f"unknown line_type {kind!r} on page {row['page_number']}")


def build_year(db_path: Path, verse_of: dict[int, tuple[int, int]], juz, hizb) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM pages ORDER BY page_number, line_number").fetchall()
    con.close()
    pages: list[list[list[int]]] = [[] for _ in range(PAGE_COUNT)]
    for row in rows:
        pages[int(row["page_number"]) - 1].append(_encode_line(row))
    juz_of, hizb_of = [], []
    for num, lines in enumerate(pages, start=1):
        first_word = next((ln[2] for ln in lines if ln[0] == LINE_AYAH), None)
        if first_word is None:
            raise ValueError(f"{db_path.name}: page {num} has no ayah line")
        verse = verse_of[first_word]
        juz_of.append(_section_at(juz, verse))
        hizb_of.append(_section_at(hizb, verse))
    return {"pages": pages, "juz": juz_of, "hizb": hizb_of}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the compact Madani mushaf layouts.")
    ap.add_argument("--src", type=Path, required=True, help="quranic-universal-mushaf checkout")
    args = ap.parse_args()
    data = args.src / "data"
    verse_of = _verse_of_word(_DK_SCRIPT)
    juz = _section_starts(data / "quran-metadata" / "juz-metadata.json", "juz_number")
    hizb = _section_starts(data / "quran-metadata" / "hizb-metadata.json", "hizb_number")
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        body = {
            "year": year,
            **build_year(data / "quran-layouts" / f"madani-{year}h.db", verse_of, juz, hizb),
        }
        out = _OUT_DIR / f"layout-{year}.json"
        out.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
        print(f"{out.relative_to(_REPO)}  {out.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
