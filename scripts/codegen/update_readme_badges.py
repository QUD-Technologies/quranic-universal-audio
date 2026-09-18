#!/usr/bin/env python3
"""Refresh README stats badges and data/stats.json from the production Inspector bucket.

``data/stats.json`` carries the same numbers as raw values for other consumers
(the qud.dev project page reads it at build time).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

PROD_BUCKET_ID = "hetchyy/quranic-inspector-bucket"
DB_BUCKET_PATH = "db/inspector.db"
DEFAULT_README = Path(__file__).resolve().parents[2] / "README.md"
DEFAULT_STATS = Path(__file__).resolve().parents[2] / "data" / "stats.json"

START_MARKER = "  <!-- stats-badges:start -->"
END_MARKER = "  <!-- stats-badges:end -->"


@dataclass(frozen=True)
class BadgeStats:
    reciters: int
    mushafs: int
    riwayat: int
    seconds: int


def format_hours(seconds: int, *, step: int = 50) -> str:
    hours = max(0, int(seconds // 3600))
    floored = (hours // step) * step
    return f"{floored:,}h+"


def badge_url(label: str, value: str, color: str) -> str:
    return f"https://img.shields.io/badge/{quote(label, safe='')}-{quote(value, safe='')}-{color}"


def _bucket_uri(bucket_id: str, path: str) -> str:
    return f"buckets/{bucket_id}/{path}"


def read_bucket_bytes(bucket_id: str, path: str, *, token: str | None = None) -> bytes:
    if token:
        os.environ.setdefault("HF_TOKEN", token)
    from huggingface_hub import hffs  # type: ignore[import-not-found]

    result = hffs.cat_file(_bucket_uri(bucket_id, path))
    return result.encode("utf-8") if isinstance(result, str) else result


def download_db(bucket_id: str, *, token: str | None = None) -> Path:
    data = read_bucket_bytes(bucket_id, DB_BUCKET_PATH, token=token)
    fd, tmp = tempfile.mkstemp(prefix="inspector-badges-", suffix=".db")
    os.close(fd)
    path = Path(tmp)
    path.write_bytes(data)
    return path


def _duration_from_manifest(doc: dict, slug: str) -> int:
    chapters = doc.get("chapters")
    if not isinstance(chapters, dict) or not chapters:
        raise RuntimeError(f"{slug}: audio manifest has no chapters")

    total = 0.0
    missing: list[str] = []
    for chapter, entry in chapters.items():
        if not isinstance(entry, dict):
            missing.append(str(chapter))
            continue
        value = entry.get("duration_sec")
        if value is None:
            missing.append(str(chapter))
            continue
        total += float(value)

    if missing:
        sample = ", ".join(missing[:8])
        suffix = "..." if len(missing) > 8 else ""
        raise RuntimeError(
            f"{slug}: missing duration_sec for {len(missing)} chapter(s): {sample}{suffix}"
        )
    return int(total)


def _rows_to_stats(
    rows: list[sqlite3.Row],
    *,
    manifest_reader: Callable[[str], dict] | None,
    label: str,
) -> BadgeStats:
    reciters = len({row["reciter_id"] for row in rows})
    mushafs = len(rows)
    riwayat = len({row["riwayah"] for row in rows})

    seconds = 0
    missing_duration: list[str] = []
    for row in rows:
        duration = row["total_duration_sec"]
        if duration is not None:
            seconds += int(duration)
            continue
        if manifest_reader is None:
            missing_duration.append(row["slug"])
            continue
        seconds += _duration_from_manifest(manifest_reader(row["slug"]), row["slug"])

    if missing_duration:
        raise RuntimeError(
            f"missing total_duration_sec for {label} delivery(s): " + ", ".join(missing_duration)
        )

    return BadgeStats(reciters=reciters, mushafs=mushafs, riwayat=riwayat, seconds=seconds)


def collect_stats(
    db_path: Path,
    *,
    manifest_reader: Callable[[str], dict] | None = None,
) -> tuple[BadgeStats, BadgeStats]:
    """Return (catalog_stats, aligned_stats): all sourced audio vs. released+public only."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        catalog_rows = conn.execute(
            """
            SELECT d.slug, d.reciter_id, d.total_duration_sec, d.riwayah
            FROM deliveries d
            ORDER BY d.slug
            """
        ).fetchall()
        aligned_rows = conn.execute(
            """
            SELECT d.slug, d.reciter_id, d.total_duration_sec, d.riwayah
            FROM deliveries d
            JOIN delivery_states s ON s.slug = d.slug
            WHERE s.state = 'released'
              AND s.visibility = 'public'
            ORDER BY d.slug
            """
        ).fetchall()
    finally:
        conn.close()

    catalog_stats = _rows_to_stats(catalog_rows, manifest_reader=manifest_reader, label="catalog")
    aligned_stats = _rows_to_stats(aligned_rows, manifest_reader=manifest_reader, label="aligned")
    return catalog_stats, aligned_stats


