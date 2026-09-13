"""Release asset schema smoke tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from qua_shared.schemas import (
    DigitalKhattDoc,
    LetterTimestampsDoc,
    ReleaseCatalog,
    ReleaseCatalogAudio,
    ReleaseCoverage,
    ReleaseRecitationCatalog,
    VerseTimestampsDoc,
    WordTimestampsDoc,
)


def _meta(tier: str, layout: str) -> dict:
    return {
        "schema_version": 3,
        "slug": "example_reciter",
        "audio_category": "by_surah",
        "verse_count": 1,
        "occurrence_count": 1,
        "tier": tier,
        "layout": layout,
        "script": "digital_khatt_v2",
        "script_sha256": "0" * 64,
        "unicode_indexing": "scalar",
    }


def test_timestamp_tier_shapes_validate():
    VerseTimestampsDoc.model_validate(
        {"_meta": _meta("verse", "rows"), "rows": [["100:1", 0, 2831, True, 0]]}
    )
    WordTimestampsDoc.model_validate(
        {
            "_meta": _meta("word", "rows"),
            "rows": [["100:1", 0, 2831, True, 0, [[1, 70, 1550], [2, 1550, 2790]]]],
        }
    )
    LetterTimestampsDoc.model_validate(
        {
            "_meta": _meta("letter", "rows"),
            "rows": [
                [
                    "100:1",
                    0,
                    2831,
                    True,
                    0,
                    [[1, 70, 1550]],
                    "xy",
                    [[0, 70, 240, True, [[0, 1]]], [0, 240, 420, False, [[1, 2]]]],
                ]
            ],
        }
    )


def test_public_release_schema_one_is_rejected():
    meta = _meta("verse", "[start,end]")
    meta["schema_version"] = 1
    with pytest.raises(ValidationError):
        VerseTimestampsDoc.model_validate({"_meta": meta, "rows": [["1:1", 0, 1, True, 0]]})


def test_timestamp_tier_rejects_bad_verse_key():
    with pytest.raises(ValidationError):
        VerseTimestampsDoc.model_validate(
            {"_meta": _meta("verse", "rows"), "rows": [["surah:ayah", 0, 1, True, 0]]}
        )


def test_timestamp_tier_allows_repeated_occurrences_with_one_canonical():
    meta = _meta("word", "rows")
    meta["occurrence_count"] = 2
    WordTimestampsDoc.model_validate(
        {
            "_meta": meta,
            "rows": [
                ["1:1", 0, 100, False, 100, [[1, 0, 100]]],
                ["1:1", 200, 400, True, 0, [[1, 200, 400]]],
            ],
        }
    )


def test_timestamp_tier_rejects_two_canonical_occurrences_for_one_ref():
    meta = _meta("verse", "rows")
    meta["occurrence_count"] = 2
    with pytest.raises(ValidationError, match="exactly one canonical"):
        VerseTimestampsDoc.model_validate(
            {
                "_meta": meta,
                "rows": [
                    ["1:1", 0, 100, True, 0],
                    ["1:1", 200, 400, True, 0],
                ],
            }
        )


def test_release_catalog_requires_audio_url_map():
    catalog = ReleaseCatalog(
        recitations=[
            ReleaseRecitationCatalog(
                slug="example_reciter",
                audio=ReleaseCatalogAudio(
                    chapter_urls={"100": "https://cdn.example/100.mp3"},
                    sample_rate_hz=44100,
                    channels=2,
                ),
                coverage=ReleaseCoverage(surahs=1, ayahs=3),
            )
        ]
    )
    assert catalog.recitations[0].audio.chapter_urls["100"].endswith("100.mp3")


def test_chapter_offsets_omitted_from_json_when_empty():
    # CDN by-surah: no offsets → the key must be ABSENT (not ``{}``) so existing
    # catalogs stay byte-stable and their content_hash doesn't churn.
    audio = ReleaseCatalogAudio(chapter_urls={"1": "https://cdn.example/1.mp3"})
    dumped = audio.model_dump(mode="json", by_alias=True)
    assert "chapter_offsets_ms" not in dumped


def test_chapter_offsets_present_for_combined_source():
    audio = ReleaseCatalogAudio(
        chapter_urls={"1": "https://yt.example/v", "2": "https://yt.example/v"},
        chapter_offsets_ms={"2": 215000},
    )
    dumped = audio.model_dump(mode="json", by_alias=True)
    assert dumped["chapter_offsets_ms"] == {"2": 215000}
    # Round-trips back through validation.
    assert ReleaseCatalogAudio.model_validate(dumped).chapter_offsets_ms == {"2": 215000}


def test_legacy_catalog_without_offsets_still_validates():
    # An old release catalog.json (cut before the field existed) must load.
    rec = ReleaseRecitationCatalog.model_validate(
        {
            "slug": "example_reciter",
            "audio": {"chapter_urls": {"1": "https://cdn.example/1.mp3"}},
            "coverage": {"surahs": 1, "ayahs": 3},
        }
    )
    assert rec.audio.chapter_offsets_ms == {}


def test_coverage_missing_keys_omitted_when_complete():
    # A complete recitation: neither missing key in the JSON (byte-stable hash).
    dumped = ReleaseCoverage(surahs=114, ayahs=6236).model_dump(mode="json")
    assert "missing_surahs" not in dumped
    assert "missing_verses" not in dumped


def test_coverage_missing_keys_present_and_round_trip():
    cov = ReleaseCoverage(surahs=30, ayahs=327, missing_surahs="1-84", missing_verses="2:3")
    dumped = cov.model_dump(mode="json")
    assert dumped["missing_surahs"] == "1-84"
    assert dumped["missing_verses"] == "2:3"
    assert ReleaseCoverage.model_validate(dumped).missing_surahs == "1-84"


def test_legacy_coverage_without_missing_keys_validates():
    cov = ReleaseCoverage.model_validate({"surahs": 114, "ayahs": 6236})
    assert cov.missing_surahs == "" and cov.missing_verses == ""


def test_digital_khatt_doc_validates_location_keys():
    doc = DigitalKhattDoc.model_validate(
        {
            "1:1:1": {
                "id": 1,
                "surah": "1",
                "ayah": "1",
                "word": "1",
                "location": "1:1:1",
                "text": "bismi",
            }
        }
    )
    assert doc.root["1:1:1"].location == "1:1:1"

    with pytest.raises(ValidationError):
        DigitalKhattDoc.model_validate(
            {
                "1:1:1": {
                    "surah": "1",
                    "ayah": "1",
                    "word": "1",
                    "location": "1:1:2",
                    "text": "bismi",
                }
            }
        )
