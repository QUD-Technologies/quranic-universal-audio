from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "codegen" / "update_readme_badges.py"
)
SPEC = importlib.util.spec_from_file_location("update_readme_badges", SCRIPT_PATH)
assert SPEC is not None
badges = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = badges
SPEC.loader.exec_module(badges)


def test_cli_help_does_not_import_site_packages():
    result = subprocess.run(
        [sys.executable, "-S", str(SCRIPT_PATH), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout


def test_format_hours_floors_to_50_with_plus():
    assert badges.format_hours(0) == "0h+"
    assert badges.format_hours(49 * 3600) == "0h+"
    assert badges.format_hours(50 * 3600) == "50h+"
    assert badges.format_hours(99 * 3600) == "50h+"
    assert badges.format_hours(1249 * 3600) == "1,200h+"


def test_replace_badges_replaces_legacy_commented_block():
    readme = """<p align="center">
  <!-- <br> -->
  <!-- <a href="data/RECITERS.md"><img src="https://img.shields.io/badge/Unsegmented-832%20reciters%20%C2%B7%2014,320h-d4842a" alt="Unsegmented"></a> -->
  <!-- <a href="data/RECITERS.md"><img src="https://img.shields.io/badge/Riwayat-16%20%2F%2020-f0ad4e" alt="Riwayat"></a> -->
  <!-- <a href="data/RECITERS.md"><img src="https://img.shields.io/badge/Segmented-14%20reciters%20%C2%B7%20336h-d4842a" alt="Segmented"></a> -->
  <br>
</p>
"""
    catalog = badges.BadgeStats(reciters=12, mushafs=20, riwayat=7, seconds=750 * 3600)
    aligned = badges.BadgeStats(reciters=5, mushafs=14, riwayat=3, seconds=100 * 3600)

    rendered = badges.render_badges(catalog, aligned)

    updated = badges.replace_badges(readme, rendered)

    assert "<!-- stats-badges:start -->" in updated
    assert "Reciters-12" in updated
    assert "Riwayat-7" in updated
    assert "Mushafs-14" in updated
    assert "Riwayat-3" in updated
    assert "Hours-500h%2B" in updated
    assert "Hours-100h%2B" in updated
    assert "Unsegmented" not in updated


def test_replace_badges_updates_existing_marked_block():
    old = "\n".join(
        [
            "before",
            badges.START_MARKER,
            "  stale",
            badges.END_MARKER,
            "after",
        ]
    )
    catalog = badges.BadgeStats(reciters=1, mushafs=2, riwayat=2, seconds=50 * 3600)
    aligned = badges.BadgeStats(reciters=1, mushafs=1, riwayat=1, seconds=50 * 3600)
    new_block = badges.render_badges(catalog, aligned)

    updated = badges.replace_badges(old, new_block)

    assert "stale" not in updated
    assert updated.count(badges.START_MARKER) == 1
    assert "Reciters-1" in updated


def test_sources_only_manifest_counts_zero_seconds():
    # Online-intake "detect" group: playlist sources, surahs not split out yet.
    doc = {"chapters": {}, "sources": [{"url": "https://example.com/1"}]}

    assert badges._duration_from_manifest(doc, "pending_yt") == 0


def test_manifest_without_chapters_or_sources_raises():
    with pytest.raises(RuntimeError, match="no chapters or sources"):
        badges._duration_from_manifest({"chapters": {}, "sources": []}, "broken")


def test_collect_stats_filters_public_released_and_uses_manifest_fallback(tmp_path):
    db_path = tmp_path / "inspector.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE deliveries (
                slug TEXT PRIMARY KEY,
                reciter_id TEXT NOT NULL,
                riwayah TEXT NOT NULL,
                total_duration_sec INTEGER
            );
            CREATE TABLE delivery_states (
                slug TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                visibility TEXT NOT NULL
            );
            INSERT INTO deliveries VALUES
                ('pub_a', 'r1', 'hafs_an_asim', 3600),
                ('pub_b', 'r2', 'warsh_an_nafi', NULL),
                ('wip', 'r1', 'hafs_an_asim', 7200),
                ('discarded', 'r3', 'qalun_an_nafi', 7200);
            INSERT INTO delivery_states VALUES
                ('pub_a', 'released', 'public'),
                ('pub_b', 'released', 'public'),
                ('wip', 'under_review', 'public'),
                ('discarded', 'released', 'discarded');
            """
        )
        conn.commit()
    finally:
        conn.close()

    def manifest_reader(slug: str) -> dict:
        assert slug == "pub_b"
        return {
            "chapters": {
                "1": {"duration_sec": 10},
                "2": {"duration_sec": 20.8},
            }
        }

    catalog_stats, aligned_stats = badges.collect_stats(db_path, manifest_reader=manifest_reader)

    # Catalog counts every sourced delivery — all 4 rows, 3 distinct reciters
    # (r1 appears twice via pub_a/wip), 3 distinct riwayat.
    assert catalog_stats.reciters == 3
    assert catalog_stats.mushafs == 4
    assert catalog_stats.riwayat == 3
    assert catalog_stats.seconds == 18030

    # Aligned counts only the released/public set (pub_a + pub_b), NOT the wip
    # or discarded rows — consistent with the published dataset.
    assert aligned_stats.reciters == 2
    assert aligned_stats.mushafs == 2
    assert aligned_stats.riwayat == 2
    assert aligned_stats.seconds == 3630
