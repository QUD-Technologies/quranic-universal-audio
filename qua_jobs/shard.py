#!/usr/bin/env python3
"""Shard a release tier file into per-surah files.

Stdlib-only — shipped as a release asset so consumers can use it without
installing dependencies. Reads any of ``verse_timestamps.json.gz``,
``word_timestamps.json.gz``, ``letter_timestamps.json.gz`` (gzipped or plain
``.json``); writes one ``<surah>.json`` per chapter covered, byte-stable across
re-runs.

Usage:
    python shard.py <tier_file> [--out-dir <dir>]

The output directory defaults to ``per_surah/`` next to the input. Each shard
preserves ``_meta`` and the source occurrence order. Legacy keyed tiers remain
accepted so the helper can also split pre-v3 releases.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    """Load JSON or gzipped JSON from ``path``."""
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _surah_of(verse_key: str) -> int | None:
    """Extract the surah number from a ``"surah:ayah"`` key, or None if not a verse key."""
    if ":" not in verse_key:
        return None
    try:
        return int(verse_key.split(":", 1)[0])
    except ValueError:
        return None


def _ayah_of(verse_key: str) -> int:
    """Extract the ayah number for sort. Falls back to 0 on parse failure."""
    parts = verse_key.split(":", 2)
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return 0


def split(doc: dict) -> dict[int, dict]:
    """Group timestamp rows by chapter. Returns ``{surah: shard_dict}``.

    Occurrence rows preserve timeline order. Legacy keyed rows are sorted by
    ayah for byte stability.
    """
    src_meta = doc.get("_meta", {}) or {}
    rows = doc.get("rows")
    if isinstance(rows, list):
        by_chapter_rows: dict[int, list[list]] = {}
        for row in rows:
            if not isinstance(row, list) or not row:
                continue
            surah = _surah_of(row[0]) if isinstance(row[0], str) else None
            if surah is not None:
                by_chapter_rows.setdefault(surah, []).append(row)
        out: dict[int, dict] = {}
        for surah, chapter_rows in by_chapter_rows.items():
            meta = dict(src_meta)
            meta["chapter"] = surah
            meta["verse_count"] = len({row[0] for row in chapter_rows})
            meta["occurrence_count"] = len(chapter_rows)
            out[surah] = {"_meta": meta, "rows": chapter_rows}
        return out

    by_chapter: dict[int, dict[str, object]] = {}
    for key, val in doc.items():
        if key.startswith("_"):
            continue
        surah = _surah_of(key)
        if surah is None:
            continue
        by_chapter.setdefault(surah, {})[key] = val

    out: dict[int, dict] = {}
    for surah, verses in by_chapter.items():
        meta = dict(src_meta)
        meta["chapter"] = surah
        shard: dict[str, object] = {"_meta": meta}
        for k in sorted(verses, key=_ayah_of):
            shard[k] = verses[k]
        out[surah] = shard
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shard a tier file into per-surah JSON files")
    parser.add_argument("input", type=Path, help="Tier file (.json.gz or .json)")
    parser.add_argument(
        "--out-dir", type=Path, help="Output directory (default: <input parent>/per_surah)"
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: {args.input} does not exist", file=sys.stderr)
        return 2

    out_dir = args.out_dir or args.input.parent / "per_surah"
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = _load(args.input)
    shards = split(doc)
    if not shards:
        print(f"error: no verse entries found in {args.input}", file=sys.stderr)
        return 1

    for surah in sorted(shards):
        target = out_dir / f"{surah}.json"
        payload = json.dumps(
            shards[surah], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        target.write_text(payload, encoding="utf-8")
    print(f"wrote {len(shards)} files to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
