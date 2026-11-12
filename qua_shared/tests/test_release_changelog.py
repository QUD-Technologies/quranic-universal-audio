"""Tests for the shared release-changelog renderer.

``qua_shared/release_changelog.py`` is the single source of truth for the GH
release body, used by both the cut HF Job and the Inspector cut-modal preview.
These guard the format contract: title first, asset table first, display names
only (no slugs), concise guide copy, and two collapsed schema sections.
"""

from __future__ import annotations

from qua_shared.release_changelog import render_changelog


def _member(
    name_en,
    *,
    change_kind="added",
    riwayah="Hafs A'n Assem",
    style="Murattal",
    channel="Tarteel CDN",
    coverage_surahs=114,
    coverage_ayahs=None,
    name_ar="فلان",
    missing_surahs="",
    missing_verses="",
    quality="verified",
    slug=None,
):
    return {
        "slug": slug or name_en.lower().replace(" ", "_"),
        "quality": quality,
        "name_en": name_en,
        "name_ar": name_ar,
        "riwayah": riwayah,
        "style": style,
        "channel": channel,
        "change_kind": change_kind,
        "coverage_surahs": coverage_surahs,
        "coverage_ayahs": coverage_ayahs,
        "missing_surahs": missing_surahs,
        "missing_verses": missing_verses,
    }


def test_first_release_added_only():
    md = render_changelog(
        version="v0.1.0",
        previous_version=None,
        release_date="2026-06-03",
        members=[_member("Abdulbasit Abdulsamad"), _member("Saud Al-Shuraim")],
        owner="QUD-Technologies",
        repo="quranic-universal-audio",
        hf_dataset="QUD-Technologies/quranic-universal-ayahs",
    )
    # Title first, then the asset section. Tolerant of the template's optional
    # standing intro paragraph between them (present on some release bodies) —
    # assert the invariants, not an exact byte prefix.
    assert md.startswith("# 2026-06-03\n")
    assert "## What to download" in md
    assert "This release publishes" not in md
    assert (
        "| `catalog.json` | Release-level catalog: reciter names, riwayah, style, coverage, "
        "quality label, audio metadata, and the audio URLs paired with the timestamp data. |"
    ) in md
    # The release-level files index the whole release; each zip carries its own catalog.
    assert "`specials.json` (its Isti'adha / Basmala)" in md
    assert "each zip also carries its own `catalog.json`" in md
    assert "release_schemas.json" not in md
    assert "First release: **2** recitations." in md
    assert "<details><summary>Verified recitations - 2</summary>" in md
    assert "Abdulbasit Abdulsamad" in md
    # Nothing has known issues, so that table is left out.
    assert "Recitations with known issues" not in md
    assert "carried" not in md
    # The quality note sits right under the license paragraph.
    assert (
        md.index("> **License.**")
        < md.index("> **Recitation quality.**")
        < md.index("## What to download")
    )


def test_display_names_no_slugs():
    md = render_changelog(
        version="v0.1.0",
        previous_version=None,
        release_date="2026-06-03",
        members=[_member("Abdulbasit Abdulsamad")],
    )
    assert "Hafs A'n Assem" in md and "Tarteel CDN" in md
    # The slug forms must never leak into the human-facing body.
    for slug in ("hafs_an_asim", "_tarteel", "murattal"):
        assert slug not in md


def test_tables_split_by_quality_new_first():
    members = [
        _member("Stable Clean", change_kind="unchanged"),
        _member("New Clean", change_kind="added"),
        _member("Stable Flawed", change_kind="unchanged", quality="known_issues"),
        _member("Updated Flawed", change_kind="refresh", quality="known_issues"),
        _member("New Flawed", change_kind="added", quality="known_issues", slug="new_flawed_yt"),
    ]
    md = render_changelog(
        version="v0.2.0",
        previous_version="v0.1.0",
        release_date="2026-07-01",
        members=members,
    )
    assert "Adds 2, refreshes 1 (2 carried) over v0.1.0." in md
    verified, known = md.split("<details><summary>Recitations with known issues - 3</summary>")
    assert "<details><summary>Verified recitations - 2</summary>" in verified
    assert "Issues |" not in verified.split("## Recitations")[1]
    # New first, then updated, then unchanged, within each table.
    assert verified.index("| New Clean - ") < verified.index("| Stable Clean - ")
    assert known.index("| New Flawed - ") < known.index("| Updated Flawed - ")
    assert known.index("| Updated Flawed - ") < known.index("| Stable Flawed - ")
    assert "| Coverage | Missing | Change | Issues |" in known
    assert "| New | [Segments](https://audio.qud.dev/?segments=new_flawed_yt) |" in known