LABEL_COLOR = "64748b"
STATS_COLOR = "d4842a"
RIWAYAT_COLOR = "f0ad4e"


def render_badges(catalog: BadgeStats, aligned: BadgeStats) -> str:
    catalog_hours = format_hours(catalog.seconds, step=500)
    aligned_hours = format_hours(aligned.seconds, step=50)

    def label_badge(text: str) -> str:
        return f'<img src="https://img.shields.io/badge/{quote(text, safe="")}-{LABEL_COLOR}" alt="{text}">'

    def badge(label: str, value: str, color: str) -> str:
        return f'<img src="{badge_url(label, value, color)}" alt="{label}: {value}">'

    lines = [
        START_MARKER,
        "  <br>",
        "  "
        + label_badge("\U0001f509 Catalog")
        + " "
        + badge("Reciters", f"{catalog.reciters:,}", STATS_COLOR)
        + " "
        + badge("Riwayat", f"{catalog.riwayat:,}", RIWAYAT_COLOR)
        + " "
        + badge("Hours", catalog_hours, STATS_COLOR),
        "  <br>",
        "  "
        + label_badge("\U0001f3af Aligned")
        + " "
        + badge("Mushafs", f"{aligned.mushafs:,}", STATS_COLOR)
        + " "
        + badge("Riwayat", f"{aligned.riwayat:,}", RIWAYAT_COLOR)
        + " "
        + badge("Hours", aligned_hours, STATS_COLOR),
        END_MARKER,
    ]
    return "\n".join(lines)


def render_stats_json(catalog: BadgeStats, aligned: BadgeStats) -> str:
    """Raw counts for consumers that want numbers, not badges."""
    doc = {
        "schema_version": 1,
        "catalog": {
            "reciters": catalog.reciters,
            "mushafs": catalog.mushafs,
            "riwayat": catalog.riwayat,
            "seconds": catalog.seconds,
        },
        "aligned": {
            "reciters": aligned.reciters,
            "mushafs": aligned.mushafs,
            "riwayat": aligned.riwayat,
            "seconds": aligned.seconds,
        },
    }
    return json.dumps(doc, indent=2) + "\n"


def replace_badges(readme: str, badges: str) -> str:
    if START_MARKER in readme and END_MARKER in readme:
        before, rest = readme.split(START_MARKER, 1)
        _, after = rest.split(END_MARKER, 1)
        return before + badges + after

    old_block = """  <!-- <br> -->
  <!-- <a href="data/RECITERS.md"><img src="https://img.shields.io/badge/Unsegmented-832%20reciters%20%C2%B7%2014,320h-d4842a" alt="Unsegmented"></a> -->
  <!-- <a href="data/RECITERS.md"><img src="https://img.shields.io/badge/Riwayat-16%20%2F%2020-f0ad4e" alt="Riwayat"></a> -->
  <!-- <a href="data/RECITERS.md"><img src="https://img.shields.io/badge/Segmented-14%20reciters%20%C2%B7%20336h-d4842a" alt="Segmented"></a> -->"""
    if old_block in readme:
        return readme.replace(old_block, badges)

    raise RuntimeError("README badge block not found")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    parser.add_argument("--stats", type=Path, default=DEFAULT_STATS)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--bucket-id", default=PROD_BUCKET_ID)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("HF_TOKEN")
    temp_db: Path | None = None
    try:
        db_path = args.db_path
        if db_path is None:
            temp_db = download_db(args.bucket_id, token=token)
            db_path = temp_db

        def manifest_reader(slug: str) -> dict:
            raw = read_bucket_bytes(
                args.bucket_id, f"catalog/audio_manifest/{slug}.json", token=token
            )
            return json.loads(raw)

        catalog_stats, aligned_stats = collect_stats(db_path, manifest_reader=manifest_reader)
        badges = render_badges(catalog_stats, aligned_stats)
        print(
            f"catalog: reciters={catalog_stats.reciters} riwayat={catalog_stats.riwayat} "
            f"hours={format_hours(catalog_stats.seconds, step=500)}"
        )
        print(
            f"aligned: mushafs={aligned_stats.mushafs} riwayat={aligned_stats.riwayat} "
            f"hours={format_hours(aligned_stats.seconds, step=50)}"
        )
        print(badges)

        if args.dry_run:
            return 0

        readme = args.readme.read_text(encoding="utf-8")
        updated = replace_badges(readme, badges)
        args.readme.write_text(updated, encoding="utf-8")
        args.stats.write_text(render_stats_json(catalog_stats, aligned_stats), encoding="utf-8")
        return 0
    finally:
        if temp_db is not None:
            try:
                temp_db.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