def test_coverage_cell_ayahs_then_surahs():
    md_ayahs = render_changelog(
        version="v1.0.0",
        previous_version=None,
        release_date="d",
        members=[_member("R", coverage_ayahs=6236, coverage_surahs=114)],
    )
    assert "6,236 ayahs" in md_ayahs
    md_surahs = render_changelog(
        version="v1.0.0",
        previous_version=None,
        release_date="d",
        members=[_member("R", coverage_ayahs=None, coverage_surahs=114)],
    )
    assert "114 surahs" in md_surahs and "ayahs" not in md_surahs.split("Reciter zip schemas")[0]


def test_audio_pairing_and_timestamp_layers_are_explained():
    md = render_changelog(
        version="v0.1.0",
        previous_version=None,
        release_date="d",
        members=[_member("R")],
    )
    assert "`catalog.json` contains the audio URLs for each recitation" in md
    assert "every timestamp value is milliseconds relative to that matching source audio." in md
    assert "storage, speed, and network efficiency" in md
    assert "Use `shard.py` when your app prefers per-surah files locally" in md
    assert '["1:1", 70, 2790, true, 41]' in md
    # The worked example spans multiple words; letter tiers are no longer released.
    assert "ٱللَّهِ" in md
    assert "letter_timestamps" not in md and "owns_sound" not in md
    assert "loops back or re-recites" in md


def test_staying_up_to_date_section_and_asset_row():
    md = render_changelog(
        version="v0.3.0",
        previous_version="v0.2.0",
        release_date="d",
        members=[_member("R", change_kind="refresh")],
    )
    assert "## Staying up to date" in md
    assert "Watch -> Custom -> Releases" in md
    assert "python check_updates.py manifest.json --sync" in md
    # asset-table row advertises the helper
    assert "`check_updates.py` |" in md


def test_table_cell_pipe_escaped():
    md = render_changelog(
        version="v0.1.0",
        previous_version=None,
        release_date="d",
        members=[_member("A | B")],
    )
    assert "A \\| B" in md


def test_no_missing_callout_when_all_complete():
    md = render_changelog(
        version="v1.0.0",
        previous_version=None,
        release_date="d",
        members=[_member("R"), _member("S")],
    )
    assert "About missing verses" not in md
    # Column header still present; every cell is an em dash.
    assert "| Coverage | Missing |" in md


def test_schema_sections_are_collapsed():
    md = render_changelog(
        version="v0.1.0",
        previous_version=None,
        release_date="d",
        members=[_member("R")],
    )
    assert "<details><summary>Reciter zip schemas</summary>" in md
    assert "<details><summary>Catalog and manifest schemas</summary>" in md
    assert "type VerseTimestamps" in md
    assert '"tier": "verse"' in md
    assert '"tier": "word"' in md
    assert '"tier": "letter"' not in md
    assert "type ReleaseManifest" in md


def test_member_table_has_no_timings_column_or_proxy_note():
    """Tier depth is a manifest fact (`tiers`), not release-body prose."""
    member = {**_member("R"), "tiers": ["verse", "word"]}
    md = render_changelog(
        version="v1.0.0",
        previous_version=None,
        release_date="d",
        members=[member, _member("S")],
    )
    assert "| Timings |" not in md
    assert "word · verse" not in md
    assert "aligning the audio against Hafs as a proxy" not in md
    assert "[!NOTE]" not in md
